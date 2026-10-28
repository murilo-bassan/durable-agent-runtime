"""Chaos drill: kill a worker process mid-flight and prove no run is lost or duplicated.

    python scripts/chaos.py            # needs Postgres (docker compose up -d db)

1. enqueue N runs (auto-approved, so they run to completion)
2. start worker A (a real OS process), SIGKILL it after a moment
3. start worker B, wait until every run is terminal
4. assert: all succeeded, every purchase order created exactly once
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from runtime import agents, approvals, budget, db, runs, service  # noqa: E402

N = int(os.environ.get("CHAOS_RUNS", "24"))
ENV = {**os.environ, "WORKER_SLOTS": "4", "WORKER_STALE_AFTER_S": "3", "WORKER_HEARTBEAT_INTERVAL_S": "0.5",
       "WORKER_POLL_INTERVAL_S": "0.05", "PYTHONPATH": str(ROOT)}


def worker() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "runtime.worker"], cwd=ROOT, env=ENV,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def approve_all() -> None:
    for a in approvals.pending():
        approvals.decide(str(a["id"]), True, "chaos-bot")


def main() -> None:
    db.migrate()
    agents.seed_prompts()
    budget.set_budget("chaos", 10**8, max_concurrent=100)
    ids = [str(service.submit("chaos", "procurement", {"item": "laptops", "quantity": i + 1, "max_unit_price": 9000})[0]["id"])
           for i in range(N)]
    print(f"enqueued {N} runs")

    a = worker()
    time.sleep(1.5)
    approve_all()
    time.sleep(0.4)
    a.kill()                                            # SIGKILL on POSIX, TerminateProcess on Windows
    a.wait()
    in_flight = [r for r in ids if runs.get(r)["status"] == "running"]
    print(f"worker A killed with {len(in_flight)} runs in flight")

    b = worker()
    deadline = time.time() + 90
    try:
        while time.time() < deadline:
            approve_all()
            statuses = [runs.get(r)["status"] for r in ids]
            if all(s in runs.TERMINAL for s in statuses):
                break
            time.sleep(0.3)
    finally:
        b.terminate()
        b.wait()

    with db.tx() as cur:
        cur.execute("SELECT count(*) AS n FROM side_effects WHERE effect_key LIKE ANY(%s)",
                    ([f"{r}:%" for r in ids],))
        orders = cur.fetchone()["n"]
    final = [runs.get(r) for r in ids]
    ok = sum(1 for r in final if r["status"] == "succeeded")
    retried = sum(1 for r in final if r["attempts"] > 1)
    print(f"succeeded {ok}/{N} · runs that needed a retry: {retried} · purchase orders: {orders}")
    assert ok == N, "some runs did not finish"
    assert orders == N, "a purchase order was lost or duplicated"
    print("chaos drill passed ✓")


if __name__ == "__main__":
    try:
        main()
    finally:
        db.close()
