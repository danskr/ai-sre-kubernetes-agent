import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from app import db
from app.config import settings

logger = logging.getLogger(__name__)


def _wait_for_agent_server() -> None:
    last_error: Exception | None = None
    for attempt in range(1, settings.agent_server_connect_attempts + 1):
        try:
            response = httpx.get(
                f"{settings.agent_server_url}/ok",
                timeout=3.0,
            )
            response.raise_for_status()
            return
        except Exception as exc:
            last_error = exc
            if attempt < settings.agent_server_connect_attempts:
                time.sleep(settings.agent_server_connect_retry_seconds)
    raise RuntimeError(f"Agent Server is unavailable: {last_error}") from last_error


def run_incident_workflow(
    incident_id: str,
    incident_kind: str,
    *,
    consecutive_probe_failures: int | None = None,
) -> dict[str, Any]:
    """Submit one operational incident to the same Agent Server graph Studio uses.

    The connection check can be retried safely before a run exists. Once the run is
    submitted, it is never automatically re-submitted on transport failure because
    operational nodes may have side effects. That preserves at-most-once submission
    from the observer.

    /runs/wait returns when the run ends OR when a dynamic interrupt pauses it.
    Scenario 3 therefore returns to the observer while Agent Server keeps the thread
    checkpointed and resumable from Studio.
    """
    _wait_for_agent_server()

    with httpx.Client(timeout=settings.agent_server_run_timeout_seconds) as client:
        response = client.post(f"{settings.agent_server_url}/threads", json={})
        response.raise_for_status()
        body = response.json()
        thread_id = body.get("thread_id")
        if not thread_id:
            raise RuntimeError(f"Agent Server did not return thread_id: {body}")
        thread_id = str(thread_id)

        workflow_meta = {
            "assistant_id": settings.agent_server_assistant_id,
            "thread_id": thread_id,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "status": "submitted",
        }
        db.update_incident(incident_id, details={"workflow": workflow_meta})

        graph_input: dict[str, Any] = {
            "request_type": "incident",
            "incident_id": incident_id,
            "incident_kind": incident_kind,
        }
        if consecutive_probe_failures is not None:
            graph_input["consecutive_probe_failures"] = consecutive_probe_failures

        try:
            response = client.post(
                f"{settings.agent_server_url}/threads/{thread_id}/runs/wait",
                json={
                    "assistant_id": settings.agent_server_assistant_id,
                    "input": graph_input,
                },
            )
            response.raise_for_status()
        except Exception as exc:
            db.update_incident(
                incident_id,
                details={
                    "workflow": {
                        **workflow_meta,
                        "status": "submission_uncertain",
                        "error": str(exc),
                        "failed_at": datetime.now(timezone.utc).isoformat(),
                    }
                },
            )
            raise RuntimeError(
                "Agent Server run submission failed or its completion status is uncertain; "
                "the observer will not retry automatically to avoid duplicate operational actions."
            ) from exc

        result = response.json()
        interrupted = bool(result.get("__interrupt__"))
        db.update_incident(
            incident_id,
            details={
                "workflow": {
                    **workflow_meta,
                    "status": "interrupted" if interrupted else "completed",
                    "returned_at": datetime.now(timezone.utc).isoformat(),
                    "interrupt": result.get("__interrupt__") if interrupted else None,
                }
            },
        )
        logger.info(
            "Unified workflow returned incident=%s kind=%s thread=%s interrupted=%s",
            incident_id,
            incident_kind,
            thread_id,
            interrupted,
        )
        return result
