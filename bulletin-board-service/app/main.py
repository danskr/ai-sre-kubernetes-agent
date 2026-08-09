import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.demo_faults import router as demo_faults_router
from app.api.health import router as health_router
from app.api.messages import router as messages_router
from app.config import get_settings
from app.database import Base, engine, wait_for_database
from app.demo_faults import MEMORY_GROWTH_FAULT

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(settings.app_name)


@asynccontextmanager
async def lifespan(_: FastAPI):
    wait_for_database()
    Base.metadata.create_all(bind=engine)
    if settings.demo_faults_enabled:
        MEMORY_GROWTH_FAULT.configure(
            engine,
            chunk_mib=settings.memory_growth_chunk_mib,
            interval_seconds=settings.memory_growth_interval_seconds,
            start_delay_seconds=settings.memory_growth_start_delay_seconds,
        )
        MEMORY_GROWTH_FAULT.start_worker()
    logger.info(
        "service_started environment=%s demo_faults_enabled=%s",
        settings.environment,
        settings.demo_faults_enabled,
    )
    try:
        yield
    finally:
        if settings.demo_faults_enabled:
            MEMORY_GROWTH_FAULT.shutdown()
        logger.info("service_stopped")


app = FastAPI(
    title="Bulletin Board Service",
    description="A small JSON API used by the agent-assisted Kubernetes reliability project.",
    version="0.4.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    # Demo control endpoints deliberately do not appear in the application's
    # operational request log, so SRE diagnosis sees symptoms rather than the trigger.
    hide_from_operational_log = request.url.path.startswith("/demo/faults")
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    started = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception:
        if not hide_from_operational_log:
            logger.exception(
                "request_failed method=%s path=%s request_id=%s",
                request.method,
                request.url.path,
                request_id,
            )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "request_id": request_id},
            headers={"X-Request-ID": request_id},
        )

    duration_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Request-ID"] = request_id
    if not hide_from_operational_log:
        logger.info(
            "request_completed method=%s path=%s status=%s duration_ms=%.2f request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            request_id,
        )
    return response


app.include_router(health_router)
app.include_router(messages_router, prefix="/api/v1")
app.include_router(demo_faults_router)
