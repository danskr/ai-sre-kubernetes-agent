import json
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .config import settings


DDL = """
CREATE TABLE IF NOT EXISTS sre_k8s_events (
    event_uid TEXT PRIMARY KEY,
    first_seen TIMESTAMPTZ NOT NULL,
    last_seen TIMESTAMPTZ NOT NULL,
    event_type TEXT,
    reason TEXT,
    object_kind TEXT,
    object_name TEXT,
    message TEXT,
    event_count INTEGER,
    raw JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS sre_pod_snapshots (
    id BIGSERIAL PRIMARY KEY,
    observed_at TIMESTAMPTZ NOT NULL,
    pod_name TEXT NOT NULL,
    pod_uid TEXT NOT NULL,
    phase TEXT,
    ready BOOLEAN NOT NULL,
    restart_count INTEGER NOT NULL,
    node_name TEXT
);
CREATE INDEX IF NOT EXISTS idx_sre_pod_snapshots_time
    ON sre_pod_snapshots(observed_at DESC);
ALTER TABLE sre_pod_snapshots
    ADD COLUMN IF NOT EXISTS container_statuses JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE sre_pod_snapshots
    ADD COLUMN IF NOT EXISTS last_termination_reason TEXT;
ALTER TABLE sre_pod_snapshots
    ADD COLUMN IF NOT EXISTS last_exit_code INTEGER;
ALTER TABLE sre_pod_snapshots
    ADD COLUMN IF NOT EXISTS last_finished_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS sre_health_samples (
    id BIGSERIAL PRIMARY KEY,
    observed_at TIMESTAMPTZ NOT NULL,
    ok BOOLEAN NOT NULL,
    status_code INTEGER,
    latency_ms DOUBLE PRECISION,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_sre_health_samples_time
    ON sre_health_samples(observed_at DESC);

CREATE TABLE IF NOT EXISTS sre_incidents (
    incident_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    summary TEXT NOT NULL,
    details JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sre_incidents_started
    ON sre_incidents(started_at DESC);

CREATE TABLE IF NOT EXISTS sre_deployment_snapshots (
    id BIGSERIAL PRIMARY KEY,
    observed_at TIMESTAMPTZ NOT NULL,
    generation BIGINT,
    revision INTEGER,
    desired_replicas INTEGER NOT NULL,
    ready_replicas INTEGER NOT NULL,
    available_replicas INTEGER NOT NULL,
    updated_replicas INTEGER NOT NULL,
    images JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sre_deployment_snapshots_time
    ON sre_deployment_snapshots(observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_sre_deployment_snapshots_revision_time
    ON sre_deployment_snapshots(revision, observed_at DESC);

CREATE TABLE IF NOT EXISTS sre_remediation_actions (
    action_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    from_revision INTEGER,
    target_revision INTEGER,
    details JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sre_remediation_actions_started
    ON sre_remediation_actions(started_at DESC);

CREATE TABLE IF NOT EXISTS sre_approval_decisions (
    decision_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    action TEXT,
    decision TEXT NOT NULL,
    decided_at TIMESTAMPTZ NOT NULL,
    details JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sre_approval_decisions_time
    ON sre_approval_decisions(decided_at DESC);
"""


def _conn():
    return psycopg.connect(settings.database_url, row_factory=dict_row)


def init_db() -> None:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()


def upsert_k8s_event(event: dict[str, Any]) -> None:
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO sre_k8s_events(
                event_uid, first_seen, last_seen, event_type, reason,
                object_kind, object_name, message, event_count, raw
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
            ON CONFLICT(event_uid) DO UPDATE SET
                last_seen = EXCLUDED.last_seen,
                event_type = EXCLUDED.event_type,
                reason = EXCLUDED.reason,
                message = EXCLUDED.message,
                event_count = EXCLUDED.event_count,
                raw = EXCLUDED.raw
            """,
            (
                event["event_uid"], event["first_seen"], event["last_seen"],
                event.get("event_type"), event.get("reason"),
                event.get("object_kind"), event.get("object_name"),
                event.get("message"), event.get("event_count", 1),
                json.dumps(event.get("raw", {}), default=str),
            ),
        )
        conn.commit()


def insert_pod_snapshot(snapshot: dict[str, Any]) -> None:
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO sre_pod_snapshots(
                observed_at, pod_name, pod_uid, phase, ready,
                restart_count, node_name, container_statuses,
                last_termination_reason, last_exit_code, last_finished_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
            """,
            (
                snapshot["observed_at"], snapshot["pod_name"], snapshot["pod_uid"],
                snapshot.get("phase"), snapshot["ready"],
                snapshot.get("restart_count", 0), snapshot.get("node_name"),
                json.dumps(snapshot.get("container_statuses", []), default=str),
                snapshot.get("last_termination_reason"), snapshot.get("last_exit_code"),
                snapshot.get("last_finished_at"),
            ),
        )
        conn.commit()


