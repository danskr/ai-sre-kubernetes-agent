from datetime import datetime, timezone
from typing import Any

import hashlib
import json
import re

import httpx
from kubernetes import client, config

from .config import settings


_loaded = False
REVISION_ANNOTATION = "deployment.kubernetes.io/revision"


def load_config() -> None:
    global _loaded
    if _loaded:
        return
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    _loaded = True


def core_api() -> client.CoreV1Api:
    load_config()
    return client.CoreV1Api()


def apps_api() -> client.AppsV1Api:
    load_config()
    return client.AppsV1Api()


def _state_name(state) -> str | None:
    if state is None:
        return None
    if state.running is not None:
        return "running"
    if state.waiting is not None:
        return "waiting"
    if state.terminated is not None:
        return "terminated"
    return None


def _container_status(cs) -> dict[str, Any]:
    terminated = cs.last_state.terminated if cs.last_state else None
    waiting = cs.state.waiting if cs.state and cs.state.waiting else None
    return {
        "name": cs.name,
        "ready": bool(cs.ready),
        "restart_count": int(cs.restart_count or 0),
        "image": cs.image,
        "state": _state_name(cs.state),
        "waiting_reason": waiting.reason if waiting else None,
        "last_termination_reason": terminated.reason if terminated else None,
        "last_exit_code": terminated.exit_code if terminated else None,
        "last_signal": terminated.signal if terminated else None,
        "last_started_at": terminated.started_at if terminated else None,
        "last_finished_at": terminated.finished_at if terminated else None,
    }


def list_app_pods() -> list[dict[str, Any]]:
    pods = core_api().list_namespaced_pod(
        namespace=settings.app_namespace,
        label_selector=settings.app_label_selector,
    ).items

    result = []
    for pod in pods:
        conditions = pod.status.conditions or []
        ready = any(c.type == "Ready" and c.status == "True" for c in conditions)
        statuses = pod.status.container_statuses or []
        container_statuses = [_container_status(cs) for cs in statuses]
        restarts = sum(item["restart_count"] for item in container_statuses)
        images = {item["name"]: item["image"] for item in container_statuses}
        owners = pod.metadata.owner_references or []
        owner_replicaset = next((o.name for o in owners if o.kind == "ReplicaSet"), None)
        app_status = next(
            (item for item in container_statuses if item["name"] == settings.app_container_name),
            container_statuses[0] if container_statuses else {},
        )
        result.append(
            {
                "observed_at": datetime.now(timezone.utc),
                "pod_name": pod.metadata.name,
                "pod_uid": pod.metadata.uid,
                "phase": pod.status.phase,
                "ready": ready,
                "restart_count": restarts,
                "node_name": pod.spec.node_name,
                "images": images,
                "owner_replicaset": owner_replicaset,
                "container_statuses": container_statuses,
                "last_termination_reason": app_status.get("last_termination_reason"),
                "last_exit_code": app_status.get("last_exit_code"),
                "last_finished_at": app_status.get("last_finished_at"),
            }
        )
    return result


def _container_images(containers) -> dict[str, str]:
    return {c.name: c.image for c in (containers or [])}


def _container_resources(containers) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for c in containers or []:
        resources = c.resources
        output[c.name] = {
            "requests": dict(resources.requests or {}) if resources else {},
            "limits": dict(resources.limits or {}) if resources else {},
        }
    return output


def _revision(annotations: dict[str, str] | None) -> int | None:
    raw = (annotations or {}).get(REVISION_ANNOTATION)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def deployment_state() -> dict[str, Any]:
    dep = apps_api().read_namespaced_deployment(
        name=settings.app_deployment_name,
        namespace=settings.app_namespace,
    )
    containers = dep.spec.template.spec.containers or []
    return {
        "name": dep.metadata.name,
        "uid": dep.metadata.uid,
        "generation": dep.metadata.generation,
        "revision": _revision(dep.metadata.annotations),
        "desired_replicas": dep.spec.replicas or 0,
        "ready_replicas": dep.status.ready_replicas or 0,
        "available_replicas": dep.status.available_replicas or 0,
        "updated_replicas": dep.status.updated_replicas or 0,
        "observed_generation": dep.status.observed_generation,
        "images": _container_images(containers),
        "resources": _container_resources(containers),
        "template_fingerprint": _spec_fingerprint(dep.spec.template.spec),
        "creation_timestamp": dep.metadata.creation_timestamp,
    }


def _owned_replica_sets():
    dep = apps_api().read_namespaced_deployment(
        name=settings.app_deployment_name,
        namespace=settings.app_namespace,
    )
    replica_sets = apps_api().list_namespaced_replica_set(
        namespace=settings.app_namespace,
        label_selector=settings.app_label_selector,
    ).items
    owned = []
    for rs in replica_sets:
        owners = rs.metadata.owner_references or []
        if any(o.kind == "Deployment" and o.uid == dep.metadata.uid for o in owners):
            revision = _revision(rs.metadata.annotations)
            if revision is not None:
                owned.append((revision, rs))
    owned.sort(key=lambda item: item[0], reverse=True)
    return owned


