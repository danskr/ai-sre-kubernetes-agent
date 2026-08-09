import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from . import db
from .graph import graph
from .observer import Observer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)
observer = Observer()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    observer.start()
    logger.info("SRE agent service started")
    try:
        yield
    finally:
        observer.stop()
        logger.info("SRE agent service stopped")


app = FastAPI(title="SRE Agent", version="0.5.1", lifespan=lifespan)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    thread_id: str = Field(default="default", min_length=1, max_length=200)


@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc)}


@app.get("/incidents")
def incidents(hours: int = Query(default=12, ge=1, le=168)):
    return {"hours": hours, "incidents": db.get_incidents(hours)}


@app.get("/incidents/{incident_id}")
def incident(incident_id: str):
    value = db.get_incident(incident_id)
    if value is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return value


@app.get("/events")
def events(hours: int = Query(default=12, ge=1, le=168)):
    return {"hours": hours, "events": db.get_events(hours)}


@app.get("/probe-history")
def probe_history(hours: int = Query(default=12, ge=1, le=168)):
    return {"hours": hours, "samples": db.get_health(hours)}


@app.get("/pod-history")
def pod_history(hours: int = Query(default=12, ge=1, le=168)):
    return {"hours": hours, "pods": db.get_pod_history(hours)}


@app.get("/deployment-history")
def deployment_history(hours: int = Query(default=12, ge=1, le=168)):
    return {"hours": hours, "snapshots": db.get_deployment_snapshots(hours)}


@app.get("/remediations")
def remediations(hours: int = Query(default=12, ge=1, le=168)):
    return {"hours": hours, "remediations": db.get_remediations(hours)}


@app.get("/approvals")
def approvals(hours: int = Query(default=12, ge=1, le=168)):
    return {"hours": hours, "approvals": db.get_approvals(hours)}


@app.post("/chat")
def chat(request: ChatRequest):
    config = {"configurable": {"thread_id": request.thread_id}}
    result = graph.invoke({"messages": [HumanMessage(content=request.message)]}, config=config)
    answer = result["messages"][-1].content
    return {"thread_id": request.thread_id, "answer": answer}
