import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from app import db
from app.config import settings
from app.k8s_client import (
    current_app_logs,
    current_cluster_state,
    dependency_state,
    deployment_history,
    deployment_state,
    increase_app_memory_limit,
    list_app_pods,
    probe_application,
)

logger = logging.getLogger(__name__)


class ResourceDiagnosis(BaseModel):
    observed_condition: Literal[
        "container_memory_limit_exceeded",
        "node_or_infrastructure_pressure",
        "resource_condition_unknown",
    ]
    condition_confidence: float = Field(ge=0.0, le=1.0)
    likely_root_cause: Literal[
        "application_memory_growth",
        "memory_limit_too_low",
        "node_or_infrastructure_pressure",
        "unknown",
    ]
    root_cause_confidence: float = Field(ge=0.0, le=1.0)
    recommended_human_action: Literal[
        "increase_memory_limit",
        "rollback",
        "observe",
        "human_review",
    ]
    summary: str
    evidence: list[str]
    risk_notes: list[str]


class ResourceReviewState(TypedDict, total=False):
    incident_id: str
    incident: dict[str, Any]
    evidence: dict[str, Any]
    diagnosis: dict[str, Any]
    policy: dict[str, Any]
    decision: dict[str, Any]
    execution: dict[str, Any]
    verification: dict[str, Any]


RESOURCE_PROMPT = """You are performing SRE triage for a Kubernetes resource-pressure incident.

Evidence boundary:
- Use only bulletin-board Kubernetes state/events, application/current-previous logs, persisted pod/restart history, readiness probes, Deployment history, and PostgreSQL Kubernetes-level state.
- You have no access to the user-agent or the hidden demo trigger.

Interpretation rules:
- A Kubernetes container status of OOMKilled is strong evidence that the container exceeded its configured memory limit. It does NOT by itself prove whether the cause is an application memory leak, legitimate workload growth, or an undersized limit.
- Repeated OOMKilled restarts show that Kubernetes restart/self-healing is not permanently solving the condition.
- Do not claim a memory leak unless the available evidence supports that inference; distinguish the observed memory-limit breach from the root-cause hypothesis.
- Increasing a memory limit can provide temporary headroom but can mask a leak or increase node-level risk. Restarting is already being attempted by Kubernetes. Rollback is appropriate only when there is strong release-correlation evidence.
- This incident class is intentionally NOT eligible for autonomous remediation. Recommend the best human-reviewed next action, but preserve uncertainty.

Use concrete pod names, restart counts, OOMKilled/exit evidence, memory limits, revision information, dependency health, and timestamps.
"""


model = ChatOpenAI(
    model=settings.openai_model,
    api_key=settings.openai_api_key,
    temperature=0,
    use_responses_api=True,
    output_version="responses/v1",
    reasoning={"effort": "medium", "summary": "auto"},
)
structured_model = model.with_structured_output(ResourceDiagnosis)


def collect_resource_evidence(incident_id: str) -> dict[str, Any]:
    return {
        "collected_at": datetime.now(timezone.utc),
        "incident": db.get_incident(incident_id),
        "cluster_state": current_cluster_state(),
        "dependency_state": dependency_state(),
        "deployment_history": deployment_history()[:5],
        "pod_history": db.get_pod_history(1)[:160],
        "health_summary": db.get_health_summary(1),
        "recent_kubernetes_events": db.get_events(1)[:100],
        "application_logs": current_app_logs(160),
    }


def triage_resource_incident(incident_id: str) -> dict[str, Any]:
    incident = db.get_incident(incident_id)
    if not incident or incident.get("kind") != "resource_oom":
        raise RuntimeError(f"Incident {incident_id} is not an active resource_oom incident")

    db.update_incident(
        incident_id,
        status="diagnosing",
        summary="Repeated OOMKilled restarts detected; collecting resource-pressure evidence. No autonomous write action is permitted.",
    )
    evidence = collect_resource_evidence(incident_id)
    response = structured_model.invoke(
        [
            SystemMessage(content=RESOURCE_PROMPT),
            HumanMessage(content=json.dumps(evidence, default=str, indent=2)),
        ]
    )
    diagnosis = response.model_dump()
    policy = {
        "automatic_action_allowed": False,
        "human_approval_required": True,
        "reason": (
            "Resource-pressure remediation can mask an application defect or consume additional node capacity; "
            "this incident class is outside the autonomous rollback policy."
        ),
        "allowed_human_actions": ["increase_memory_limit"],
        "increase_memory_limit": {
            "target": settings.human_memory_limit_target,
            "hard_maximum": settings.human_memory_limit_max,
            "scope": f"{settings.app_namespace}/{settings.app_deployment_name}:{settings.app_container_name}",
            "effect": "temporary additional memory headroom; not a root-cause fix",
        },
    }
    db.update_incident(
        incident_id,
        status="needs_human_review",
        summary="Repeated OOMKilled restarts require human review; autonomous remediation was deliberately refused.",
        details={
            "resource_evidence": evidence,
            "resource_diagnosis": diagnosis,
            "resource_policy": policy,
        },
    )
    logger.warning(
        "Resource triage incident=%s condition=%s root_cause=%s root_confidence=%.3f action=%s auto_allowed=false",
        incident_id,
        diagnosis["observed_condition"],
        diagnosis["likely_root_cause"],
        diagnosis["root_cause_confidence"],
        diagnosis["recommended_human_action"],
    )
    return {"evidence": evidence, "diagnosis": diagnosis, "policy": policy}