def _spec_fingerprint(spec) -> str:
    serial = client.ApiClient().sanitize_for_serialization(spec)
    return hashlib.sha256(json.dumps(serial, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _template_summary(rs) -> dict[str, Any]:
    containers = rs.spec.template.spec.containers or []
    return {
        "images": _container_images(containers),
        "resources": _container_resources(containers),
        "template_fingerprint": _spec_fingerprint(rs.spec.template.spec),
    }


def deployment_history() -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    for revision, rs in _owned_replica_sets():
        summary = _template_summary(rs)
        history.append(
            {
                "revision": revision,
                "replicaset": rs.metadata.name,
                "created_at": rs.metadata.creation_timestamp,
                **summary,
                "desired_replicas": rs.spec.replicas or 0,
                "ready_replicas": rs.status.ready_replicas or 0,
                "available_replicas": rs.status.available_replicas or 0,
            }
        )
    return history


def rollback_to_previous_revision(reason: str, incident_id: str) -> dict[str, Any]:
    owned = _owned_replica_sets()
    if len(owned) < 2:
        raise RuntimeError("No previous Deployment revision is available for rollback")

    current_revision, current_rs = owned[0]
    previous_revision, previous_rs = owned[1]
    current_summary = _template_summary(current_rs)
    previous_summary = _template_summary(previous_rs)
    if current_summary["template_fingerprint"] == previous_summary["template_fingerprint"]:
        raise RuntimeError("Current and previous revisions use the same Pod template")

    api_client = client.ApiClient()
    previous_spec = api_client.sanitize_for_serialization(previous_rs.spec.template.spec)
    previous_labels = dict(previous_rs.spec.template.metadata.labels or {})
    previous_annotations = dict(previous_rs.spec.template.metadata.annotations or {})
    now = datetime.now(timezone.utc).isoformat()
    previous_annotations.update(
        {
            "sre-agent.openai.com/rollback-at": now,
            "sre-agent.openai.com/incident-id": incident_id,
            "sre-agent.openai.com/rollback-reason": reason[:240],
            "sre-agent.openai.com/source-revision": str(current_revision),
            "sre-agent.openai.com/target-revision": str(previous_revision),
        }
    )
    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "labels": previous_labels,
                    "annotations": previous_annotations,
                },
                "spec": previous_spec,
            }
        }
    }

    apps_api().patch_namespaced_deployment(
        name=settings.app_deployment_name,
        namespace=settings.app_namespace,
        body=patch,
        _content_type="application/merge-patch+json",
    )

    return {
        "deployment": settings.app_deployment_name,
        "namespace": settings.app_namespace,
        "from_revision": current_revision,
        "target_revision": previous_revision,
        "from_images": current_summary["images"],
        "target_images": previous_summary["images"],
        "from_template_fingerprint": current_summary["template_fingerprint"],
        "target_template_fingerprint": previous_summary["template_fingerprint"],
        "patched_at": now,
    }


def _memory_quantity_bytes(value: str) -> int:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(Ki|Mi|Gi|K|M|G)?\s*", value)
    if not match:
        raise ValueError(f"Unsupported Kubernetes memory quantity: {value!r}")
    number = float(match.group(1))
    unit = match.group(2)
    multipliers = {
        None: 1,
        "Ki": 1024,
        "Mi": 1024**2,
        "Gi": 1024**3,
        "K": 1000,
        "M": 1000**2,
        "G": 1000**3,
    }
    return int(number * multipliers[unit])


