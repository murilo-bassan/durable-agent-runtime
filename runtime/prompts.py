"""Versioned prompts in the database.

A run records the exact `(name, version)` it started with, so a prompt edited
mid-flight never changes a running agent, and any past answer can be traced to
the text that produced it. Activation is a single UPDATE guarded by a partial
unique index — there is never a moment with two active versions.
"""
from __future__ import annotations

import hashlib
import string
from dataclasses import dataclass
from typing import Any

from runtime import db


class PromptError(Exception):
    pass


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    template: str
    sha256: str
    active: bool

    def render(self, variables: dict[str, Any]) -> str:
        fields = {f for _, f, _, _ in string.Formatter().parse(self.template) if f}
        missing = fields - set(variables)
        if missing:
            raise PromptError(f"missing variables for {self.name} v{self.version}: {sorted(missing)}")
        return self.template.format(**variables)


def _row(r: dict) -> Prompt:
    return Prompt(name=r["name"], version=r["version"], template=r["template"], sha256=r["sha256"], active=r["active"])


def publish(name: str, template: str, author: str, activate: bool = True) -> Prompt:
    """Stores a new version. Identical text to the latest version is a no-op, not a new version."""
    sha = hashlib.sha256(template.encode()).hexdigest()
    with db.tx() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"prompt:{name}",))
        cur.execute("SELECT * FROM prompts WHERE name = %s ORDER BY version DESC LIMIT 1", (name,))
        latest = cur.fetchone()
        if latest and latest["sha256"] == sha:
            version = latest["version"]
        else:
            version = (latest["version"] if latest else 0) + 1
            cur.execute("INSERT INTO prompts (name, version, template, sha256, created_by) VALUES (%s, %s, %s, %s, %s)",
                        (name, version, template, sha, author))
        if activate:
            cur.execute("UPDATE prompts SET active = false WHERE name = %s AND active", (name,))
            cur.execute("UPDATE prompts SET active = true WHERE name = %s AND version = %s", (name, version))
    return get(name, version)


def get(name: str, version: int) -> Prompt:
    with db.tx() as cur:
        cur.execute("SELECT * FROM prompts WHERE name = %s AND version = %s", (name, version))
        row = cur.fetchone()
    if row is None:
        raise PromptError(f"prompt {name} v{version} not found")
    return _row(row)


def active(name: str) -> Prompt:
    with db.tx() as cur:
        cur.execute("SELECT * FROM prompts WHERE name = %s AND active", (name,))
        row = cur.fetchone()
    if row is None:
        raise PromptError(f"prompt {name} has no active version")
    return _row(row)


def versions(name: str) -> list[Prompt]:
    with db.tx() as cur:
        cur.execute("SELECT * FROM prompts WHERE name = %s ORDER BY version", (name,))
        return [_row(r) for r in cur.fetchall()]
