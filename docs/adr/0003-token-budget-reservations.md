# ADR 0003 — Token budgets with reserve / settle / release

**Status:** accepted

## Context
"Check the remaining budget, then call the model" is a race: ten concurrent
runs all see 1 000 tokens left and all spend them.

## Decision
A ledger with three entry kinds. Before a call, the agent **reserves** its worst
case (prompt estimate + `max_output_tokens`) while holding a row lock on the
owner's `budgets` row; the check and the insert are one critical section. After
the call it **settles** (releases the reservation, records real usage); on error
it **releases**. Remaining = daily limit − (reserved + settled − released) over 24 h.

## Consequences
- Overspending under concurrency is impossible (tested: 10 threads × 300 tokens
  against 1 000 → exactly 3 succeed).
- A run that exhausts the budget fails with `budget_exceeded` and the numbers,
  instead of a provider 429 at an arbitrary point.
- Reservations are pessimistic, so near the limit a run may be refused that would
  have fit. That is the right side to err on for spend.
