"""Run lifecycle on Postgres: enqueue, claim, heartbeat, recover, finish.

    queued ──claim──► running ──► succeeded | failed | cancelled
      ▲                 │  │
      │   (write tool)  │  └─ heartbeat stops ─► recover_stale ─► queued (attempt+1) … ► dead
      └── decision ◄── waiting_approval

Delivery is **at-least-once**: a worker that dies mid-step leaves the run
`running` with a stale heartbeat; any other worker requeues it and the agent
resumes from the persisted transcript, re-doing only the interrupted step.
Steps with side effects are made idempotent with `effect_key = run:step`
(`tools.base.once`), so "at least once" never becomes "twice" where it matters.

Every write a worker does is conditioned on `worker_id = me AND status =
'running'` — a worker that lost its run (recovered by someone else) finds out
on its next write and stops, instead of fighting the new owner.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from runtime import budget, db


class IdempotencyConflict(Exception):
    """Same Idempotency-Key, different request body."""


class LostOwnership(Exception):
    """The run was recovered or cancelled under this worker."""


@dataclass(frozen=True)
class Claimed:
    id: str
    owner: str
    agent: str
    input: dict[str, Any]
    prompt_name: str
    prompt_version: int
    model: str
    max_steps: int
    transcript: list[dict[str, Any]]
    attempts: int


TERMINAL = ("succeeded", "failed", "cancelled", "dead")


def _sha(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def enqueue(owner: str, agent: str, input: dict[str, Any], *, prompt_name: str, prompt_version: int, model: str,
            max_steps: int, idempotency_key: str | None = None) -> tuple[dict[str, Any], bool]:
    """Creates a queued run. With an idempotency key, a retry returns the original run (created=False)."""
    request_sha = _sha({"agent": agent, "input": input})
    with db.tx() as cur:
        budget.ensure_owner(cur, owner)
        if idempotency_key:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"idem:{owner}:{idempotency_key}",))
            cur.execute("SELECT run_id, request_sha FROM idempotency_keys WHERE owner = %s AND key = %s",
                        (owner, idempotency_key))
            hit = cur.fetchone()
            if hit:
                if hit["request_sha"] != request_sha:
                    raise IdempotencyConflict("this Idempotency-Key was used with a different request")
                cur.execute("SELECT * FROM runs WHERE id = %s", (hit["run_id"],))
                return cur.fetchone(), False
        run_id = uuid.uuid4()
        cur.execute(
            """INSERT INTO runs (id, owner, agent, input, prompt_name, prompt_version, model, max_steps)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (run_id, owner, agent, db.json(input), prompt_name, prompt_version, model, max_steps))
        run = cur.fetchone()
        if idempotency_key:
            cur.execute("INSERT INTO idempotency_keys (owner, key, request_sha, run_id) VALUES (%s, %s, %s, %s)",
                        (owner, idempotency_key, request_sha, run_id))
        cur.execute("SELECT pg_notify('runs_queued', %s)", (str(run_id),))
    return run, True


# The per-owner concurrency cap is checked in the same statement that claims.
# It is a soft cap: two workers claiming at the same instant for the same owner
# may both pass. A hard cap would need a per-owner lock — see docs/adr/0002.
_CLAIM = """
WITH candidate AS (
    SELECT r.id FROM runs r
    JOIN budgets b ON b.owner = r.owner
    WHERE r.status = 'queued'
      AND (SELECT count(*) FROM runs x WHERE x.owner = r.owner AND x.status = 'running') < b.max_concurrent
    ORDER BY r.created_at
    FOR UPDATE OF r SKIP LOCKED
    LIMIT 1
)
UPDATE runs SET status = 'running', worker_id = %(worker)s, heartbeat_at = now(),
                started_at = COALESCE(started_at, now())
FROM candidate WHERE runs.id = candidate.id
RETURNING runs.*
"""


def claim_next(worker_id: str) -> Claimed | None:
    with db.tx() as cur:
        cur.execute(_CLAIM, {"worker": worker_id})
        r = cur.fetchone()
    if r is None:
        return None
    return Claimed(id=str(r["id"]), owner=r["owner"], agent=r["agent"], input=r["input"],
                   prompt_name=r["prompt_name"], prompt_version=r["prompt_version"], model=r["model"],
                   max_steps=r["max_steps"], transcript=r["transcript"], attempts=r["attempts"])


def heartbeat(worker_id: str, run_ids: list[str]) -> None:
    if not run_ids:
        return
    with db.tx() as cur:
        cur.execute("UPDATE runs SET heartbeat_at = now() WHERE worker_id = %s AND status = 'running' AND id = ANY(%s)",
                    (worker_id, run_ids))


def recover_stale(stale_after_s: float, max_attempts: int) -> dict[str, list[str]]:
    """Requeues runs whose worker stopped beating; past max_attempts they go `dead` (poison runs)."""
    with db.tx() as cur:
        cur.execute(
            """SELECT id, attempts FROM runs
               WHERE status = 'running' AND heartbeat_at < now() - make_interval(secs => %s)
               FOR UPDATE SKIP LOCKED""", (stale_after_s,))
        stale = cur.fetchall()
        requeued, dead = [], []
        for r in stale:
            if r["attempts"] >= max_attempts:
                cur.execute("""UPDATE runs SET status = 'dead', worker_id = NULL, finished_at = now(),
                               error = %s WHERE id = %s""",
                            (db.json({"reason": "max_attempts", "attempts": r["attempts"]}), r["id"]))
                dead.append(str(r["id"]))
            else:
                cur.execute("UPDATE runs SET status = 'queued', worker_id = NULL, attempts = attempts + 1 WHERE id = %s",
                            (r["id"],))
                requeued.append(str(r["id"]))
    return {"requeued": requeued, "dead": dead}


