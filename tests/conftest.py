import os
import sys
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime import agents, config, db  # noqa: E402

# Tests get their own database: they drop the schema on every test, which must never
# touch the database a running API/worker (demo, dev) is using.
ADMIN_URL = os.environ.get("TEST_ADMIN_DATABASE_URL", "postgresql://runtime:runtime@localhost:55432/runtime")
TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://runtime:runtime@localhost:55432/runtime_test")


def _reachable() -> bool:
    try:
        with psycopg.connect(ADMIN_URL, connect_timeout=3, autocommit=True) as conn:
            name = TEST_URL.rsplit("/", 1)[1]
            if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
                conn.execute(f'CREATE DATABASE "{name}"')
        return True
    except psycopg.OperationalError:
        return False


@pytest.fixture(autouse=True)
def fresh_db():
    if not _reachable():
        pytest.skip("Postgres not reachable — run `docker compose up -d db` (see README)")
    config.override(config.Settings(database_url=TEST_URL, stale_after_s=1, heartbeat_interval_s=0.2,
                                    poll_interval_s=0.05, approval_ttl_s=3600))
    with psycopg.connect(TEST_URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    db.close()
    db.migrate()
    agents.seed_prompts()
    yield
    db.close()
    config.override(None)


@pytest.fixture
def goal():
    return {"item": "laptops", "quantity": 10, "max_unit_price": 5000}
