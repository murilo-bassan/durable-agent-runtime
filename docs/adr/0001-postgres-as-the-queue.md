# ADR 0001 — Postgres is the queue

**Status:** accepted

## Context
Agent runs are long (seconds to hours with a human in the loop), few (not
millions per second) and *stateful*: each one carries a transcript, steps,
token usage and approvals. The usual answer — Celery/RQ on Redis — would put
the job in one store and its state in another, and every crash would need the
two reconciled.

## Decision
The run row **is** the job. Workers claim with

```sql
SELECT … FROM runs WHERE status = 'queued' ORDER BY created_at
FOR UPDATE SKIP LOCKED LIMIT 1
```

inside the `UPDATE … SET status = 'running'`, so claiming and changing state is
one atomic statement. `SKIP LOCKED` lets any number of workers poll at once
without blocking each other or double-claiming (tested with 8 threads × 40 runs).
A partial index on `status = 'queued'` keeps the scan small.

## Consequences
- One source of truth: enqueue, state, transcript and side effects share transactions.
- No broker to run, monitor or back up.
- Throughput ceiling is far above what LLM-bound work needs (each run spends
  seconds waiting on a model); a busier system would add `LISTEN/NOTIFY`
  wake-ups (already emitted on `runs_queued`) before reaching for a broker.
