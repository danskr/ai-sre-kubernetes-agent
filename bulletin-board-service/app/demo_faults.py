import threading
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


class DbLeakFaultController:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._enabled = False
        self._activated_at: datetime | None = None
        self._started_monotonic: float | None = None
        self._leaked_sessions: list[Session] = []

    def start(self) -> dict:
        with self._lock:
            if not self._enabled:
                self._enabled = True
                self._activated_at = datetime.now(timezone.utc)
                self._started_monotonic = time.monotonic()
        return self.status()

    def stop(self) -> dict:
        with self._lock:
            self._enabled = False
            self._activated_at = None
            self._started_monotonic = None
            leaked = list(self._leaked_sessions)
            self._leaked_sessions.clear()

        for session in leaked:
            try:
                session.close()
            except Exception:
                pass
        return self.status()

    def should_leak(self) -> bool:
        with self._lock:
            return self._enabled

    def retain_session(self, session: Session) -> None:
        with self._lock:
            if self._enabled:
                self._leaked_sessions.append(session)
                return
        session.close()

    def extra_latency_seconds(self) -> float:
        with self._lock:
            if not self._enabled or self._started_monotonic is None:
                return 0.0
            elapsed = time.monotonic() - self._started_monotonic

        if elapsed < 3:
            return 0.0
        if elapsed < 6:
            return 0.10
        if elapsed < 9:
            return 0.25
        return 0.50

    def status(self) -> dict:
        with self._lock:
            enabled = self._enabled
            activated_at = self._activated_at
            started = self._started_monotonic
            leaked_count = len(self._leaked_sessions)

        elapsed = None if started is None else max(0.0, time.monotonic() - started)
        if not enabled:
            stage = "idle"
        elif elapsed is not None and elapsed < 3:
            stage = "starting"
        elif elapsed is not None and elapsed < 6:
            stage = "degrading"
        elif elapsed is not None and elapsed < 9:
            stage = "severe"
        else:
            stage = "pool-exhaustion"

        return {
            "enabled": enabled,
            "activated_at": activated_at.isoformat() if activated_at else None,
            "elapsed_seconds": round(elapsed, 3) if elapsed is not None else None,
            "leaked_sessions": leaked_count,
            "ramp_stage": stage,
        }