def increase_app_memory_limit(*, target_limit: str, incident_id: str, reason: str) -> dict[str, Any]:
    """Apply the one bounded Scenario 3 human-approved write action.

    The function refuses targets above the configured maximum and only patches the
    named application container in the allowlisted bulletin-board Deployment.
    """
    target_bytes = _memory_quantity_bytes(target_limit)
    max_bytes = _memory_quantity_bytes(settings.human_memory_limit_max)
    if target_bytes > max_bytes:
        raise RuntimeError(
            f"Requested target {target_limit} exceeds configured maximum {settings.human_memory_limit_max}"
        )

    dep = apps_api().read_namespaced_deployment(
        name=settings.app_deployment_name,
        namespace=settings.app_namespace,
    )
    container = next(
        (c for c in dep.spec.template.spec.containers or [] if c.name == settings.app_container_name),
        None,
    )
    if container is None:
        raise RuntimeError(f"Container {settings.app_container_name!r} not found")
    current_limit = (container.resources.limits or {}).get("memory") if container.resources else None
    if current_limit is None:
        raise RuntimeError("Application container has no memory limit to bound")
    current_bytes = _memory_quantity_bytes(str(current_limit))
    if target_bytes <= current_bytes:
        raise RuntimeError(
            f"Target memory limit {target_limit} must be greater than current limit {current_limit}"
        )

    now = datetime.now(timezone.utc).isoformat()
    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "sre-agent.openai.com/human-approved-at": now,
                        "sre-agent.openai.com/incident-id": incident_id,
                        "sre-agent.openai.com/approved-action": "increase-memory-limit",
                        "sre-agent.openai.com/action-reason": reason[:240],
                    }
                },
                "spec": {
                    "containers": [
                        {
                            "name": settings.app_container_name,
                            "resources": {"limits": {"memory": target_limit}},
                        }
                    ]
                },
            }
        }
    }
    apps_api().patch_namespaced_deployment(
        name=settings.app_deployment_name,
        namespace=settings.app_namespace,
        body=patch,
        _content_type="application/strategic-merge-patch+json",
    )
    return {
        "deployment": settings.app_deployment_name,
        "namespace": settings.app_namespace,
        "container": settings.app_container_name,
        "previous_memory_limit": str(current_limit),
        "target_memory_limit": target_limit,
        "patched_at": now,
    }


def probe_application(timeout_seconds: float = 3.0) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    try:
        with httpx.Client(timeout=timeout_seconds) as http:
            response = http.get(settings.app_probe_url)
        return {
            "observed_at": started,
            "ok": 200 <= response.status_code < 400,
            "status_code": response.status_code,
            "error": None,
        }
    except Exception as exc:
        return {
            "observed_at": started,
            "ok": False,
            "status_code": None,
            "error": str(exc),
        }


def _event_time(event) -> datetime:
    value = (
        getattr(event, "event_time", None)
        or getattr(event, "last_timestamp", None)
        or getattr(event, "first_timestamp", None)
        or event.metadata.creation_timestamp
    )
    return value or datetime.now(timezone.utc)


def list_namespace_events() -> list[dict[str, Any]]:
    events = core_api().list_namespaced_event(namespace=settings.app_namespace).items
    output = []
    for e in events:
        t = _event_time(e)
        output.append(
            {
                "event_uid": e.metadata.uid,
                "first_seen": e.first_timestamp or e.metadata.creation_timestamp or t,
                "last_seen": e.last_timestamp or t,
                "event_type": e.type,
                "reason": e.reason,
                "object_kind": e.involved_object.kind,
                "object_name": e.involved_object.name,
                "message": e.message,
                "event_count": e.count or 1,
                "raw": e.to_dict(),
            }
        )
    return output


def dependency_state() -> dict[str, Any]:
    """Return Kubernetes-level state for PostgreSQL without reading user-agent data."""
    pods = core_api().list_namespaced_pod(
        namespace=settings.app_namespace,
        label_selector="app=postgres",
    ).items
    output = []
    for pod in pods:
        conditions = pod.status.conditions or []
        ready = any(c.type == "Ready" and c.status == "True" for c in conditions)
        statuses = pod.status.container_statuses or []
        output.append(
            {
                "pod_name": pod.metadata.name,
                "phase": pod.status.phase,
                "ready": ready,
                "restart_count": sum(cs.restart_count or 0 for cs in statuses),
                "node_name": pod.spec.node_name,
            }
        )
    return {
        "dependency": "postgresql",
        "pods": output,
        "healthy": bool(output) and all(p["phase"] == "Running" and p["ready"] for p in output),
    }


def current_cluster_state() -> dict[str, Any]:
    return {
        "namespace": settings.app_namespace,
        "deployment": deployment_state(),
        "deployment_history": deployment_history()[:5],
        "pods": list_app_pods(),
        "dependency_state": dependency_state(),
    }


def current_app_logs(tail_lines: int = 100) -> dict[str, Any]:
    tail_lines = max(10, min(tail_lines, 500))
    api = core_api()
    logs: dict[str, Any] = {}
    for pod in list_app_pods():
        item: dict[str, Any] = {"current": None, "previous": None}
        try:
            item["current"] = api.read_namespaced_pod_log(
                name=pod["pod_name"],
                namespace=settings.app_namespace,
                container=settings.app_container_name,
                tail_lines=tail_lines,
                timestamps=True,
            )
        except Exception as exc:
            item["current"] = f"Unable to read current logs: {exc}"
        if int(pod.get("restart_count") or 0) > 0:
            try:
                item["previous"] = api.read_namespaced_pod_log(
                    name=pod["pod_name"],
                    namespace=settings.app_namespace,
                    container=settings.app_container_name,
                    previous=True,
                    tail_lines=tail_lines,
                    timestamps=True,
                )
            except Exception as exc:
                item["previous"] = f"Unable to read previous logs: {exc}"
        logs[pod["pod_name"]] = item
    return logs
