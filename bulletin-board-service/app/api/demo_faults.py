from fastapi import APIRouter, HTTPException, status

from app.config import get_settings
from app.demo_faults import DB_LEAK_FAULT, MEMORY_GROWTH_FAULT

router = APIRouter(prefix="/demo/faults", include_in_schema=False)
settings = get_settings()


def ensure_enabled() -> None:
    if not settings.demo_faults_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


@router.get("")
def get_fault_status() -> dict:
    ensure_enabled()
    return {
        "available": True,
        "db_leak": DB_LEAK_FAULT.status(),
        "memory_growth": MEMORY_GROWTH_FAULT.status(),
    }


@router.post("/db-leak/start")
def start_db_leak() -> dict:
    ensure_enabled()
    return {"available": True, "db_leak": DB_LEAK_FAULT.start()}


@router.post("/db-leak/stop")
def stop_db_leak() -> dict:
    ensure_enabled()
    return {"available": True, "db_leak": DB_LEAK_FAULT.stop()}


@router.post("/memory-growth/start")
def start_memory_growth() -> dict:
    ensure_enabled()
    return {"available": True, "memory_growth": MEMORY_GROWTH_FAULT.start()}


@router.post("/memory-growth/stop")
def stop_memory_growth() -> dict:
    ensure_enabled()
    return {"available": True, "memory_growth": MEMORY_GROWTH_FAULT.stop()}
