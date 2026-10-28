"""Human approval for tools that change the world.

When an agent wants to call a write tool, the run pauses (`waiting_approval`)
and an approval is created for *those exact arguments*: the decision stores
`action_sha = sha256(tool, canonical(arguments))`. If the model, after resuming,
asks for something even slightly different (another amount, another supplier),
the hash does not match and a new approval is required. An approval is consumed
once; it cannot be replayed.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Literal

from runtime import db, runs
from runtime.config import get_settings

State = Literal["none", "pending", "approved", "rejected", "expired"]


def action_sha(tool: str, arguments: dict[str, Any]) -> str:
    return hashlib.sha256(f"{tool}|{json.dumps(arguments, sort_keys=True, separators=(',', ':'))}".encode()).hexdigest()


def request(run_id: str, tool: str, arguments: dict[str, Any]) -> str:
    approval_id = uuid.uuid4()
    with db.tx() as cur:
        cur.execute(
            """INSERT INTO approvals (id, run_id, tool, arguments, action_sha, expires_at)
               VALUES (%s, %s, %s, %s, %s, now() + make_interval(secs => %s))""",
            (approval_id, run_id, tool, db.json(arguments), action_sha(tool, arguments), get_settings().approval_ttl_s))
    return str(approval_id)


def state_for(run_id: str, tool: str, arguments: dict[str, Any]) -> tuple[State, dict[str, Any] | None]:
    """The latest decision for this run and these exact arguments."""
    with db.tx() as cur:
        cur.execute("""UPDATE approvals SET status = 'expired' WHERE run_id = %s AND status = 'pending'
                       AND expires_at < now()""", (run_id,))
        cur.execute("""SELECT * FROM approvals WHERE run_id = %s AND action_sha = %s AND status <> 'consumed'
                       ORDER BY created_at DESC LIMIT 1""", (run_id, action_sha(tool, arguments)))
        row = cur.fetchone()
    return (row["status"], row) if row else ("none", None)


def consume(approval_id: str) -> bool:
    with db.tx() as cur:
        cur.execute("UPDATE approvals SET status = 'consumed' WHERE id = %s AND status = 'approved'", (approval_id,))
        return cur.rowcount == 1


def close_stale_for_run(run_id: str) -> None:
    """A rejected/expired decision has been shown to the agent; it should not block the next request."""
    with db.tx() as cur:
        cur.execute("UPDATE approvals SET status = 'consumed' WHERE run_id = %s AND status IN ('rejected', 'expired')",
                    (run_id,))


def decide(approval_id: str, approve: bool, decided_by: str, note: str = "") -> dict[str, Any]:
    with db.tx() as cur:
        cur.execute("""UPDATE approvals SET status = %s, decided_by = %s, note = %s, decided_at = now()
                       WHERE id = %s AND status = 'pending' AND expires_at > now() RETURNING *""",
                    ("approved" if approve else "rejected", decided_by, note, approval_id))
        row = cur.fetchone()
    if row is None:
        raise LookupError("approval not found, already decided, or expired")
    runs.resume(str(row["run_id"]))
    return row


def expire_due() -> list[str]:
    """Housekeeping: expires overdue approvals and wakes their runs so the agent can react."""
    with db.tx() as cur:
        cur.execute("""UPDATE approvals SET status = 'expired' WHERE status = 'pending' AND expires_at < now()
                       RETURNING run_id""")
        run_ids = [str(r["run_id"]) for r in cur.fetchall()]
    for run_id in run_ids:
        runs.resume(run_id)
    return run_ids


def pending(limit: int = 50) -> list[dict[str, Any]]:
    with db.tx() as cur:
        cur.execute("""SELECT a.*, r.owner, r.agent, r.input AS run_input FROM approvals a JOIN runs r ON r.id = a.run_id
                       WHERE a.status = 'pending' ORDER BY a.created_at LIMIT %s""", (limit,))
        return cur.fetchall()