def _load_review(state: ResourceReviewState) -> dict[str, Any]:
    incident_id = state.get("incident_id")
    if not incident_id:
        raise RuntimeError("incident_id is required")
    incident = db.get_incident(incident_id)
    if not incident:
        raise RuntimeError(f"Incident {incident_id} not found")
    if incident.get("kind") != "resource_oom":
        raise RuntimeError(f"Incident {incident_id} is {incident.get('kind')}, not resource_oom")

    details = incident.get("details") or {}
    evidence = details.get("resource_evidence")
    diagnosis = details.get("resource_diagnosis")
    policy = details.get("resource_policy")
    if not (evidence and diagnosis and policy):
        triage = triage_resource_incident(incident_id)
        incident = db.get_incident(incident_id) or incident
        evidence = triage["evidence"]
        diagnosis = triage["diagnosis"]
        policy = triage["policy"]

    return {
        "incident": incident,
        "evidence": evidence,
        "diagnosis": diagnosis,
        "policy": policy,
    }


def _mark_awaiting_approval(state: ResourceReviewState) -> dict[str, Any]:
    db.update_incident(
        state["incident_id"],
        status="awaiting_approval",
        summary="Resource-pressure mitigation is paused pending explicit human approval.",
    )
    return {}


def _approval(state: ResourceReviewState) -> dict[str, Any]:
    diagnosis = state["diagnosis"]
    policy = state["policy"]
    value = interrupt(
        {
            "type": "sre_human_approval",
            "incident_id": state["incident_id"],
            "summary": diagnosis.get("summary"),
            "observed_condition": diagnosis.get("observed_condition"),
            "condition_confidence": diagnosis.get("condition_confidence"),
            "likely_root_cause": diagnosis.get("likely_root_cause"),
            "root_cause_confidence": diagnosis.get("root_cause_confidence"),
            "evidence": diagnosis.get("evidence", []),
            "risk_notes": diagnosis.get("risk_notes", []),
            "automatic_action_allowed": False,
            "proposed_action": {
                "action": "increase_memory_limit",
                "target": settings.human_memory_limit_target,
                "hard_maximum": settings.human_memory_limit_max,
                "scope": policy.get("increase_memory_limit", {}).get("scope"),
                "warning": "This is a bounded temporary mitigation, not proof that the root cause is fixed.",
            },
            "resume_with": {
                "approve": {"decision": "approve", "action": "increase_memory_limit"},
                "reject": {"decision": "reject", "reason": "optional operator reason"},
            },
        }
    )

    if isinstance(value, str):
        normalized = {"decision": value.strip().lower()}
    elif isinstance(value, dict):
        normalized = dict(value)
    else:
        normalized = {"decision": "reject", "reason": f"Unsupported approval payload type: {type(value).__name__}"}

    normalized["decision"] = str(normalized.get("decision", "reject")).strip().lower()

    # Studio's human-approval UI may resume the interrupt with only
    # {"decision": "approve"}. The interrupt itself offers exactly one
    # bounded write action, so map an approval with no explicit action to
    # that offered action. Never infer an action for reject/unknown values.
    if normalized["decision"] == "approve" and not normalized.get("action"):
        normalized["action"] = "increase_memory_limit"

    return {"decision": normalized}


def _route_after_approval(state: ResourceReviewState) -> Literal["execute", "reject"]:
    decision = state.get("decision") or {}
    if decision.get("decision") == "approve" and decision.get("action") == "increase_memory_limit":
        return "execute"
    return "reject"


def _execute(state: ResourceReviewState) -> dict[str, Any]:
    incident_id = state["incident_id"]
    diagnosis = state["diagnosis"]
    decision_id = str(uuid.uuid4())
    action_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    db.record_approval_decision(
        {
            "decision_id": decision_id,
            "incident_id": incident_id,
            "action": "increase_memory_limit",
            "decision": "approved",
            "decided_at": now,
            "details": {
                "source": "LangGraph interrupt",
                "human_input": state.get("decision"),
                "target": settings.human_memory_limit_target,
            },
        }
    )
    db.create_remediation_action(
        {
            "action_id": action_id,
            "incident_id": incident_id,
            "action": "increase_memory_limit_human_approved",
            "status": "running",
            "started_at": now,
            "details": {
                "approval_decision_id": decision_id,
                "diagnosis": diagnosis,
                "policy": state["policy"],
            },
        }
    )
    db.update_incident(
        incident_id,
        status="remediating",
        summary="Human approved a bounded application memory-limit increase; applying mitigation.",
        details={"approval_decision_id": decision_id, "action_id": action_id},
    )

    try:
        patch = increase_app_memory_limit(
            target_limit=settings.human_memory_limit_target,
            incident_id=incident_id,
            reason=diagnosis.get("summary", "human-approved resource mitigation"),
        )
    except Exception as exc:
        db.finish_remediation_action(action_id, "failed", {"error": str(exc)})
        db.update_incident(
            incident_id,
            status="needs_human_review",
            summary="Human-approved resource mitigation failed to apply.",
            details={"execution_error": str(exc)},
        )
        raise

    return {
        "execution": {
            "decision_id": decision_id,
            "action_id": action_id,
            "patch": patch,
        }
    }


