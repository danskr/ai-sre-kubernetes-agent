import logging
import time
from typing import Any, Literal

from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from app import db
from app.config import settings
from app.k8s_client import deployment_state, list_app_pods, probe_application
from app.remediation import (
    collect_regression_evidence,
    diagnose_regression,
    evaluate_rollback_policy,
    execute_rollback,
    regression_no_action,
    route_after_rollback_policy,
    verify_rollback,
)
from app.resource_review import (
    execute_resource_mitigation,
    human_resource_approval,
    load_resource_review,
    mark_resource_awaiting_approval,
    reject_resource_mitigation,
    route_after_resource_approval,
    verify_resource_mitigation,
)
from app.tools import TOOLS

logger = logging.getLogger(__name__)


class SREState(MessagesState, total=False):
    # request_type is deliberately explicit. Normal chat enters through "chat";
    # operational incidents are submitted by the background observer through
    # Agent Server using "incident".
    request_type: str
    incident_id: str
    incident_kind: str
    consecutive_probe_failures: int
    incident: dict[str, Any]
    evidence: dict[str, Any]
    diagnosis: dict[str, Any]
    policy: dict[str, Any]
    rollback: dict[str, Any]
    decision: dict[str, Any]
    execution: dict[str, Any]
    verification: dict[str, Any]
    workflow_result: dict[str, Any]


SYSTEM_PROMPT = """You are the conversational interface of one unified Kubernetes SRE workflow for the bulletin-board application.

The same LangGraph workflow supports three operational behaviors:
- Scenario 1: observe a pod disappearance, allow Kubernetes to self-heal, and verify recovery without agent remediation.
- Scenario 2: diagnose a strongly evidenced post-deployment regression and, only when deterministic safety policy permits it, automatically roll back and verify recovery.
- Scenario 3: diagnose repeated OOMKilled resource pressure, explicitly refuse autonomous remediation, pause for human approval, and only then execute a bounded mitigation and verify it.

Your conversational branch is read-only. Operational writes are not LLM tools and cannot be triggered by ordinary chat messages; they exist only in explicit incident-workflow nodes guarded by deterministic policy and, for Scenario 3, human approval.

Evidence boundary:
- You may use Kubernetes state/events for the bulletin-board namespace, bulletin-board application logs, the SRE agent's own readiness-probe history, incident records, Deployment revision history, approval records, and remediation audit records.
- You have NO access to user-agent logs, metrics, status, request history, or any other user-agent data. Never claim otherwise.

Rules:
- For questions about the past, call historical incident/event/health/remediation tools rather than relying on chat memory.
- For current-state questions, call the current cluster-state tool.
- Cite concrete evidence in the answer: timestamps, pod names, revisions/images, Kubernetes reasons, probe status, and recovery timing when available.
- Clearly distinguish observed fact from inference.
- If evidence is insufficient, say so.
- Scenario 1 pod self-healing is performed by Kubernetes; say so when applicable.
- Scenario 2 automatic rollback may be performed by this unified workflow. Only claim it happened when remediation/incident evidence records it.
- OOMKilled proves a memory-limit breach but does not by itself prove an application memory leak.
- Scenario 3 requires an explicit human approval before the bounded memory-limit increase. Call approval/remediation history before claiming approval or mitigation occurred.
- Keep answers concise unless the user asks for detail.
"""


model = ChatOpenAI(
    model=settings.openai_model,
    api_key=settings.openai_api_key,
    temperature=0,
    use_responses_api=True,
    output_version="responses/v1",
    reasoning={"effort": "low", "summary": "auto"},
)
model_with_tools = model.bind_tools(TOOLS)


def route_request(state: SREState) -> dict[str, Any]:
    return {}


def route_request_type(state: SREState) -> Literal["chat_agent", "load_incident"]:
    return "load_incident" if state.get("request_type") == "incident" else "chat_agent"


def call_model(state: SREState):
    response = model_with_tools.invoke([SystemMessage(content=SYSTEM_PROMPT)] + state["messages"])
    return {"messages": [response]}


