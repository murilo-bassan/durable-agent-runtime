-- Durable agent runtime: runs, steps, approvals, budgets, prompts, idempotency.
-- Every state an agent can be in is a row, so a crashed process loses nothing
-- but the step it was in the middle of — and that step is retried.

CREATE TYPE run_status AS ENUM (
    'queued', 'running', 'waiting_approval', 'succeeded', 'failed', 'cancelled', 'dead'
);

CREATE TABLE prompts (
    name        TEXT NOT NULL,
    version     INTEGER NOT NULL,
    template    TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    active      BOOLEAN NOT NULL DEFAULT false,
    created_by  TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (name, version)
);
-- At most one active version per prompt, enforced by the database, not by code.
CREATE UNIQUE INDEX prompts_one_active ON prompts (name) WHERE active;

CREATE TABLE runs (
    id              UUID PRIMARY KEY,
    owner           TEXT NOT NULL,
    agent           TEXT NOT NULL,
    input           JSONB NOT NULL,
    status          run_status NOT NULL DEFAULT 'queued',
    prompt_name     TEXT NOT NULL,
    prompt_version  INTEGER NOT NULL,
    model           TEXT NOT NULL,
    max_steps       INTEGER NOT NULL CHECK (max_steps BETWEEN 1 AND 50),
    transcript      JSONB NOT NULL DEFAULT '[]',   -- messages so far; the agent resumes from here
    result          JSONB,
    error           JSONB,
    attempts        INTEGER NOT NULL DEFAULT 0,
    worker_id       TEXT,
    heartbeat_at    TIMESTAMPTZ,
    cancel_requested BOOLEAN NOT NULL DEFAULT false,
    tokens_in       INTEGER NOT NULL DEFAULT 0,
    tokens_out      INTEGER NOT NULL DEFAULT 0,
    cost_usd        NUMERIC(12, 6) NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    FOREIGN KEY (prompt_name, prompt_version) REFERENCES prompts (name, version)
);
-- The claim query scans only queued rows, oldest first.
CREATE INDEX runs_queued ON runs (created_at) WHERE status = 'queued';
CREATE INDEX runs_running ON runs (heartbeat_at) WHERE status = 'running';
CREATE INDEX runs_owner ON runs (owner, created_at DESC);

CREATE TABLE steps (
    id          BIGSERIAL PRIMARY KEY,
    run_id      UUID NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    n           INTEGER NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('llm', 'tool', 'approval', 'system')),
    name        TEXT,
    input       JSONB,
    output      JSONB,
    status      TEXT NOT NULL CHECK (status IN ('ok', 'error', 'pending')),
    attempt     INTEGER NOT NULL,
    duration_ms INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX steps_run ON steps (run_id, n);

CREATE TABLE idempotency_keys (
    owner       TEXT NOT NULL,
    key         TEXT NOT NULL,
    request_sha TEXT NOT NULL,     -- same key + different body = conflict, not a silent replay
    run_id      UUID NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (owner, key)
);

CREATE TABLE approvals (
    id          UUID PRIMARY KEY,
    run_id      UUID NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    tool        TEXT NOT NULL,
    arguments   JSONB NOT NULL,
    action_sha  TEXT NOT NULL,     -- binds the decision to these exact arguments
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'rejected', 'expired', 'consumed')),
    decided_by  TEXT,
    note        TEXT,
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at  TIMESTAMPTZ
);
CREATE UNIQUE INDEX approvals_one_open_per_run ON approvals (run_id) WHERE status IN ('pending', 'approved');

CREATE TABLE budgets (
    owner           TEXT PRIMARY KEY,
    daily_tokens    INTEGER NOT NULL CHECK (daily_tokens > 0),
    max_concurrent  INTEGER NOT NULL DEFAULT 2 CHECK (max_concurrent > 0)
);

-- Reservations make spending atomic: reserve the worst case before calling the
-- model, settle the real number after. Two concurrent runs cannot both spend
-- the last 1000 tokens.
CREATE TABLE token_ledger (
    id          BIGSERIAL PRIMARY KEY,
    owner       TEXT NOT NULL,
    run_id      UUID REFERENCES runs (id) ON DELETE SET NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('reserved', 'settled', 'released')),
    tokens      INTEGER NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX token_ledger_owner_day ON token_ledger (owner, created_at);

-- Side effects of write tools, deduplicated by (run, step) so a retried step
-- does not create a second purchase order.
CREATE TABLE side_effects (
    effect_key  TEXT PRIMARY KEY,
    tool        TEXT NOT NULL,
    result      JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