def insert_health_sample(sample: dict[str, Any]) -> None:
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO sre_health_samples(
                observed_at, ok, status_code, latency_ms, error
            ) VALUES (%s,%s,%s,%s,%s)
            """,
            (
                sample["observed_at"], sample["ok"], sample.get("status_code"),
                sample.get("latency_ms"), sample.get("error"),
            ),
        )
        conn.commit()


def insert_deployment_snapshot(snapshot: dict[str, Any]) -> None:
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO sre_deployment_snapshots(
                observed_at, generation, revision, desired_replicas,
                ready_replicas, available_replicas, updated_replicas, images
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
            """,
            (
                snapshot["observed_at"], snapshot.get("generation"), snapshot.get("revision"),
                snapshot.get("desired_replicas", 0), snapshot.get("ready_replicas", 0),
                snapshot.get("available_replicas", 0), snapshot.get("updated_replicas", 0),
                json.dumps(snapshot.get("images", {}), default=str),
            ),
        )
        conn.commit()


def create_incident(incident: dict[str, Any]) -> None:
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO sre_incidents(
                incident_id, kind, status, started_at, summary, details
            ) VALUES (%s,%s,%s,%s,%s,%s::jsonb)
            ON CONFLICT(incident_id) DO NOTHING
            """,
            (
                incident["incident_id"], incident["kind"], incident["status"],
                incident["started_at"], incident["summary"],
                json.dumps(incident.get("details", {}), default=str),
            ),
        )
        conn.commit()


def update_incident(
    incident_id: str,
    *,
    status: str | None = None,
    summary: str | None = None,
    details: dict[str, Any] | None = None,
    end: bool = False,
) -> None:
    updates: list[str] = []
    values: list[Any] = []
    if status is not None:
        updates.append("status=%s")
        values.append(status)
    if summary is not None:
        updates.append("summary=%s")
        values.append(summary)
    if details:
        updates.append("details = details || %s::jsonb")
        values.append(json.dumps(details, default=str))
    if end:
        updates.append("ended_at=%s")
        values.append(datetime.now(timezone.utc))
    if not updates:
        return
    values.append(incident_id)
    with _conn() as conn:
        conn.execute(
            f"UPDATE sre_incidents SET {', '.join(updates)} WHERE incident_id=%s",
            tuple(values),
        )
        conn.commit()


def resolve_open_pod_incidents(summary: str, details: dict[str, Any]) -> int:
    with _conn() as conn:
        cur = conn.execute(
            """
            UPDATE sre_incidents
            SET status='resolved', ended_at=%s, summary=%s, details = details || %s::jsonb
            WHERE status='open' AND kind='pod_disappearance'
            """,
            (datetime.now(timezone.utc), summary, json.dumps(details, default=str)),
        )
        conn.commit()
        return cur.rowcount


def has_open_incident(kind: str) -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM sre_incidents WHERE status IN ('open','diagnosing','remediating','needs_human_review','awaiting_approval') AND kind=%s LIMIT 1",
            (kind,),
        ).fetchone()
        return row is not None


def create_remediation_action(action: dict[str, Any]) -> None:
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO sre_remediation_actions(
                action_id, incident_id, action, status, started_at,
                from_revision, target_revision, details
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
            """,
            (
                action["action_id"], action["incident_id"], action["action"],
                action["status"], action["started_at"], action.get("from_revision"),
                action.get("target_revision"), json.dumps(action.get("details", {}), default=str),
            ),
        )
        conn.commit()