def route_chat(state: SREState) -> Literal["tools", "__end__"]:
    last = state["messages"][-1]
    return "tools" if getattr(last, "tool_calls", None) else END


def load_incident(state: SREState) -> dict[str, Any]:
    incident_id = state.get("incident_id")
    if not incident_id:
        raise RuntimeError("incident_id is required for an incident workflow")
    incident = db.get_incident(incident_id)
    if not incident:
        raise RuntimeError(f"Incident {incident_id} not found")
    return {
        "incident": incident,
        "incident_kind": state.get("incident_kind") or incident.get("kind") or "unknown",
    }


def route_incident_kind(
    state: SREState,
) -> Literal[
    "verify_kubernetes_self_healing",
    "collect_regression_evidence",
    "resource_triage",
    "unsupported_incident",
]:
    kind = state.get("incident_kind")
    if kind == "pod_disappearance":
        return "verify_kubernetes_self_healing"
    if kind == "runtime_regression":
        return "collect_regression_evidence"
    if kind == "resource_oom":
        return "resource_triage"
    return "unsupported_incident"


def verify_kubernetes_self_healing(state: SREState) -> dict[str, Any]:
    incident_id = state["incident_id"]
    deadline = time.monotonic() + settings.self_heal_verify_timeout_seconds
    successes = 0
    observations: list[dict[str, Any]] = []

    while time.monotonic() < deadline:
        dep = deployment_state()
        pods = list_app_pods()
        probe = probe_application(timeout_seconds=3.0)
        all_ready = bool(pods) and all(bool(p.get("ready")) for p in pods)
        replicas_restored = (
            dep.get("desired_replicas", 0) > 0
            and dep.get("ready_replicas", 0) >= dep.get("desired_replicas", 0)
        )
        healthy = all_ready and replicas_restored and probe.get("ok", False)
        observations.append(
            {
                "time": time.time(),
                "deployment": dep,
                "pods": pods,
                "probe": probe,
                "healthy": healthy,
            }
        )
        if healthy:
            successes += 1
            if successes >= settings.self_heal_verify_successes:
                result = {
                    "verified": True,
                    "self_healed_by": "kubernetes",
                    "agent_write_action_executed": False,
                    "consecutive_successes": successes,
                    "observations": observations[-10:],
                }
                db.update_incident(
                    incident_id,
                    status="resolved",
                    summary=(
                        "Kubernetes restored the desired application replica count and the application readiness probe is healthy. "
                        "The unified SRE workflow verified recovery; no agent remediation was required."
                    ),
                    details={"workflow_verification": result},
                    end=True,
                )
                logger.info("Scenario 1 self-healing verified incident=%s", incident_id)
                return {"verification": result, "workflow_result": result}
        else:
            successes = 0
        time.sleep(2)

    result = {
        "verified": False,
        "self_healed_by": "kubernetes",
        "agent_write_action_executed": False,
        "observations": observations[-10:],
    }
    db.update_incident(
        incident_id,
        status="needs_human_review",
        summary="Kubernetes self-recovery was not verified within the workflow timeout; human review is required.",
        details={"workflow_verification": result},
    )
    return {"verification": result, "workflow_result": result}


def prepare_regression_evidence(state: SREState) -> dict[str, Any]:
    incident_id = state["incident_id"]
    count = int(state.get("consecutive_probe_failures") or 0)
    if count <= 0:
        incident = state.get("incident") or db.get_incident(incident_id) or {}
        count = int((incident.get("details") or {}).get("consecutive_probe_failures") or 0)

    db.update_incident(
        incident_id,
        status="diagnosing",
        summary="Degradation detected shortly after a Deployment revision; the unified workflow is collecting evidence.",
    )
    evidence = collect_regression_evidence(count)
    db.update_incident(incident_id, details={"diagnostic_evidence": evidence})
    return {"evidence": evidence, "consecutive_probe_failures": count}


