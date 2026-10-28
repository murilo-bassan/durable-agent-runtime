# ADR 0002 — At-least-once steps, exactly-once effects

**Status:** accepted

## Context
A worker can die between "the tool did something" and "I recorded that it did".
No protocol makes that window disappear; the choice is between losing work
(at-most-once) or repeating it (at-least-once).

## Decision
- **Runs are at-least-once.** Workers heartbeat; a run whose heartbeat is older
  than `WORKER_STALE_AFTER_S` is requeued by any live worker (`recover_stale`,
  itself `SKIP LOCKED`). After `RUN_MAX_ATTEMPTS` failure-driven attempts it
  goes `dead` instead of looping forever (poison-run protection).
- **The agent resumes, it does not restart.** All state is the persisted
  transcript; the loop inspects the last message to decide what is owed, so only
  the interrupted step repeats.
- **Effects are exactly-once.** Write tools run through `once(effect_key)` with
  `effect_key = run_id:tool_call_id`, under an advisory lock, recording the
  result in the same transaction. A repeated step gets the stored result back.
- **Ownership is checked on every write.** Updates carry
  `WHERE worker_id = me AND status = 'running'`; a worker that was presumed dead
  but was only slow discovers it on its next write (`LostOwnership`) and stops.

## Consequences
- `scripts/chaos.py` kills a worker process mid-flight: every run finishes and
  the number of purchase orders equals the number of runs. CI runs the drill.
- Read-only tools may run twice after a crash. That is accepted: they are cheap
  and side-effect free by definition.
- The per-owner concurrency cap is soft (two simultaneous claims may both pass).
  A hard cap needs a per-owner lock in the claim; not worth the contention today.