def finish_remediation_action(action_id: str, status: str, details: dict[str, Any]) -> None:
    with _conn() as conn:
        conn.execute(
            """
            UPDATE sre_remediation_actions
            SET status=%s, ended_at=%s, details = details || %s::jsonb
            WHERE action_id=%s
            """,
            (status, datetime.now(timezone.utc), json.dumps(details, default=str), action_id),
        )
        conn.commit()


def _since(hours: int) -> datetime:
    hours = max(1, min(hours, 168))
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def get_incidents(hours: int = 12) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT incident_id, kind, status, started_at, ended_at, summary, details
            FROM sre_incidents
            WHERE started_at >= %s
            ORDER BY started_at DESC
            LIMIT 200
            """,
            (_since(hours),),
        ).fetchall()
        return [dict(r) for r in rows]


def get_events(hours: int = 12) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT first_seen, last_seen, event_type, reason, object_kind,
                   object_name, message, event_count
            FROM sre_k8s_events
            WHERE last_seen >= %s
            ORDER BY last_seen DESC
            LIMIT 300
            """,
            (_since(hours),),
        ).fetchall()
        return [dict(r) for r in rows]


def get_health(hours: int = 12) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT observed_at, ok, status_code, latency_ms, error
            FROM sre_health_samples
            WHERE observed_at >= %s
            ORDER BY observed_at DESC
            LIMIT 500
            """,
            (_since(hours),),
        ).fetchall()
        return [dict(r) for r in rows]


def get_recent_health(limit: int = 12) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 100))
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT observed_at, ok, status_code, latency_ms, error
            FROM sre_health_samples
            ORDER BY observed_at DESC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_health_summary(hours: int = 12) -> dict[str, Any]:
    since = _since(hours)
    with _conn() as conn:
        summary = conn.execute(
            """
            SELECT
                COUNT(*) AS total_samples,
                COUNT(*) FILTER (WHERE NOT ok) AS failed_samples,
                AVG(latency_ms) FILTER (WHERE latency_ms IS NOT NULL) AS avg_latency_ms,
                MAX(latency_ms) FILTER (WHERE latency_ms IS NOT NULL) AS max_latency_ms,
                MIN(observed_at) AS first_sample,
                MAX(observed_at) AS last_sample
            FROM sre_health_samples
            WHERE observed_at >= %s
            """,
            (since,),
        ).fetchone()
        failures = conn.execute(
            """
            SELECT observed_at, status_code, latency_ms, error
            FROM sre_health_samples
            WHERE observed_at >= %s AND NOT ok
            ORDER BY observed_at DESC
            LIMIT 50
            """,
            (since,),
        ).fetchall()

    total = int(summary["total_samples"] or 0)
    failed = int(summary["failed_samples"] or 0)
    success_rate = ((total - failed) / total * 100.0) if total else None
    return {
        "hours": hours,
        "total_samples": total,
        "failed_samples": failed,
        "success_rate_percent": round(success_rate, 3) if success_rate is not None else None,
        "avg_latency_ms": round(float(summary["avg_latency_ms"]), 2) if summary["avg_latency_ms"] is not None else None,
        "max_latency_ms": round(float(summary["max_latency_ms"]), 2) if summary["max_latency_ms"] is not None else None,
        "first_sample": summary["first_sample"],
        "last_sample": summary["last_sample"],
        "recent_failures": [dict(r) for r in failures],
    }


def get_deployment_snapshots(hours: int = 12) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT observed_at, generation, revision, desired_replicas, ready_replicas,
                   available_replicas, updated_replicas, images
            FROM sre_deployment_snapshots
            WHERE observed_at >= %s
            ORDER BY observed_at DESC
            LIMIT 500
            """,
            (_since(hours),),
        ).fetchall()
        return [dict(r) for r in rows]