def unsupported_incident(state: SREState) -> dict[str, Any]:
    kind = state.get("incident_kind") or "unknown"
    result = {"handled": False, "reason": f"Unsupported incident kind: {kind}"}
    db.update_incident(
        state["incident_id"],
        status="needs_human_review",
        summary=f"The unified workflow does not have a safe automated path for incident kind {kind}.",
        details={"workflow_result": result},
    )
    return {"workflow_result": result}


def _build_graph(*, checkpointer=None):
    builder = StateGraph(SREState)

    # Common entry and conversational branch.
    builder.add_node("route_request", route_request)
    builder.add_node("chat_agent", call_model)
    builder.add_node("tools", ToolNode(TOOLS))

    # Incident classification and Scenario 1.
    builder.add_node("load_incident", load_incident)
    builder.add_node("verify_kubernetes_self_healing", verify_kubernetes_self_healing)
    builder.add_node("unsupported_incident", unsupported_incident)

    # Scenario 2: deterministic automatic rollback path.
    builder.add_node("collect_regression_evidence", prepare_regression_evidence)
    builder.add_node("diagnose_release_regression", diagnose_regression)
    builder.add_node("evaluate_rollback_policy", evaluate_rollback_policy)
    builder.add_node("rollback_release", execute_rollback)
    builder.add_node("verify_rollback", verify_rollback)
    builder.add_node("regression_no_action", regression_no_action)

    # Scenario 3: human-in-the-loop bounded mitigation path.
    builder.add_node("resource_triage", load_resource_review)
    builder.add_node("await_human_approval", mark_resource_awaiting_approval)
    builder.add_node("human_approval", human_resource_approval)
    builder.add_node("execute_bounded_mitigation", execute_resource_mitigation)
    builder.add_node("verify_mitigation", verify_resource_mitigation)
    builder.add_node("reject_no_action", reject_resource_mitigation)

    builder.add_edge(START, "route_request")
    builder.add_conditional_edges(
        "route_request",
        route_request_type,
        {"chat_agent": "chat_agent", "load_incident": "load_incident"},
    )

    builder.add_conditional_edges("chat_agent", route_chat, {"tools": "tools", END: END})
    builder.add_edge("tools", "chat_agent")

    builder.add_conditional_edges(
        "load_incident",
        route_incident_kind,
        {
            "verify_kubernetes_self_healing": "verify_kubernetes_self_healing",
            "collect_regression_evidence": "collect_regression_evidence",
            "resource_triage": "resource_triage",
            "unsupported_incident": "unsupported_incident",
        },
    )
    builder.add_edge("verify_kubernetes_self_healing", END)
    builder.add_edge("unsupported_incident", END)

    builder.add_edge("collect_regression_evidence", "diagnose_release_regression")
    builder.add_edge("diagnose_release_regression", "evaluate_rollback_policy")
    builder.add_conditional_edges(
        "evaluate_rollback_policy",
        route_after_rollback_policy,
        {"rollback": "rollback_release", "no_action": "regression_no_action"},
    )
    builder.add_edge("rollback_release", "verify_rollback")
    builder.add_edge("verify_rollback", END)
    builder.add_edge("regression_no_action", END)

    builder.add_edge("resource_triage", "await_human_approval")
    builder.add_edge("await_human_approval", "human_approval")
    builder.add_conditional_edges(
        "human_approval",
        route_after_resource_approval,
        {"execute": "execute_bounded_mitigation", "reject": "reject_no_action"},
    )
    builder.add_edge("execute_bounded_mitigation", "verify_mitigation")
    builder.add_edge("verify_mitigation", END)
    builder.add_edge("reject_no_action", END)

    return builder.compile(checkpointer=checkpointer)


# Backward-compatible /chat uses the exact same workflow definition in-process.
graph = _build_graph(checkpointer=InMemorySaver())

# LangGraph Agent Server owns persistence for Studio and for incident runs submitted
# by the background observer. This is the ONE exported workflow.
studio_graph = _build_graph()
