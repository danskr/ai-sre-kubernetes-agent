import json
from typing import Any

from langchain_core.tools import tool

from . import db
from .k8s_client import current_app_logs, current_cluster_state, deployment_history

# Chat tools intentionally return compact evidence. The incident database can contain
# large nested verification histories; returning those raw records to an LLM can turn a
# simple question into a very large model request. Keep every individual tool response
# bounded even if the backing database contains days of detailed observations.
MAX_TOOL_OUTPUT_CHARS = 24_000


def _json(value: Any, *, max_chars: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    text = json.dumps(value, default=str, separators=(",", ":"))
    if len(text) <= max_chars:
        return text
    return json.dumps(
        {
            "truncated": True,
            "reason": "tool output exceeded the conversational evidence budget",
            "returned_characters": max_chars,
            "preview": text[:max_chars],
        },
        separators=(",", ":"),
    )


def _compact_probe(probe: Any) -> dict[str, Any] | None:
    if not isinstance(probe, dict):
        return None
    return {
        "observed_at": probe.get("observed_at"),
        "ok": probe.get("ok"),
        "status_code": probe.get("status_code"),
        "error": probe.get("error"),
    }


def _compact_pod(pod: Any) -> dict[str, Any] | None:
    if not isinstance(pod, dict):
        return None
    return {
        "pod_name": pod.get("pod_name"),
        "pod_uid": pod.get("pod_uid"),
        "phase": pod.get("phase"),
        "ready": pod.get("ready"),
        "restart_count": pod.get("restart_count"),
        "last_termination_reason": pod.get("last_termination_reason"),
        "last_exit_code": pod.get("last_exit_code"),
        "owner_replicaset": pod.get("owner_replicaset"),
        "images": pod.get("images"),
    }


def _compact_deployment(dep: Any) -> dict[str, Any] | None:
    if not isinstance(dep, dict):
        return None
    return {
        "name": dep.get("name"),
        "revision": dep.get("revision"),
        "generation": dep.get("generation"),
        "images": dep.get("images"),
        "resources": dep.get("resources"),
        "desired_replicas": dep.get("desired_replicas"),
        "ready_replicas": dep.get("ready_replicas"),
        "available_replicas": dep.get("available_replicas"),
        "updated_replicas": dep.get("updated_replicas"),
        "template_fingerprint": dep.get("template_fingerprint"),
    }


def _compact_observation(obs: Any) -> dict[str, Any] | None:
    if not isinstance(obs, dict):
        return None
    pods = [_compact_pod(p) for p in (obs.get("pods") or [])[:3]]
    return {
        "time": obs.get("time"),
        "healthy": obs.get("healthy"),
        "success": obs.get("success"),
        "memory_limit": obs.get("memory_limit"),
        "replicas_ready": obs.get("replicas_ready"),
        "restart_increased": obs.get("restart_increased"),
        "deployment_revision": obs.get("deployment_revision"),
        "probe": _compact_probe(obs.get("probe")),
        "deployment": _compact_deployment(obs.get("deployment")),
        "pods": [p for p in pods if p is not None],
    }


def _compact_diagnosis(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "summary": value.get("summary"),
        "observed_condition": value.get("observed_condition"),
        "condition_confidence": value.get("condition_confidence"),
        "likely_root_cause": value.get("likely_root_cause"),
        "root_cause_confidence": value.get("root_cause_confidence"),
        "recommended_human_action": value.get("recommended_human_action"),
        "evidence": list(value.get("evidence") or [])[:12],
        "risk_notes": list(value.get("risk_notes") or [])[:8],
    }


def _compact_policy(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "automatic_action_allowed": value.get("automatic_action_allowed"),
        "human_approval_required": value.get("human_approval_required"),
        "reason": value.get("reason"),
        "allowed_human_actions": value.get("allowed_human_actions"),
        "increase_memory_limit": value.get("increase_memory_limit"),
        "rollback_allowed": value.get("rollback_allowed"),
    }


def _compact_verification(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    observations = [
        o for o in (_compact_observation(x) for x in (value.get("observations") or [])[-3:]) if o is not None
    ]
    return {
        "verified": value.get("verified"),
        "self_healed_by": value.get("self_healed_by"),
        "agent_write_action_executed": value.get("agent_write_action_executed"),
        "consecutive_successes": value.get("consecutive_successes"),
        "root_cause_resolved": value.get("root_cause_resolved"),
        "target_memory_limit": value.get("target_memory_limit"),
        "interpretation": value.get("interpretation"),
        "observations": observations,
    }


def _compact_incident_summary(value: Any) -> dict[str, Any]:
    details = value.get("details") if isinstance(value.get("details"), dict) else {}
    workflow = details.get("workflow") if isinstance(details.get("workflow"), dict) else {}
    verification = details.get("workflow_verification") or details.get("verification")
    return {
        "incident_id": value.get("incident_id"),
        "kind": value.get("kind"),
        "status": value.get("status"),
        "started_at": value.get("started_at"),
        "ended_at": value.get("ended_at"),
        "summary": value.get("summary"),
        "lost_pod_name": details.get("lost_pod_name"),
        "deployment_revision": details.get("deployment_revision"),
        "workflow": {
            "status": workflow.get("status"),
            "thread_id": workflow.get("thread_id"),
        },
        "verification": _compact_verification(verification),
    }


def _compact_incident_details(value: Any) -> dict[str, Any]:
    details = value.get("details") if isinstance(value.get("details"), dict) else {}
    result = _compact_incident_summary(value)
    result.update(
        {
            "previous_phase": details.get("previous_phase"),
            "owner_replicaset": details.get("owner_replicaset"),
            "diagnosis": _compact_diagnosis(details.get("diagnosis")),
            "policy": _compact_policy(details.get("policy")),
            "approval_decision_id": details.get("approval_decision_id"),
            "workflow_result": _compact_verification(details.get("workflow_result")),
        }
    )
    # Some incident classes keep the useful evidence under nested result objects.
    for key in ("evidence", "regression_evidence", "resource_review"):
        if key in details:
            raw = details.get(key)
            if isinstance(raw, list):
                result[key] = raw[:12]
            elif isinstance(raw, dict):
                result[key] = {k: raw[k] for k in list(raw)[:20]}
    return result


def _compact_remediation(value: Any) -> dict[str, Any]:
    details = value.get("details") if isinstance(value.get("details"), dict) else {}
    observations = [
        o for o in (_compact_observation(x) for x in (details.get("observations") or [])[-3:]) if o is not None
    ]
    return {
        "action_id": value.get("action_id"),
        "incident_id": value.get("incident_id"),
        "action": value.get("action"),
        "status": value.get("status"),
        "started_at": value.get("started_at"),
        "ended_at": value.get("ended_at"),
        "from_revision": value.get("from_revision"),
        "target_revision": value.get("target_revision"),
        "verified": details.get("verified"),
        "interpretation": details.get("interpretation"),
        "root_cause_resolved": details.get("root_cause_resolved"),
        "target_memory_limit": details.get("target_memory_limit"),
        "approval_decision_id": details.get("approval_decision_id"),
        "consecutive_successes": details.get("consecutive_successes"),
        "policy": _compact_policy(details.get("policy")),
        "diagnosis": _compact_diagnosis(details.get("diagnosis")),
        "observations": observations,
    }


@tool
def get_incidents(hours: int = 12, limit: int = 5) -> str:
    """Return a compact list of the newest SRE incidents. Use this first for questions about what happened recently; then call get_incident_details for one incident if more evidence is needed."""
    limit = max(1, min(limit, 10))
    incidents = db.get_incidents(hours)[:limit]
    return _json({"hours": hours, "count": len(incidents), "incidents": [_compact_incident_summary(i) for i in incidents]})


@tool
def get_incident_details(incident_id: str) -> str:
    """Return bounded evidence for one specific incident ID, including diagnosis and the last few verification observations. Prefer this over fetching broad histories when answering follow-up questions about a known incident."""
    incident = db.get_incident(incident_id)
    if not incident:
        return _json({"error": "incident_not_found", "incident_id": incident_id})
    return _json(_compact_incident_details(incident))


@tool
def get_kubernetes_events(hours: int = 2, limit: int = 30) -> str:
    """Return a bounded list of recent persisted Kubernetes events from the bulletin-board namespace."""
    limit = max(1, min(limit, 50))
    return _json({"hours": hours, "events": db.get_events(hours)[:limit]})


@tool
def get_health_history(hours: int = 12) -> str:
    """Return an aggregated SRE application-readiness probe summary for the last N hours."""
    summary = db.get_health_summary(hours)
    summary["recent_failures"] = list(summary.get("recent_failures") or [])[:20]
    return _json(summary)


@tool
def get_pod_restart_history(hours: int = 2, limit: int = 30) -> str:
    """Return a bounded list of persisted bulletin-board Pod snapshots, emphasizing restart and termination evidence."""
    limit = max(1, min(limit, 50))
    rows = db.get_pod_history(hours)[:limit]
    compact = []
    for row in rows:
        pod = _compact_pod(row) or {}
        pod["observed_at"] = row.get("observed_at")
        compact.append(pod)
    return _json({"hours": hours, "pods": compact})


@tool
def get_current_cluster_state() -> str:
    """Return the current bulletin-board Deployment, recent ReplicaSet history, Pod/container state, and PostgreSQL Kubernetes state."""
    state = current_cluster_state()
    compact = {
        "namespace": state.get("namespace"),
        "deployment": _compact_deployment(state.get("deployment")),
        "deployment_history": list(state.get("deployment_history") or [])[:5],
        "pods": [p for p in (_compact_pod(x) for x in (state.get("pods") or [])[:5]) if p is not None],
        "dependency_state": state.get("dependency_state"),
    }
    return _json(compact)


@tool
def get_current_application_logs(tail_lines: int = 60) -> str:
    """Return recent bulletin-board current/previous-container logs. This never reads user-agent logs. tail_lines is capped to keep chat context bounded."""
    tail_lines = max(10, min(tail_lines, 100))
    return _json(current_app_logs(tail_lines))


@tool
def get_deployment_history(limit: int = 8) -> str:
    """Return a bounded Kubernetes ReplicaSet-backed revision history for the bulletin-board Deployment, including images and resources."""
    limit = max(1, min(limit, 15))
    return _json({"revisions": deployment_history()[:limit]})


@tool
def get_remediation_history(hours: int = 12, limit: int = 10) -> str:
    """Return a compact audit history of automatic and human-approved remediation actions."""
    limit = max(1, min(limit, 20))
    rows = db.get_remediations(hours)[:limit]
    return _json({"hours": hours, "remediations": [_compact_remediation(x) for x in rows]})


@tool
def get_approval_history(hours: int = 12, limit: int = 10) -> str:
    """Return a compact list of human approval/rejection decisions for SRE remediation workflows."""
    limit = max(1, min(limit, 20))
    rows = db.get_approvals(hours)[:limit]
    compact = [
        {
            "decision_id": x.get("decision_id"),
            "incident_id": x.get("incident_id"),
            "action": x.get("action"),
            "decision": x.get("decision"),
            "decided_at": x.get("decided_at"),
            "details": x.get("details"),
        }
        for x in rows
    ]
    return _json({"hours": hours, "approvals": compact})


TOOLS = [
    get_incidents,
    get_incident_details,
    get_kubernetes_events,
    get_health_history,
    get_pod_restart_history,
    get_current_cluster_state,
    get_current_application_logs,
    get_deployment_history,
    get_remediation_history,
    get_approval_history,
]