class MemoryGrowthFaultController:
    """Controlled memory-growth demo fault with durable enable/disable state.

    The enable flag is persisted in the bulletin application database.  This is
    intentional: when Linux/Kubernetes OOM-kills the application process, the new
    container process sees the still-enabled flag and resumes memory growth.  That
    produces a repeatable restart pattern instead of a one-shot crash.

    The allocation itself is process-local and deliberately produces no application
    log line that reveals the hidden demo trigger to sre-agent.
    """

    _TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS demo_fault_state (
            fault_name TEXT PRIMARY KEY,
            enabled BOOLEAN NOT NULL,
            activated_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL
        )
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._engine: Engine | None = None
        self._chunk_mib = 12
        self._interval_seconds = 1.5
        self._start_delay_seconds = 3.0
        self._allocations: list[bytearray] = []
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._enabled_seen = False
        self._enabled_since_monotonic: float | None = None

    def configure(
        self,
        engine: Engine,
        *,
        chunk_mib: int,
        interval_seconds: float,
        start_delay_seconds: float,
    ) -> None:
        self._engine = engine
        self._chunk_mib = max(1, min(int(chunk_mib), 64))
        self._interval_seconds = max(0.25, float(interval_seconds))
        self._start_delay_seconds = max(0.0, float(start_delay_seconds))
        with engine.begin() as connection:
            connection.execute(text(self._TABLE_SQL))
            connection.execute(
                text(
                    """
                    INSERT INTO demo_fault_state(fault_name, enabled, activated_at, updated_at)
                    VALUES ('memory_growth', false, NULL, now())
                    ON CONFLICT(fault_name) DO NOTHING
                    """
                )
            )

    def start_worker(self) -> None:
        if self._engine is None:
            raise RuntimeError("Memory-growth fault controller is not configured")
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._worker,
                daemon=True,
                name="demo-memory-growth",
            )
            self._thread.start()

    def shutdown(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        thread = self._thread
        if thread:
            thread.join(timeout=2)
        self._clear_allocations()

    def _row(self) -> dict[str, Any]:
        if self._engine is None:
            return {"enabled": False, "activated_at": None, "updated_at": None}
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT enabled, activated_at, updated_at
                    FROM demo_fault_state
                    WHERE fault_name='memory_growth'
                    """
                )
            ).mappings().first()
        return dict(row) if row else {"enabled": False, "activated_at": None, "updated_at": None}

    def _set_enabled(self, enabled: bool) -> None:
        if self._engine is None:
            raise RuntimeError("Memory-growth fault controller is not configured")
        now = datetime.now(timezone.utc)
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO demo_fault_state(fault_name, enabled, activated_at, updated_at)
                    VALUES ('memory_growth', :enabled, :activated_at, :updated_at)
                    ON CONFLICT(fault_name) DO UPDATE SET
                        enabled=EXCLUDED.enabled,
                        activated_at=EXCLUDED.activated_at,
                        updated_at=EXCLUDED.updated_at
                    """
                ),
                {
                    "enabled": enabled,
                    "activated_at": now if enabled else None,
                    "updated_at": now,
                },
            )
        self._wake_event.set()

    def start(self) -> dict:
        self._set_enabled(True)
        return self.status()

    def stop(self) -> dict:
        self._set_enabled(False)
        self._clear_allocations()
        return self.status()

    def _clear_allocations(self) -> None:
        with self._lock:
            self._allocations.clear()
            self._enabled_seen = False
            self._enabled_since_monotonic = None

    def _allocate_chunk(self) -> None:
        size = self._chunk_mib * 1024 * 1024
        chunk = bytearray(size)
        # Touch one byte on each page so the allocation becomes resident memory
        # instead of remaining only virtually allocated.
        for offset in range(0, size, 4096):
            chunk[offset] = 1
        with self._lock:
            self._allocations.append(chunk)

    def _worker(self) -> None:
        next_allocation_at: float | None = None
        while not self._stop_event.is_set():
            try:
                enabled = bool(self._row().get("enabled"))
            except Exception:
                enabled = False

            now = time.monotonic()
            if enabled:
                if not self._enabled_seen:
                    self._enabled_seen = True
                    self._enabled_since_monotonic = now
                    next_allocation_at = now + self._start_delay_seconds
                if next_allocation_at is not None and now >= next_allocation_at:
                    self._allocate_chunk()
                    next_allocation_at = time.monotonic() + self._interval_seconds
            else:
                if self._enabled_seen or self._allocations:
                    self._clear_allocations()
                next_allocation_at = None

            self._wake_event.wait(timeout=0.5 if enabled else 1.0)
            self._wake_event.clear()

    def status(self) -> dict:
        row = self._row()
        with self._lock:
            allocated_mib = len(self._allocations) * self._chunk_mib
            enabled_since = self._enabled_since_monotonic
        elapsed = None
        if enabled_since is not None:
            elapsed = max(0.0, time.monotonic() - enabled_since)
        activated_at = row.get("activated_at")
        updated_at = row.get("updated_at")
        return {
            "enabled": bool(row.get("enabled")),
            "activated_at": activated_at.isoformat() if activated_at else None,
            "updated_at": updated_at.isoformat() if updated_at else None,
            "process_elapsed_seconds": round(elapsed, 3) if elapsed is not None else None,
            "allocated_mib_in_current_process": allocated_mib,
            "chunk_mib": self._chunk_mib,
            "interval_seconds": self._interval_seconds,
            "start_delay_seconds": self._start_delay_seconds,
            "persists_across_container_restarts": True,
        }


DB_LEAK_FAULT = DbLeakFaultController()
MEMORY_GROWTH_FAULT = MemoryGrowthFaultController()
