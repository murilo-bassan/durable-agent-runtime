from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from runtime import db


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    parameters: dict[str, Any]          # JSON Schema
    fn: Callable[..., dict[str, Any]]
    writes: bool = False                # True → human approval + exactly-once effect

    def schema(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


def once(effect_key: str, tool: str, effect: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Runs `effect` at most once per key; a retry returns the stored result.

    The advisory lock serializes two workers racing on the same key (possible
    after a recovery), and the insert happens in the same transaction as the
    check, so there is no window where both see "not done yet".
    """
    with db.tx() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (effect_key,))
        cur.execute("SELECT result FROM side_effects WHERE effect_key = %s", (effect_key,))
        hit = cur.fetchone()
        if hit:
            return {**hit["result"], "replayed": True}
        result = effect()
        cur.execute("INSERT INTO side_effects (effect_key, tool, result) VALUES (%s, %s, %s)",
                    (effect_key, tool, db.json(result)))
        return result