def get_health_window_summary(start: datetime, end: datetime) -> dict[str, Any]:
    """Summarize probe health for an explicit time window."""
    with _conn() as conn:
        summary = conn.execute(
            """
            SELECT
                COUNT(*) AS total_samples,
                COUNT(*) FILTER (WHERE ok) AS successful_samples,
                COUNT(*) FILTER (WHERE NOT ok) AS failed_samples,
                AVG(latency_ms) FILTER (WHERE latency_ms IS NOT NULL) AS avg_latency_ms,
                MAX(latency_ms) FILTER (WHERE latency_ms IS NOT NULL) AS max_latency_ms,
                MIN(observed_at) AS first_sample,
                MAX(observed_at) AS last_sample,
                MIN(observed_at) FILTER (WHERE NOT ok) AS first_failure_at
            FROM sre_health_samples
            WHERE observed_at >= %s AND observed_at < %s
            """,
            (start, end),
        ).fetchone()
    total = int(summary["total_samples"] or 0)
    success = int(summary["successful_samples"] or 0)
    failed = int(summary["failed_samples"] or 0)
    return {
        "start": start,
        "end": end,
        "total_samples": total,
        "successful_samples": success,
        "failed_samples": failed,
        "success_rate_percent": round(success / total * 100.0, 3) if total else None,
        "avg_latency_ms": round(float(summary["avg_latency_ms"]), 2) if summary["avg_latency_ms"] is not None else None,
        "max_latency_ms": round(float(summary["max_latency_ms"]), 2) if summary["max_latency_ms"] is not None else None,
        "first_sample": summary["first_sample"],
        "last_sample": summary["last_sample"],
        "first_failure_at": summary["first_failure_at"],
    }


def get_revision_first_observed_at(revision: int) -> datetime | None:
    """Return when sre-agent first observed a Deployment revision.

    Deployment/ReplicaSet creation timestamps are not sufficient for rollback scenarios
    because Kubernetes may reuse an existing ReplicaSet and assign it a newer revision.
    The persisted observer history is therefore the source of truth for when the
    revision became active from sre-agent's perspective.
    """
    with _conn() as conn:
        row = conn.execute(
            """
            SELECT MIN(observed_at) AS first_observed_at
            FROM sre_deployment_snapshots
            WHERE revision=%s
            """,
            (revision,),
        ).fetchone()
    return row["first_observed_at"] if row else None


def get_revision_baseline(revision: int, before: datetime, lookback_seconds: int = 600) -> dict[str, Any]:
    """Return health evidence scoped strictly to one Deployment revision.

    Only snapshots whose persisted revision equals ``revision`` are selected. Probe
    health is then summarized from the first such snapshot through ``before``. This
    avoids contaminating the previous revision's baseline with failures from an older
    revision that happened to fall inside a generic wall-clock lookback window.
    """
    floor = before - timedelta(seconds=max(30, lookback_seconds))
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT observed_at, generation, revision, desired_replicas, ready_replicas,
                   available_replicas, updated_replicas, images
            FROM sre_deployment_snapshots
            WHERE revision=%s AND observed_at >= %s AND observed_at < %s
            ORDER BY observed_at ASC
            LIMIT 240
            """,
            (revision, floor, before),
        ).fetchall()

    snapshots = [dict(r) for r in rows]
    healthy = [
        r for r in snapshots
        if int(r.get("desired_replicas") or 0) > 0
        and int(r.get("ready_replicas") or 0) >= int(r.get("desired_replicas") or 0)
        and int(r.get("available_replicas") or 0) >= int(r.get("desired_replicas") or 0)
    ]
    snapshot_count = len(snapshots)
    healthy_count = len(healthy)
    snapshot_health_percent = (healthy_count / snapshot_count * 100.0) if snapshot_count else None

    actual_start = snapshots[0]["observed_at"] if snapshots else None
    last_snapshot = snapshots[-1] if snapshots else None
    probe_health = (
        get_health_window_summary(actual_start, before)
        if actual_start is not None
        else {
            "start": None,
            "end": before,
            "total_samples": 0,
            "successful_samples": 0,
            "failed_samples": 0,
            "success_rate_percent": None,
            "avg_latency_ms": None,
            "max_latency_ms": None,
            "first_sample": None,
            "last_sample": None,
            "first_failure_at": None,
        }
    )

    max_tail_gap = max(15, settings.observe_interval_seconds * 3)
    tail_gap_seconds = (
        max(0.0, (before - last_snapshot["observed_at"]).total_seconds())
        if last_snapshot is not None
        else None
    )
    last_snapshot_healthy = bool(
        last_snapshot
        and int(last_snapshot.get("desired_replicas") or 0) > 0
        and int(last_snapshot.get("ready_replicas") or 0) >= int(last_snapshot.get("desired_replicas") or 0)
        and int(last_snapshot.get("available_replicas") or 0) >= int(last_snapshot.get("desired_replicas") or 0)
    )
    sufficient_evidence = bool(
        snapshot_count >= 3
        and int(probe_health.get("total_samples") or 0) >= 3
        and tail_gap_seconds is not None
        and tail_gap_seconds <= max_tail_gap
    )
    was_healthy = bool(
        sufficient_evidence
        and last_snapshot_healthy
        and (snapshot_health_percent or 0) >= 95.0
        and (probe_health.get("success_rate_percent") or 0) >= 95.0
    )

    return {
        "revision": revision,
        "window_start": actual_start,
        "window_end": before,
        "snapshot_count": snapshot_count,
        "healthy_snapshot_count": healthy_count,
        "snapshot_health_percent": round(snapshot_health_percent, 3) if snapshot_health_percent is not None else None,
        "probe_health": probe_health,
        "last_snapshot": last_snapshot,
        "last_snapshot_healthy": last_snapshot_healthy,
        "tail_gap_seconds": round(tail_gap_seconds, 3) if tail_gap_seconds is not None else None,
        "sufficient_evidence": sufficient_evidence,
        "was_healthy": was_healthy,
    }


def has_active_regression_incident(revision: int | None) -> bool:
    if revision is None:
        return has_open_incident("runtime_regression")
    with _conn() as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM sre_incidents
            WHERE kind='runtime_regression'
              AND status IN ('open','diagnosing','remediating','needs_human_review')
              AND COALESCE(details #>> '{deployment_state_at_detection,revision}', '') = %s
            LIMIT 1
            """,
            (str(revision),),
        ).fetchone()
        return row is not None

