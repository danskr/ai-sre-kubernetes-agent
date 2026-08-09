import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx

from . import db
from .config import settings
from .k8s_client import deployment_history, deployment_state, list_app_pods, list_namespace_events
from .workflow_client import run_incident_workflow

logger = logging.getLogger(__name__)


class Observer:
    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.previous_pods: dict[str, dict] | None = None
        self.previous_revision: int | None = None
        self.consecutive_probe_failures = 0
        self.workflow_lock = threading.Lock()
        self.workflows_in_progress: set[str] = set()
        self.remediation_in_progress = False
        self.cooldown_until: datetime | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, daemon=True, name="k8s-observer")
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)

    def _probe(self) -> dict:
        started = time.perf_counter()
        try:
            response = httpx.get(settings.app_probe_url, timeout=3.0)
            latency_ms = (time.perf_counter() - started) * 1000
            return {
                "observed_at": datetime.now(timezone.utc),
                "ok": 200 <= response.status_code < 400,
                "status_code": response.status_code,
                "latency_ms": round(latency_ms, 2),
                "error": None,
            }
        except Exception as exc:
            return {
                "observed_at": datetime.now(timezone.utc),
                "ok": False,
                "status_code": None,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "error": str(exc),
            }

    def _launch_incident_workflow(
        self,
        incident_id: str,
        incident_kind: str,
        *,
        consecutive_probe_failures: int | None = None,
    ) -> None:
        with self.workflow_lock:
            if incident_id in self.workflows_in_progress:
                return
            self.workflows_in_progress.add(incident_id)
            if incident_kind == "runtime_regression":
                if self.remediation_in_progress:
                    self.workflows_in_progress.discard(incident_id)
                    return
                self.remediation_in_progress = True

        def worker() -> None:
            try:
                run_incident_workflow(
                    incident_id,
                    incident_kind,
                    consecutive_probe_failures=consecutive_probe_failures,
                )
            except Exception as exc:
                logger.exception("Unified incident workflow failed incident=%s kind=%s", incident_id, incident_kind)
                db.update_incident(
                    incident_id,
                    status="needs_human_review",
                    summary="Unified SRE workflow failed and requires human review.",
                    details={"workflow_error": str(exc)},
                )
            finally:
                with self.workflow_lock:
                    self.workflows_in_progress.discard(incident_id)
                    if incident_kind == "runtime_regression":
                        self.remediation_in_progress = False
                        self.cooldown_until = datetime.now(timezone.utc) + timedelta(
                            seconds=settings.auto_remediation_cooldown_seconds
                        )

        threading.Thread(
            target=worker,
            daemon=True,
            name=f"workflow-{incident_kind}-{incident_id[:8]}",
        ).start()

    def _handle_oom_restart(self, pod: dict, previous_restart_count: int) -> None:
        restart_count = int(pod.get("restart_count") or 0)
        if restart_count <= previous_restart_count:
            return
        if pod.get("last_termination_reason") != "OOMKilled":
            return

        observation = {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "pod_name": pod.get("pod_name"),
            "pod_uid": pod.get("pod_uid"),
            "restart_count": restart_count,
            "previous_restart_count": previous_restart_count,
            "last_termination_reason": pod.get("last_termination_reason"),
            "last_exit_code": pod.get("last_exit_code"),
            "last_finished_at": (
                pod.get("last_finished_at").isoformat()
                if hasattr(pod.get("last_finished_at"), "isoformat")
                else pod.get("last_finished_at")
            ),
            "container_statuses": pod.get("container_statuses", []),
        }

        active = db.get_active_incident("resource_oom")
        if active is None:
            incident_id = str(uuid.uuid4())
            observations = [observation]
            db.create_incident(
                {
                    "incident_id": incident_id,
                    "kind": "resource_oom",
                    "status": "open",
                    "started_at": datetime.now(timezone.utc),
                    "summary": (
                        "Application container was OOMKilled and restarted by Kubernetes. "
                        "Resource incidents are not eligible for autonomous remediation."
                    ),
                    "details": {
                        "oom_observations": observations,
                        "latest_pod_state": pod,
                        "automatic_action_allowed": False,
                    },
                }
            )
            logger.error(
                "Opened resource OOM incident=%s pod=%s restart_count=%s",
                incident_id,
                pod.get("pod_name"),
                restart_count,
            )
        else:
            incident_id = active["incident_id"]
            details = active.get("details") or {}
            observations = list(details.get("oom_observations") or [])
            key = (observation.get("pod_uid"), observation.get("restart_count"))
            existing = {(o.get("pod_uid"), o.get("restart_count")) for o in observations}
            if key not in existing:
                observations.append(observation)
            observations = observations[-20:]
            db.update_incident(
                incident_id,
                summary=(
                    f"Repeated OOMKilled restarts observed (restart_count={restart_count}); "
                    "Kubernetes restart is not providing durable recovery."
                ),
                details={
                    "oom_observations": observations,
                    "latest_pod_state": pod,
                    "automatic_action_allowed": False,
                },
            )
            logger.error(
                "Updated resource OOM incident=%s pod=%s restart_count=%s",
                incident_id,
                pod.get("pod_name"),
                restart_count,
            )

        refreshed = db.get_incident(incident_id) or {}
        refreshed_details = refreshed.get("details") or {}
        already_triaged = bool(refreshed_details.get("resource_diagnosis"))
        if restart_count >= settings.resource_oom_restart_threshold and not already_triaged:
            self._launch_incident_workflow(incident_id, "resource_oom")

    def _maybe_open_regression_incident(self, dep: dict, health: dict) -> None:
        if health["ok"]:
            self.consecutive_probe_failures = 0
            return

        self.consecutive_probe_failures += 1
        # Scenario 3 resource-pressure incidents have an explicit human-approval
        # boundary.  Never let the Scenario 2 recent-release rollback path race an
        # observed OOMKilled incident.
        if db.get_active_incident("resource_oom") is not None:
            return
        if not settings.auto_remediation_enabled:
            return
        if self.consecutive_probe_failures < settings.auto_remediation_failure_threshold:
            return
        if self.remediation_in_progress:
            return
        if self.cooldown_until and datetime.now(timezone.utc) < self.cooldown_until:
            return
        if db.has_active_regression_incident(dep.get("revision")):
            return

        # Scenario 2 is specifically a post-deployment regression. Do not turn
        # unrelated transient failures (including Scenario 1 pod loss) into
        # automatic-rollback incidents.
        history = deployment_history()
        if len(history) < 2:
            return
        current = history[0]
        created_at = None
        if dep.get("revision") is not None:
            created_at = db.get_revision_first_observed_at(int(dep["revision"]))
        if created_at is None:
            created_at = current.get("created_at")
        if not created_at:
            return
        release_age_seconds = (datetime.now(timezone.utc) - created_at).total_seconds()
        if release_age_seconds > settings.auto_remediation_recent_deployment_seconds:
            return
        if current.get("images") == history[1].get("images"):
            return

        incident_id = str(uuid.uuid4())
        db.create_incident(
            {
                "incident_id": incident_id,
                "kind": "runtime_regression",
                "status": "open",
                "started_at": datetime.now(timezone.utc),
                "summary": "Application readiness is failing; evaluating whether a recent Deployment revision caused a runtime regression.",
                "details": {
                    "deployment_state_at_detection": dep,
                    "consecutive_probe_failures": self.consecutive_probe_failures,
                    "latest_probe": health,
                },
            }
        )
        logger.error(
            "Opened runtime regression incident=%s failures=%s revision=%s",
            incident_id,
            self.consecutive_probe_failures,
            dep.get("revision"),
        )
        self._launch_incident_workflow(
            incident_id,
            "runtime_regression",
            consecutive_probe_failures=self.consecutive_probe_failures,
        )

    def _run_once(self) -> None:
        dep = deployment_state()
        dep_snapshot = {
            "observed_at": datetime.now(timezone.utc),
            **dep,
        }
        db.insert_deployment_snapshot(dep_snapshot)

        revision_changed = (
            self.previous_revision is not None
            and dep.get("revision") is not None
            and dep.get("revision") != self.previous_revision
        )
        if revision_changed:
            logger.info(
                "Deployment revision changed old=%s new=%s images=%s",
                self.previous_revision,
                dep.get("revision"),
                dep.get("images"),
            )

        pods = list_app_pods()
        pod_map = {p["pod_uid"]: p for p in pods}
        for pod in pods:
            db.insert_pod_snapshot(pod)
            previous_restart_count = 0
            if self.previous_pods is not None and pod["pod_uid"] in self.previous_pods:
                previous_restart_count = int(
                    self.previous_pods[pod["pod_uid"]].get("restart_count") or 0
                )
            self._handle_oom_restart(pod, previous_restart_count)

        for event in list_namespace_events():
            db.upsert_k8s_event(event)

        health = self._probe()
        db.insert_health_sample(health)

        # Scenario 1: a genuinely missing Pod is an incident, but a Deployment rollout
        # deliberately scales the old ReplicaSet to zero. That Pod may disappear one or
        # more observer cycles after the revision changes, so checking only
        # ``revision_changed`` is insufficient. Suppress disappearance incidents when
        # the lost Pod's owning ReplicaSet is currently desired at zero.
        if self.previous_pods is not None:
            rs_desired = {
                item.get("replicaset"): int(item.get("desired_replicas") or 0)
                for item in deployment_history()
                if item.get("replicaset")
            }
            lost_uids = set(self.previous_pods) - set(pod_map)
            for uid in lost_uids:
                old = self.previous_pods[uid]
                owner_rs = old.get("owner_replicaset")
                rollout_scaled_down = owner_rs is not None and rs_desired.get(owner_rs) == 0
                if revision_changed or rollout_scaled_down:
                    logger.info(
                        "Suppressed expected rollout pod removal pod=%s owner_rs=%s revision_changed=%s",
                        old.get("pod_name"),
                        owner_rs,
                        revision_changed,
                    )
                    continue

                incident_id = str(uuid.uuid4())
                db.create_incident(
                    {
                        "incident_id": incident_id,
                        "kind": "pod_disappearance",
                        "status": "open",
                        "started_at": datetime.now(timezone.utc),
                        "summary": f"Application pod {old['pod_name']} disappeared from the cluster.",
                        "details": {
                            "lost_pod_name": old["pod_name"],
                            "lost_pod_uid": uid,
                            "owner_replicaset": owner_rs,
                            "previous_phase": old.get("phase"),
                            "deployment_revision": dep.get("revision"),
                        },
                    }
                )
                logger.warning("Opened incident %s for lost pod %s", incident_id, old["pod_name"])
                self._launch_incident_workflow(incident_id, "pod_disappearance")

        # Scenario 1 recovery verification is performed inside the same unified
        # LangGraph workflow submitted when the disappearance incident is opened.

        self._maybe_open_regression_incident(dep, health)
        self.previous_pods = pod_map
        self.previous_revision = dep.get("revision")

    def _run(self) -> None:
        logger.info(
            "Observer started namespace=%s selector=%s deployment=%s interval=%ss auto_remediation=%s",
            settings.app_namespace,
            settings.app_label_selector,
            settings.app_deployment_name,
            settings.observe_interval_seconds,
            settings.auto_remediation_enabled,
        )
        while not self.stop_event.is_set():
            try:
                self._run_once()
            except Exception:
                logger.exception("Observer cycle failed")
            self.stop_event.wait(settings.observe_interval_seconds)
