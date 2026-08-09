import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from . import db
from .config import settings
from .k8s_client import (
    current_app_logs,
    current_cluster_state,
    deployment_history,
    deployment_state,
    dependency_state,
    probe_application,
    rollback_to_previous_revision,
)

logger = logging.getLogger(__name__)


class RegressionDiagnosis(BaseModel):
    classification: Literal[
        "deployment_regression",
        "kubernetes_transient",
        "database_or_dependency",
        "unknown",
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    recommended_action: Literal["rollback", "observe", "human_review"]
    summary: str
    evidence: list[str]


class RemediationState(TypedDict, total=False):
    incident_id: str
    evidence: dict[str, Any]
    diagnosis: dict[str, Any]
    policy: dict[str, Any]
    rollback: dict[str, Any]
    verification: dict[str, Any]


DIAGNOSIS_PROMPT = """You are diagnosing a Kubernetes application regression for an automatic-remediation workflow.

Evidence boundary:
- Use only bulletin-board application logs, Kubernetes state/events, PostgreSQL Kubernetes-level state, and sre-agent's persisted probe/deployment history.
- You have no visibility into user-agent or any traffic generator. Never infer facts from it.

The only automatic action is rollback of the bulletin-board Deployment to the complete previous Pod template.
Recommend deployment_regression + rollback only when the evidence strongly supports the current release as the cause.

Important causal rules:
- A latent release regression may pass startup/readiness and remain healthy for minutes before normal runtime behavior exposes it. An initial healthy period does NOT exonerate the current release.
- Strong release-regression evidence includes: the previous revision was persistently healthy immediately before rollout; its revision-scoped readiness probes were also healthy; current and previous Pod templates differ; the new revision later develops an application-internal failure mode; and the PostgreSQL pod itself remains Running/Ready without restart evidence.
- SQLAlchemy connection-pool exhaustion can be an application release defect (for example leaked sessions or changed pool configuration). Do NOT automatically classify it as an external database dependency problem merely because the exception mentions the DB pool.
- Prefer database_or_dependency when there is independent evidence that PostgreSQL itself is unhealthy/unavailable, or when the same failure clearly predates the release.
- If the persisted baseline is insufficient or evidence conflicts, choose human_review.

Use concrete timestamps, revision/template changes, pre-release baseline, time-to-first-failure, readiness/availability, dependency health, and logs.
"""


model = ChatOpenAI(
    model=settings.openai_model,
    api_key=settings.openai_api_key,
    temperature=0,
    use_responses_api=True,
    output_version="responses/v1",
    reasoning={"effort": "medium", "summary": "auto"},
)
structured_model = model.with_structured_output(RegressionDiagnosis)


def _diagnose(state: RemediationState) -> dict[str, Any]:
    evidence = state["evidence"]
    response = structured_model.invoke(
        [
            SystemMessage(content=DIAGNOSIS_PROMPT),
            HumanMessage(content=json.dumps(evidence, default=str, indent=2)),
        ]
    )
    diagnosis = response.model_dump()
    logger.info(
        "Auto-remediation diagnosis incident=%s classification=%s confidence=%.3f action=%s",
        state["incident_id"],
        diagnosis["classification"],
        diagnosis["confidence"],
        diagnosis["recommended_action"],
    )
    return {"diagnosis": diagnosis}


def _policy(state: RemediationState) -> dict[str, Any]:
    evidence = state["evidence"]
    diagnosis = state["diagnosis"]
    history = evidence.get("deployment_history", [])

    checks = {
        "auto_remediation_enabled": settings.auto_remediation_enabled,
        "deployment_is_allowlisted": evidence.get("deployment_name") == settings.app_deployment_name,
        "enough_consecutive_failures": evidence.get("consecutive_probe_failures", 0)
        >= settings.auto_remediation_failure_threshold,
        "previous_revision_available": len(history) >= 2,
        "current_and_previous_images_differ": len(history) >= 2
        and history[0].get("images") != history[1].get("images"),
        "diagnosis_is_deployment_regression": diagnosis.get("classification") == "deployment_regression",
        "diagnosis_recommends_rollback": diagnosis.get("recommended_action") == "rollback",
        "confidence_meets_threshold": float(diagnosis.get("confidence", 0.0))
        >= settings.auto_remediation_confidence_threshold,
        "release_is_recent": bool(evidence.get("release_is_recent")),
        "previous_revision_baseline_healthy": bool(
            evidence.get("previous_revision_baseline", {}).get("was_healthy")
        ),
        "postgresql_kubernetes_state_healthy": bool(
            evidence.get("dependency_state", {}).get("healthy")
        ),
    }
    allowed = all(checks.values())
    return {
        "policy": {
            "allowed": allowed,
            "checks": checks,
            "confidence_threshold": settings.auto_remediation_confidence_threshold,
        }
    }


def _route_after_policy(state: RemediationState) -> Literal["rollback", "no_action"]:
    return "rollback" if state["policy"]["allowed"] else "no_action"


def _rollback(state: RemediationState) -> dict[str, Any]:
    incident_id = state["incident_id"]
    diagnosis = state["diagnosis"]
    action_id = str(uuid.uuid4())
    history = state["evidence"]["deployment_history"]
    current_revision = history[0].get("revision") if history else None
    target_revision = history[1].get("revision") if len(history) > 1 else None

    db.create_remediation_action(
        {
            "action_id": action_id,
            "incident_id": incident_id,
            "action": "rollback_deployment_template",
            "status": "running",
            "started_at": datetime.now(timezone.utc),
            "from_revision": current_revision,
            "target_revision": target_revision,
            "details": {
                "diagnosis": diagnosis,
                "policy": state["policy"],
            },
        }
    )
    db.update_incident(
        incident_id,
        status="remediating",
        summary="Automatic rollback approved by deterministic safety policy.",
        details={"diagnosis": diagnosis, "policy": state["policy"], "action_id": action_id},
    )

    try:
        result = rollback_to_previous_revision(
            reason=diagnosis["summary"],
            incident_id=incident_id,
        )
        result["action_id"] = action_id
        return {"rollback": result}
    except Exception as exc:
        db.finish_remediation_action(action_id, "failed", {"error": str(exc)})
        raise


def _verify(state: RemediationState) -> dict[str, Any]:
    rollback = state["rollback"]
    action_id = rollback["action_id"]
    target_images = rollback["target_images"]
    deadline = time.monotonic() + settings.rollback_verify_timeout_seconds
    consecutive_successes = 0
    observations: list[dict[str, Any]] = []

    while time.monotonic() < deadline:
        dep = deployment_state()
        probe = probe_application(timeout_seconds=3.0)
        image_restored = dep.get("images") == target_images
        template_restored = dep.get("template_fingerprint") == rollback.get("target_template_fingerprint")
        replicas_ready = dep.get("desired_replicas", 0) > 0 and dep.get("ready_replicas", 0) >= dep.get("desired_replicas", 0)
        success = image_restored and template_restored and replicas_ready and probe["ok"]
        observations.append(
            {
                "time": datetime.now(timezone.utc).isoformat(),
                "deployment": dep,
                "probe": probe,
                "success": success,
            }
        )
        if success:
            consecutive_successes += 1
            if consecutive_successes >= settings.rollback_verify_successes:
                result = {
                    "verified": True,
                    "consecutive_successes": consecutive_successes,
                    "final_deployment": dep,
                    "final_probe": probe,
                    "observations": observations[-10:],
                }
                db.finish_remediation_action(action_id, "succeeded", result)
                db.update_incident(
                    state["incident_id"],
                    status="resolved",
                    summary="sre-agent automatically rolled back the degraded release and verified recovery.",
                    details={"rollback": rollback, "verification": result},
                    end=True,
                )
                return {"verification": result}
        else:
            consecutive_successes = 0
        time.sleep(2)

    result = {
        "verified": False,
        "consecutive_successes": consecutive_successes,
        "observations": observations[-10:],
    }
    db.finish_remediation_action(action_id, "verification_failed", result)
    db.update_incident(
        state["incident_id"],
        status="needs_human_review",
        summary="Automatic rollback was issued but recovery could not be verified within the timeout.",
        details={"rollback": rollback, "verification": result},
    )
    return {"verification": result}


def _no_action(state: RemediationState) -> dict[str, Any]:
    diagnosis = state["diagnosis"]
    policy = state["policy"]
    db.update_incident(
        state["incident_id"],
        status="needs_human_review",
        summary="Automatic rollback was not permitted by the deterministic safety policy.",
        details={"diagnosis": diagnosis, "policy": policy},
    )
    return {}



# Public node aliases used by the unified sre_agent graph.
diagnose_regression = _diagnose
evaluate_rollback_policy = _policy
route_after_rollback_policy = _route_after_policy
execute_rollback = _rollback
verify_rollback = _verify
regression_no_action = _no_action


def collect_regression_evidence(consecutive_probe_failures: int) -> dict[str, Any]:
    history = deployment_history()
    current = history[0] if history else None
    previous = history[1] if len(history) > 1 else None
    now = datetime.now(timezone.utc)
    release_age_seconds = None
    release_is_recent = False
    release_created_at = None
    if current and current.get("revision") is not None:
        release_created_at = db.get_revision_first_observed_at(int(current["revision"]))
    if release_created_at is None and current:
        release_created_at = current.get("created_at")
    if release_created_at:
        release_age_seconds = max(0.0, (now - release_created_at).total_seconds())
        release_is_recent = release_age_seconds <= settings.auto_remediation_recent_deployment_seconds

    current_release_health = {}
    previous_revision_baseline = {}
    if release_created_at:
        current_release_health = db.get_health_window_summary(release_created_at, now + timedelta(seconds=1))
        if previous and previous.get("revision") is not None:
            previous_revision_baseline = db.get_revision_baseline(
                int(previous["revision"]),
                release_created_at,
                lookback_seconds=settings.auto_remediation_recent_deployment_seconds,
            )

    first_failure_at = current_release_health.get("first_failure_at") if current_release_health else None
    time_to_first_failure_seconds = None
    if release_created_at and first_failure_at:
        time_to_first_failure_seconds = max(0.0, (first_failure_at - release_created_at).total_seconds())

    return {
        "collected_at": now,
        "deployment_name": settings.app_deployment_name,
        "namespace": settings.app_namespace,
        "consecutive_probe_failures": consecutive_probe_failures,
        "release_age_seconds": release_age_seconds,
        "release_is_recent": release_is_recent,
        "cluster_state": current_cluster_state(),
        "dependency_state": dependency_state(),
        "deployment_history": history[:5],
        "previous_revision_baseline": previous_revision_baseline,
        "current_release_health": current_release_health,
        "time_to_first_failure_seconds": time_to_first_failure_seconds,
        "recent_health_samples": db.get_recent_health(12),
        "recent_kubernetes_events": db.get_events(1)[:80],
        "application_logs": current_app_logs(200),
    }