def get_remediations(hours: int = 12) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT action_id, incident_id, action, status, started_at, ended_at,
                   from_revision, target_revision, details
            FROM sre_remediation_actions
            WHERE started_at >= %s
            ORDER BY started_at DESC
            LIMIT 100
            """,
            (_since(hours),),
        ).fetchall()
        return [dict(r) for r in rows]


def get_incident(incident_id: str) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute(
            """
            SELECT incident_id, kind, status, started_at, ended_at, summary, details
            FROM sre_incidents
            WHERE incident_id=%s
            """,
            (incident_id,),
        ).fetchone()
        return dict(row) if row else None


def get_active_incident(kind: str) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute(
            """
            SELECT incident_id, kind, status, started_at, ended_at, summary, details
            FROM sre_incidents
            WHERE kind=%s
              AND status IN ('open','diagnosing','remediating','needs_human_review','awaiting_approval')
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (kind,),
        ).fetchone()
        return dict(row) if row else None


def get_pod_history(hours: int = 12) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT observed_at, pod_name, pod_uid, phase, ready, restart_count,
                   node_name, container_statuses, last_termination_reason,
                   last_exit_code, last_finished_at
            FROM sre_pod_snapshots
            WHERE observed_at >= %s
            ORDER BY observed_at DESC
            LIMIT 1000
            """,
            (_since(hours),),
        ).fetchall()
        return [dict(r) for r in rows]


def record_approval_decision(decision: dict[str, Any]) -> None:
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO sre_approval_decisions(
                decision_id, incident_id, action, decision, decided_at, details
            ) VALUES (%s,%s,%s,%s,%s,%s::jsonb)
            """,
            (
                decision["decision_id"], decision["incident_id"], decision.get("action"),
                decision["decision"], decision["decided_at"],
                json.dumps(decision.get("details", {}), default=str),
            ),
        )
        conn.commit()


def get_approvals(hours: int = 12) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT decision_id, incident_id, action, decision, decided_at, details
            FROM sre_approval_decisions
            WHERE decided_at >= %s
            ORDER BY decided_at DESC
            LIMIT 200
            """,
            (_since(hours),),
        ).fetchall()
        return [dict(r) for r in rows]
