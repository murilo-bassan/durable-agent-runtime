"""HTTP API for submitting, watching, approving and cancelling agent runs.

    uvicorn runtime.api:app --port 8700
    RUNTIME_EMBEDDED_WORKER=true uvicorn runtime.api:app   # single-process demo

Identity is a plain `X-Owner` header here — authentication is the gateway's
job (see the mcp-tool-gateway repo); this service focuses on execution.
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from runtime import agents, approvals, budget, db, prompts, runs, service
from runtime.worker import Worker

STATIC = Path(__file__).resolve().parents[1] / "web" / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.migrate()
    agents.seed_prompts()
    worker = None
    if os.environ.get("RUNTIME_EMBEDDED_WORKER", "").lower() in {"1", "true", "yes"}:
        worker = Worker()
        worker.start()
    yield
    if worker:
        worker.stop.set()
        worker.join(timeout=10)


app = FastAPI(title="durable-agent-runtime", version="0.1.0", lifespan=lifespan, docs_url="/docs")


def _jsonable(row: dict[str, Any] | None) -> dict[str, Any] | None:
    return json.loads(json.dumps(row, default=str)) if row is not None else None


class RunRequest(BaseModel):
    agent: str = "procurement"
    input: dict[str, Any]
    model: str | None = None


@app.post("/v1/runs", status_code=202)
def create_run(body: RunRequest, response: Response, x_owner: str = Header(default="demo"),
               idempotency_key: str | None = Header(default=None)) -> dict[str, Any]:
    try:
        run, created = service.submit(x_owner, body.agent, body.input, idempotency_key, body.model)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except service.InvalidInput as e:
        raise HTTPException(422, {"error": "invalid_input", "details": e.errors})
    except runs.IdempotencyConflict as e:
        raise HTTPException(409, {"error": "idempotency_conflict", "message": str(e)})
    if not created:
        response.status_code = 200
        response.headers["Idempotent-Replayed"] = "true"
    return _jsonable(run)


@app.get("/v1/runs")
def list_runs(owner: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    return [_jsonable({k: v for k, v in r.items() if k != "transcript"}) for r in runs.list_runs(owner, min(limit, 200))]


@app.get("/v1/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    run = runs.get(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return _jsonable(run)


@app.get("/v1/runs/{run_id}/steps")
def get_steps(run_id: str) -> list[dict[str, Any]]:
    return [_jsonable(s) for s in runs.steps(run_id)]


@app.post("/v1/runs/{run_id}/cancel")
def cancel(run_id: str) -> dict[str, str]:
    try:
        return {"id": run_id, "status": runs.request_cancel(run_id)}
    except KeyError:
        raise HTTPException(404, "run not found")


@app.get("/v1/runs/{run_id}/events")
async def events(run_id: str) -> StreamingResponse:
    """Server-Sent Events: one `status` event per change and one `step` event per new step, until terminal."""
    async def stream():
        seen_steps, last_status = 0, None
        while True:
            run = await asyncio.to_thread(runs.get, run_id)
            if run is None:
                yield "event: error\ndata: {\"error\": \"not_found\"}\n\n"
                return
            steps = await asyncio.to_thread(runs.steps, run_id)
            for s in steps[seen_steps:]:
                yield f"event: step\ndata: {json.dumps(_jsonable(s))}\n\n"
            seen_steps = len(steps)
            if run["status"] != last_status:
                last_status = run["status"]
                yield f"event: status\ndata: {json.dumps({'status': last_status})}\n\n"
            if last_status in runs.TERMINAL:
                return
            await asyncio.sleep(0.5)
    return StreamingResponse(stream(), media_type="text/event-stream")


class Decision(BaseModel):
    approve: bool
    decided_by: str = Field(min_length=1)
    note: str = ""


@app.get("/v1/approvals")
def list_approvals() -> list[dict[str, Any]]:
    return [_jsonable(a) for a in approvals.pending()]


@app.post("/v1/approvals/{approval_id}")
def decide(approval_id: str, body: Decision) -> dict[str, Any]:
    try:
        return _jsonable(approvals.decide(approval_id, body.approve, body.decided_by, body.note))
    except LookupError as e:
        raise HTTPException(409, str(e))


class PromptBody(BaseModel):
    template: str = Field(min_length=10)
    author: str
    activate: bool = True


@app.get("/v1/prompts/{name}")
def prompt_versions(name: str) -> list[dict[str, Any]]:
    return [p.__dict__ for p in prompts.versions(name)]


@app.post("/v1/prompts/{name}")
def publish_prompt(name: str, body: PromptBody) -> dict[str, Any]:
    return prompts.publish(name, body.template, body.author, body.activate).__dict__


@app.get("/v1/budget/{owner}")
def get_budget(owner: str) -> dict[str, Any]:
    return {"owner": owner, "remaining_tokens_24h": budget.remaining(owner)}


@app.get("/v1/metrics")
def metrics() -> dict[str, Any]:
    return runs.metrics()


@app.get("/health")
def health() -> dict[str, str]:
    with db.tx() as cur:
        cur.execute("SELECT 1")
    return {"status": "ok"}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")
