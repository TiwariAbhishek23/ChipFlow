"""FastAPI entry point for ChipFlow Agent."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from backend.agents.graph import ChipFlowWorkflow
from backend.models import RunRequest, RunSnapshot, state_to_snapshot
from backend.storage import RunStore
from backend.tools.knowledge import KnowledgeQueryInput, query_knowledge_graph


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
WORK_DIR = BASE_DIR / "workdir"

workflow = ChipFlowWorkflow(data_dir=DATA_DIR, work_dir=WORK_DIR)
store = RunStore(DATA_DIR / "runs")

app = FastAPI(
    title="ChipFlow Agent API",
    version="0.1.0",
    description="Long-horizon multi-agent RTL design, verification, synthesis, and knowledge graph demo.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class KnowledgeQueryResponse(BaseModel):
    nodes: list[dict] = Field(default_factory=list)
    edges: list[dict] = Field(default_factory=list)


class CypherReadQuery(BaseModel):
    query: str
    limit: int = Field(default=50, ge=1, le=200)


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": "chipflow-agent",
        "ai_runtime": workflow.model_status().model_dump(mode="json"),
        "knowledge_graph": workflow.kg.backend_status(),
    }


@app.get("/api/model")
async def model_status() -> dict:
    return workflow.model_status().model_dump(mode="json")


@app.get("/api/rules")
async def rules() -> list[dict]:
    return [rule.model_dump(mode="json") for rule in workflow.kg.get_design_rules()]


@app.post("/api/knowledge/query")
async def knowledge_query(payload: KnowledgeQueryInput) -> dict:
    snapshot = query_knowledge_graph(workflow.kg, payload)
    return snapshot.model_dump(mode="json")


@app.get("/api/knowledge/status")
async def knowledge_status() -> dict:
    return workflow.kg.backend_status()


@app.get("/api/knowledge/neo4j")
async def neo4j_snapshot(limit: int = 200) -> dict:
    snapshot = workflow.kg.neo4j_snapshot(limit)
    if snapshot is None:
        return {
            "status": workflow.kg.backend_status(),
            "nodes": [],
            "edges": [],
        }
    return {
        "status": workflow.kg.backend_status(),
        **snapshot.model_dump(mode="json"),
    }


@app.post("/api/knowledge/neo4j/cypher")
async def neo4j_cypher(payload: CypherReadQuery) -> dict:
    try:
        rows = workflow.kg.run_neo4j_read_query(payload.query, payload.limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"rows": rows, "status": workflow.kg.backend_status()}


@app.post("/api/runs", response_model=RunSnapshot)
async def create_run(payload: RunRequest) -> RunSnapshot:
    run_id = uuid.uuid4().hex
    state = workflow.initial_state(
        run_id=run_id,
        specification=payload.specification,
        max_iterations=payload.max_iterations,
        seed_demo_bug=payload.seed_demo_bug,
    )
    latest = state_to_snapshot(state)
    async for state in workflow.run_stream(state):
        latest = state_to_snapshot(state)
        store.save(latest)
    store.save(latest)
    return latest


@app.post("/api/runs/stream")
async def stream_run(payload: RunRequest) -> StreamingResponse:
    run_id = uuid.uuid4().hex
    initial = workflow.initial_state(
        run_id=run_id,
        specification=payload.specification,
        max_iterations=payload.max_iterations,
        seed_demo_bug=payload.seed_demo_bug,
    )

    async def event_stream() -> AsyncIterator[str]:
        yield _sse("run_created", {"run_id": run_id, "status": "running"})
        latest = state_to_snapshot(initial)
        try:
            async for state in workflow.run_stream(initial):
                latest = state_to_snapshot(state)
                store.save(latest)
                yield _sse("snapshot", latest.model_dump(mode="json"))
            store.save(latest)
            yield _sse("done", latest.model_dump(mode="json"))
        except Exception as exc:  # pragma: no cover - network stream path
            yield _sse("error", {"run_id": run_id, "error": str(exc)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/runs/{run_id}", response_model=RunSnapshot)
async def get_run(run_id: str) -> RunSnapshot:
    try:
        return store.load(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc


@app.get("/api/runs/{run_id}/export")
async def export_run(run_id: str) -> FileResponse:
    try:
        snapshot = store.load(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc
    path = store.export_zip(snapshot, DATA_DIR / "exports" / f"{run_id}.zip")
    return FileResponse(path, filename=f"chipflow-{run_id}.zip", media_type="application/zip")


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"
