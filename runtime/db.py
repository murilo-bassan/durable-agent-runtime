"""Postgres access: a connection pool and a tiny, ordered SQL migrator.

The migrator is deliberately boring: numbered `.sql` files applied once, in
order, inside one transaction each, recorded in `schema_migrations`. An
advisory lock makes two processes starting at the same time take turns.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from runtime.config import get_settings

MIGRATIONS = Path(__file__).parent / "migrations"
_MIGRATION_LOCK = 724_001  # arbitrary, constant advisory-lock id

_pool: ConnectionPool | None = None
_pool_url: str | None = None


def pool() -> ConnectionPool:
    global _pool, _pool_url
    url = get_settings().database_url
    if _pool is None or _pool_url != url:
        if _pool is not None:
            _pool.close()
        _pool = ConnectionPool(url, min_size=1, max_size=20, kwargs={"row_factory": dict_row}, open=True)
        _pool_url = url
    return _pool


def close() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def tx() -> Iterator[psycopg.Cursor[dict[str, Any]]]:
    """One transaction; commits on success, rolls back on any exception."""
    with pool().connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                yield cur


def json(value: Any) -> Jsonb:
    return Jsonb(value)


def migrate() -> list[str]:
    applied: list[str] = []
    with psycopg.connect(get_settings().database_url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_MIGRATION_LOCK,))
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT now())")
            done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
            for path in sorted(MIGRATIONS.glob("*.sql")):
                if path.name in done:
                    continue
                with conn.transaction():
                    conn.execute(path.read_text(encoding="utf-8"))
                    conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
                applied.append(path.name)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK,))
    return applied
