# ADR 0004 — Human approvals are bound to the exact arguments

**Status:** accepted

## Context
"Approve the purchase" is ambiguous when the model can change its mind between
the question and the action. An approval for 10 laptops at 3 547.74 must not
authorize 12 laptops at 4 619.58.

## Decision
- A write tool call pauses the run (`waiting_approval`) and frees the worker —
  nobody holds a thread while a human decides.
- The approval stores `action_sha = sha256(tool | canonical_json(arguments))`.
  On resume the agent recomputes the hash; any difference means "no approval".
- Approvals are consumed once, expire after `APPROVAL_TTL_S`, and a partial unique
  index allows at most one open approval per run.
- A rejection is fed back to the model as a tool result (`rejected_by_human` +
  note), so the agent can adapt — the demo agent tries the next-cheapest quote
  once, then stops and says so.

## Consequences
- Human-in-the-loop costs no compute while waiting.
- The reviewer sees precisely what will run, and the trail shows who approved what.
