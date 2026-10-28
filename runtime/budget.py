"""Token budgets with reservations.

Before each model call the agent *reserves* its worst case (prompt estimate +
max output tokens). The reservation is checked against the owner's daily
budget under a row lock on `budgets`, so concurrent runs serialize on the
check and cannot overspend together. After the call the reservation is
*settled* to the real usage; on failure it is *released*.

    remaining = daily_tokens - (settled + reserved - released) over the last 24h
"""
from __future__ import annotations

from dataclasses import dataclass

from runtime import db
from runtime.config import get_settings


class BudgetExceeded(Exception):
    def __init__(self, owner: str, needed: int, remaining: int) -> None:
        super().__init__(f"{owner} needs {needed} tokens, {remaining} left today")
        self.owner, self.needed, self.remaining = owner, needed, remaining


@dataclass(frozen=True)
class Reservation:
    owner: str
    run_id: str
    tokens: int


def ensure_owner(cur, owner: str) -> dict:
    s = get_settings()
    cur.execute("INSERT INTO budgets (owner, daily_tokens, max_concurrent) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (owner, s.default_daily_tokens, s.default_max_concurrent))
    cur.execute("SELECT * FROM budgets WHERE owner = %s FOR UPDATE", (owner,))
    return cur.fetchone()


def _used(cur, owner: str) -> int:
    cur.execute(
        """SELECT COALESCE(SUM(CASE kind WHEN 'released' THEN -tokens ELSE tokens END), 0) AS used
           FROM token_ledger WHERE owner = %s AND created_at > now() - interval '24 hours'""",
        (owner,))
    return int(cur.fetchone()["used"])


def set_budget(owner: str, daily_tokens: int, max_concurrent: int | None = None) -> None:
    with db.tx() as cur:
        ensure_owner(cur, owner)
        cur.execute("UPDATE budgets SET daily_tokens = %s, max_concurrent = COALESCE(%s, max_concurrent) WHERE owner = %s",
                    (daily_tokens, max_concurrent, owner))


def reserve(owner: str, run_id: str, tokens: int) -> Reservation:
    with db.tx() as cur:
        budget = ensure_owner(cur, owner)   # row lock: the check and the insert are one critical section
        remaining = budget["daily_tokens"] - _used(cur, owner)
        if tokens > remaining:
            raise BudgetExceeded(owner, tokens, remaining)
        cur.execute("INSERT INTO token_ledger (owner, run_id, kind, tokens) VALUES (%s, %s, 'reserved', %s)",
                    (owner, run_id, tokens))
    return Reservation(owner, run_id, tokens)


def settle(reservation: Reservation, actual: int) -> None:
    """Replaces the reservation by the real usage: release all of it, record what was spent."""
    with db.tx() as cur:
        cur.execute("INSERT INTO token_ledger (owner, run_id, kind, tokens) VALUES (%s, %s, 'released', %s), "
                    "(%s, %s, 'settled', %s)",
                    (reservation.owner, reservation.run_id, reservation.tokens,
                     reservation.owner, reservation.run_id, actual))


def release(reservation: Reservation) -> None:
    with db.tx() as cur:
        cur.execute("INSERT INTO token_ledger (owner, run_id, kind, tokens) VALUES (%s, %s, 'released', %s)",
                    (reservation.owner, reservation.run_id, reservation.tokens))


def remaining(owner: str) -> int:
    with db.tx() as cur:
        budget = ensure_owner(cur, owner)
        return budget["daily_tokens"] - _used(cur, owner)