def _verify(state: ResourceReviewState) -> dict[str, Any]:
    execution = state["execution"]
    action_id = execution["action_id"]
    target = settings.human_memory_limit_target
    deadline = time.monotonic() + settings.resource_verify_timeout_seconds
    consecutive_successes = 0
    observations: list[dict[str, Any]] = []
    baseline_restarts: dict[str, int] | None = None

    while time.monotonic() < deadline:
        dep = deployment_state()
        pods = list_app_pods()
        probe = probe_application(timeout_seconds=3.0)
        limit = (
            dep.get("resources", {})
            .get(settings.app_container_name, {})
            .get("limits", {})
            .get("memory")
        )
        target_applied = str(limit) == target
        replicas_ready = (
            dep.get("desired_replicas", 0) > 0
            and dep.get("ready_replicas", 0) >= dep.get("desired_replicas", 0)
        )

        restart_map = {p["pod_uid"]: int(p.get("restart_count") or 0) for p in pods}
        if target_applied and replicas_ready and baseline_restarts is None:
            baseline_restarts = restart_map
        restart_increased = False
        if baseline_restarts is not None:
            for uid, count in restart_map.items():
                if uid in baseline_restarts and count > baseline_restarts[uid]:
                    restart_increased = True
                    break

        success = target_applied and replicas_ready and probe["ok"] and not restart_increased
        observations.append(
            {
                "time": datetime.now(timezone.utc).isoformat(),
                "memory_limit": limit,
                "deployment_revision": dep.get("revision"),
                "replicas_ready": replicas_ready,
                "probe": probe,
                "restart_map": restart_map,
                "restart_increased": restart_increased,
                "success": success,
            }
        )

        if success:
            consecutive_successes += 1
            if consecutive_successes >= settings.resource_verify_successes:
                result = {
                    "verified": True,
                    "consecutive_successes": consecutive_successes,
                    "target_memory_limit": target,
                    "observations": observations[-12:],
                    "root_cause_resolved": False,
                    "interpretation": (
                        "The human-approved memory headroom increase stabilized the workload during the verification window. "
                        "This is a mitigation only; the underlying memory-growth cause remains unresolved."
                    ),
                }
                db.finish_remediation_action(action_id, "succeeded", result)
                db.update_incident(
                    state["incident_id"],
                    status="mitigated",
                    summary="Human-approved memory headroom increase stabilized the service; root cause remains unresolved.",
                    details={"human_mitigation": execution, "verification": result},
                    end=True,
                )
                return {"verification": result}
        else:
            consecutive_successes = 0
        time.sleep(2)

    result = {
        "verified": False,
        "consecutive_successes": consecutive_successes,
        "target_memory_limit": target,
        "observations": observations[-12:],
        "root_cause_resolved": False,
    }
    db.finish_remediation_action(action_id, "verification_failed", result)
    db.update_incident(
        state["incident_id"],
        status="needs_human_review",
        summary="Human-approved memory mitigation was applied but stable recovery was not verified.",
        details={"human_mitigation": execution, "verification": result},
    )
    return {"verification": result}


def _reject(state: ResourceReviewState) -> dict[str, Any]:
    decision = state.get("decision") or {}
    decision_id = str(uuid.uuid4())
    db.record_approval_decision(
        {
            "decision_id": decision_id,
            "incident_id": state["incident_id"],
            "action": decision.get("action") or "increase_memory_limit",
            "decision": "rejected",
            "decided_at": datetime.now(timezone.utc),
            "details": {"source": "LangGraph interrupt", "human_input": decision},
        }
    )
    db.update_incident(
        state["incident_id"],
        status="human_rejected",
        summary="Human reviewer rejected the proposed resource-pressure mitigation; no write action was executed.",
        details={"approval_decision_id": decision_id, "human_decision": decision},
        end=True,
    )
    return {"verification": {"verified": False, "action_executed": False, "decision": "rejected"}}



# Public node aliases used by the unified sre_agent graph.
load_resource_review = _load_review
mark_resource_awaiting_approval = _mark_awaiting_approval
human_resource_approval = _approval
route_after_resource_approval = _route_after_approval
execute_resource_mitigation = _execute
verify_resource_mitigation = _verify
reject_resource_mitigation = _reject
