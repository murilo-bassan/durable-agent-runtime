"""Drives a running API through the interesting paths: approve, reject, cancel, budget.

    RUNTIME_EMBEDDED_WORKER=true uvicorn runtime.api:app --port 8700   # terminal 1
    python scripts/demo.py http://localhost:8700                       # terminal 2
"""
from __future__ import annotations

import sys
import time
import uuid

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8700"
c = httpx.Client(base_url=BASE, timeout=10, headers={"X-Owner": "ana"})


def submit(item: str, qty: int, max_price: float) -> str:
    r = c.post("/v1/runs", json={"agent": "procurement", "input": {"item": item, "quantity": qty, "max_unit_price": max_price}},
               headers={"Idempotency-Key": str(uuid.uuid4())})
    r.raise_for_status()
    return r.json()["id"]


def approval_for(run_id: str, timeout: float = 20) -> dict | None:
    end = time.time() + timeout
    while time.time() < end:
        for a in c.get("/v1/approvals").json():
            if a["run_id"] == run_id:
                return a
        time.sleep(0.3)
    return None


def main() -> None:
    approved = submit("laptops", 10, 5000)
    rejected = submit("office chairs", 25, 1000)
    no_budget = submit("monitors", 3, 500)        # no quote fits → polite answer
    waiting = submit("desks", 6, 1600)            # left waiting for the operator
    cancelled = submit("keyboards", 50, 200)

    c.post(f"/v1/runs/{cancelled}/cancel")
    if a := approval_for(approved):
        c.post(f"/v1/approvals/{a['id']}", json={"approve": True, "decided_by": "maria.ops"})
    for _ in range(2):
        if a := approval_for(rejected):
            c.post(f"/v1/approvals/{a['id']}", json={"approve": False, "decided_by": "maria.ops", "note": "vendor on hold"})
    approval_for(waiting)
    print({"approved": approved, "rejected": rejected, "no_budget": no_budget, "waiting": waiting, "cancelled": cancelled})


if __name__ == "__main__":
    main()
