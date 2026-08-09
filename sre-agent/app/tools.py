import json

from langchain_core.tools import tool

from . import db
from .k8s_client import current_app_logs, current_cluster_state, deployment_history


def _json(value) -> str:
    return json.dumps(value, default=str, indent=2)


@tool
def get_incidents(hours: int = 12) -> str:
    """Return recorded SRE incidents from the last N hours, including pod disappearance, deployment regression, and resource OOM incidents."""
    return _json(db.get_incidents(hours))


@tool
def get_kubernetes_events(hours: int = 12) -> str:
    """Return persisted Kubernetes events from the bulletin-board namespace for the last N hours."""
    return _json(db.get_events(hours))


@tool
def get_health_history(hours: int = 12) -> str:
    """Return an aggregated SRE application-readiness probe summary for the last N hours."""
    return _json(db.get_health_summary(hours))


@tool
def get_pod_restart_history(hours: int = 12) -> str:
    """Return persisted bulletin-board Pod snapshots including restart counts and last container termination reasons such as OOMKilled."""
    return _json(db.get_pod_history(hours))


@tool
def get_current_cluster_state() -> str:
    """Return the current bulletin-board Deployment, resources, ReplicaSet history, Pod/container state, and PostgreSQL Kubernetes state."""
    return _json(current_cluster_state())


@tool
def get_current_application_logs(tail_lines: int = 100) -> str:
    """Return recent current and, when available, previous-container bulletin-board logs. This does not read any user-agent logs."""
    return _json(current_app_logs(tail_lines))


@tool
def get_deployment_history() -> str:
    """Return the Kubernetes ReplicaSet-backed revision history for the bulletin-board Deployment, including images and resources."""
    return _json(deployment_history())


@tool
def get_remediation_history(hours: int = 12) -> str:
    """Return automatic and human-approved remediation actions recorded by sre-agent during the last N hours."""
    return _json(db.get_remediations(hours))


@tool
def get_approval_history(hours: int = 12) -> str:
    """Return human approval/rejection decisions recorded for SRE remediation workflows during the last N hours."""
    return _json(db.get_approvals(hours))


TOOLS = [
    get_incidents,
    get_kubernetes_events,
    get_health_history,
    get_pod_restart_history,
    get_current_cluster_state,
    get_current_application_logs,
    get_deployment_history,
    get_remediation_history,
    get_approval_history,
]