def _owned(cur, run_id: str, worker_id: str) -> None:
    if cur.rowcount == 0:
        raise LostOwnership(f"run {run_id} is no longer owned by {worker_id}")


def checkpoint(run_id: str, worker_id: str, transcript: list[dict[str, Any]], *, tokens_in: int = 0,
               tokens_out: int = 0, cost_usd: float = 0.0) -> bool:
    """Persists the transcript and usage. Returns whether a cancellation was requested."""
    with db.tx() as cur:
        cur.execute(
            """UPDATE runs SET transcript = %s, heartbeat_at = now(), tokens_in = tokens_in + %s,
                               tokens_out = tokens_out + %s, cost_usd = cost_usd + %s
               WHERE id = %s AND worker_id = %s AND status = 'running' RETURNING cancel_requested""",
            (db.json(transcript), tokens_in, tokens_out, cost_usd, run_id, worker_id))
        _owned(cur, run_id, worker_id)
        return bool(cur.fetchone()["cancel_requested"])


def add_step(run_id: str, n: int, kind: str, name: str | None, input: Any, output: Any, status: str, attempt: int,
             duration_ms: int | None = None) -> None:
    with db.tx() as cur:
        cur.execute(
            """INSERT INTO steps (run_id, n, kind, name, input, output, status, attempt, duration_ms)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (run_id, n, kind, name, db.json(input), db.json(output), status, attempt, duration_ms))


def finish(run_id: str, worker_id: str, status: str, *, result: Any = None, error: Any = None) -> None:
    assert status in ("succeeded", "failed", "cancelled")
    with db.tx() as cur:
        cur.execute(
            """UPDATE runs SET status = %s, result = %s, error = %s, finished_at = now(), worker_id = NULL
               WHERE id = %s AND worker_id = %s AND status = 'running'""",
            (status, db.json(result), db.json(error), run_id, worker_id))
        _owned(cur, run_id, worker_id)
        cur.execute("SELECT pg_notify('runs_changed', %s)", (run_id,))


def pause_for_approval(run_id: str, worker_id: str) -> None:
    with db.tx() as cur:
        cur.execute("""UPDATE runs SET status = 'waiting_approval', worker_id = NULL
                       WHERE id = %s AND worker_id = %s AND status = 'running'""", (run_id, worker_id))
        _owned(cur, run_id, worker_id)


def resume(run_id: str) -> None:
    """Puts a waiting run back in the queue (after an approval decision or expiry)."""
    with db.tx() as cur:
        cur.execute("UPDATE runs SET status = 'queued' WHERE id = %s AND status = 'waiting_approval'", (run_id,))
        cur.execute("SELECT pg_notify('runs_queued', %s)", (run_id,))


def request_cancel(run_id: str) -> str:
    """Queued/waiting runs are cancelled at once; running ones at their next checkpoint."""
    with db.tx() as cur:
        cur.execute("SELECT status FROM runs WHERE id = %s FOR UPDATE", (run_id,))
        row = cur.fetchone()
        if row is None:
            raise KeyError(run_id)
        status = row["status"]
        if status in ("queued", "waiting_approval"):
            cur.execute("""UPDATE runs SET status = 'cancelled', finished_at = now(),
                           error = %s WHERE id = %s""", (db.json({"reason": "cancelled_by_user"}), run_id))
            cur.execute("UPDATE approvals SET status = 'expired' WHERE run_id = %s AND status IN ('pending','approved')",
                        (run_id,))
            return "cancelled"
        if status == "running":
            cur.execute("UPDATE runs SET cancel_requested = true WHERE id = %s", (run_id,))
            return "cancel_requested"
        return status


def get(run_id: str) -> dict[str, Any] | None:
    with db.tx() as cur:
        cur.execute("SELECT * FROM runs WHERE id = %s", (run_id,))
        return cur.fetchone()


def steps(run_id: str) -> list[dict[str, Any]]:
    with db.tx() as cur:
        cur.execute("SELECT * FROM steps WHERE run_id = %s ORDER BY n, id", (run_id,))
        return cur.fetchall()


def list_runs(owner: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    with db.tx() as cur:
        if owner:
            cur.execute("SELECT * FROM runs WHERE owner = %s ORDER BY created_at DESC LIMIT %s", (owner, limit))
        else:
            cur.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT %s", (limit,))
        return cur.fetchall()


def metrics() -> dict[str, Any]:
    with db.tx() as cur:
        cur.execute("SELECT status::text, count(*) AS n FROM runs GROUP BY status")
        by_status = {r["status"]: r["n"] for r in cur.fetchall()}
        cur.execute("SELECT EXTRACT(EPOCH FROM now() - min(created_at)) AS age FROM runs WHERE status = 'queued'")
        oldest = cur.fetchone()["age"]
        cur.execute("""SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM finished_at - created_at)) AS p50,
                              percentile_cont(0.95) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM finished_at - created_at)) AS p95,
                              COALESCE(sum(tokens_in + tokens_out), 0) AS tokens, COALESCE(sum(cost_usd), 0) AS cost
                       FROM runs WHERE finished_at > now() - interval '24 hours'""")
        agg = cur.fetchone()
        cur.execute("SELECT count(*) AS n FROM approvals WHERE status = 'pending'")
        pending = cur.fetchone()["n"]
    return {
        "by_status": by_status,
        "queue_depth": by_status.get("queued", 0),
        "oldest_queued_s": float(oldest) if oldest is not None else None,
        "latency_p50_s": float(agg["p50"]) if agg["p50"] is not None else None,
        "latency_p95_s": float(agg["p95"]) if agg["p95"] is not None else None,
        "tokens_24h": int(agg["tokens"]), "cost_usd_24h": float(agg["cost"]),
        "pending_approvals": pending,
    }
