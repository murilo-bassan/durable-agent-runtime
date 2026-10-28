import time

import pytest
from fastapi.testclient import TestClient

from runtime import approvals, runs
from runtime.api import app
from runtime.llm import ScriptedPlanner
from runtime.worker import Worker

GOAL = {"agent": "procurement", "input": {"item": "monitors", "quantity": 4, "max_unit_price": 2000}}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def wait_for(predicate, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if (value := predicate()):
            return value
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def test_idempotent_post(client):
    a = client.post("/v1/runs", json=GOAL, headers={"Idempotency-Key": "abc", "X-Owner": "ana"})
    b = client.post("/v1/runs", json=GOAL, headers={"Idempotency-Key": "abc", "X-Owner": "ana"})
    assert a.status_code == 202 and b.status_code == 200
    assert b.headers["Idempotent-Replayed"] == "true" and a.json()["id"] == b.json()["id"]
    other = {**GOAL, "input": {**GOAL["input"], "quantity": 5}}
    assert client.post("/v1/runs", json=other, headers={"Idempotency-Key": "abc", "X-Owner": "ana"}).status_code == 409


def test_invalid_input_is_422(client):
    r = client.post("/v1/runs", json={"agent": "procurement", "input": {"item": "x"}})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "invalid_input"


def test_end_to_end_with_real_worker(client):
    worker = Worker(llm=ScriptedPlanner(), slots=2)
    worker.start()
    try:
        run_id = client.post("/v1/runs", json=GOAL).json()["id"]
        approval = wait_for(lambda: next(iter(client.get("/v1/approvals").json()), None))
        assert client.get(f"/v1/runs/{run_id}").json()["status"] == "waiting_approval"
        r = client.post(f"/v1/approvals/{approval['id']}", json={"approve": True, "decided_by": "manager"})
        assert r.status_code == 200
        final = wait_for(lambda: (x := client.get(f"/v1/runs/{run_id}").json())["status"] == "succeeded" and x)
        assert "PO-" in final["result"]["answer"]
        metrics = client.get("/v1/metrics").json()
        assert metrics["by_status"]["succeeded"] == 1 and metrics["tokens_24h"] > 0
    finally:
        worker.stop.set()
        worker.join(5)


def test_worker_recovers_a_dead_peer(client):
    """A run claimed by a worker that never beats is picked up by a live worker."""
    run_id = client.post("/v1/runs", json=GOAL).json()["id"]
    ghost = runs.claim_next("ghost-worker/0")
    assert ghost.id == run_id
    worker = Worker(llm=ScriptedPlanner(), slots=1)
    worker.start()
    try:
        wait_for(lambda: approvals.pending(), timeout=20)
        assert client.get(f"/v1/runs/{run_id}").json()["attempts"] == 2
    finally:
        worker.stop.set()
        worker.join(5)


def test_sse_stream_ends_at_terminal_state(client):
    run_id = client.post("/v1/runs", json=GOAL).json()["id"]
    client.post(f"/v1/runs/{run_id}/cancel")
    with client.stream("GET", f"/v1/runs/{run_id}/events") as stream:
        body = "".join(stream.iter_text())
    assert "event: status" in body and "cancelled" in body
