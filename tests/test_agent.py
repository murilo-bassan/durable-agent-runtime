import json

from runtime import agent_loop, approvals, budget, db, runs, service
from runtime.llm import LLMResponse, ScriptedPlanner


def start(goal, owner="ana"):
    run, _ = service.submit(owner, "procurement", goal)
    claimed = runs.claim_next("w1")
    assert claimed and claimed.id == str(run["id"])
    return claimed


def resume(worker="w1"):
    return runs.claim_next(worker)


def test_full_flow_with_approval(goal):
    c = start(goal)
    assert agent_loop.execute(c, "w1", ScriptedPlanner()) == "paused"
    assert runs.get(c.id)["status"] == "waiting_approval"

    [pending] = approvals.pending()
    assert pending["tool"] == "create_purchase_order"
    assert pending["arguments"]["unit_price"] <= goal["max_unit_price"]
    approvals.decide(str(pending["id"]), True, decided_by="manager")

    c2 = resume()
    assert agent_loop.execute(c2, "w1", ScriptedPlanner()) == "succeeded"
    run = runs.get(c.id)
    assert run["status"] == "succeeded" and "PO-" in run["result"]["answer"]
    kinds = [s["kind"] for s in runs.steps(c.id)]
    assert kinds.count("tool") == 5 and "approval" in kinds   # search, 3 quotes, order
    assert run["tokens_in"] > 0


def test_rejection_tries_next_quote_then_stops(goal):
    c = start(goal)
    agent_loop.execute(c, "w1", ScriptedPlanner())
    first = approvals.pending()[0]
    approvals.decide(str(first["id"]), False, "manager", note="vendor under review")
    agent_loop.execute(resume(), "w1", ScriptedPlanner())
    second = approvals.pending()[0]
    assert second["arguments"]["supplier_id"] != first["arguments"]["supplier_id"]
    approvals.decide(str(second["id"]), False, "manager")
    assert agent_loop.execute(resume(), "w1", ScriptedPlanner()) == "succeeded"
    assert "rejected" in runs.get(c.id)["result"]["answer"]


def test_approval_is_bound_to_exact_arguments(goal):
    c = start(goal)
    agent_loop.execute(c, "w1", ScriptedPlanner())
    a = approvals.pending()[0]
    args = dict(a["arguments"])
    assert approvals.state_for(c.id, "create_purchase_order", args)[0] == "pending"
    args["quantity"] += 1
    assert approvals.state_for(c.id, "create_purchase_order", args)[0] == "none"


def test_crash_and_recovery_resumes_without_redoing_work(goal):
    """Worker A dies after two steps; worker B finishes from the transcript."""
    c = start(goal)

    class DiesAfterTwoCalls(ScriptedPlanner):
        calls = 0

        def complete(self, *a, **k):
            DiesAfterTwoCalls.calls += 1
            if DiesAfterTwoCalls.calls > 2:
                raise SystemExit("power cut")   # not caught by the loop: simulates a dead process
            return super().complete(*a, **k)

    try:
        agent_loop.execute(c, "w1", DiesAfterTwoCalls())
    except SystemExit:
        pass
    with db.tx() as cur:
        cur.execute("UPDATE runs SET heartbeat_at = now() - interval '1 hour' WHERE id = %s", (c.id,))
    assert runs.recover_stale(1, 3)["requeued"] == [c.id]

    b = runs.claim_next("w2")
    assert b.attempts == 2 and len(b.transcript) == 6   # system, user, (assistant, tool) × 2
    assert agent_loop.execute(b, "w2", ScriptedPlanner()) == "paused"
    searches = [s for s in runs.steps(c.id) if s["name"] == "search_suppliers"]
    assert len(searches) == 1                            # not repeated after recovery


def test_write_tool_runs_once_even_if_step_is_retried(goal):
    from runtime.tools.base import once
    calls = []
    effect = lambda: calls.append(1) or {"status": "created"}  # noqa: E731
    first = once("run-x:call_5", "create_purchase_order", effect)
    second = once("run-x:call_5", "create_purchase_order", effect)
    assert calls == [1] and second["replayed"] is True and first["status"] == "created"


def test_budget_exhaustion_fails_cleanly(goal):
    budget.set_budget("ana", 50)
    c = start(goal)
    assert agent_loop.execute(c, "w1", ScriptedPlanner()) == "failed"
    assert runs.get(c.id)["error"]["reason"] == "budget_exceeded"


def test_cancel_is_honored_at_next_checkpoint(goal):
    c = start(goal)

    class CancelsMidway(ScriptedPlanner):
        def complete(self, model, messages, tools, max_tokens):
            runs.request_cancel(c.id)
            return super().complete(model, messages, tools, max_tokens)

    assert agent_loop.execute(c, "w1", CancelsMidway()) == "cancelled"
    assert runs.get(c.id)["status"] == "cancelled"


def test_max_steps(goal):
    class Loops:
        def complete(self, model, messages, tools, max_tokens):
            n = sum(1 for m in messages if m["role"] == "assistant")
            return LLMResponse(None, [{"id": f"c{n}", "name": "search_suppliers", "arguments": {"item": "laptops"}}], 10, 10)

    c = start(goal)
    assert agent_loop.execute(c, "w1", Loops()) == "failed"
    assert runs.get(c.id)["error"]["reason"] == "max_steps"


def test_bad_tool_arguments_become_observations(goal):
    class Sloppy:
        def complete(self, model, messages, tools, max_tokens):
            if any(m["role"] == "tool" for m in messages):
                last = json.loads([m for m in messages if m["role"] == "tool"][-1]["content"])
                return LLMResponse(f"saw {last['error']}", [], 5, 5)
            return LLMResponse(None, [{"id": "c1", "name": "get_quote", "arguments": {"item": 3}}], 5, 5)

    c = start(goal)
    assert agent_loop.execute(c, "w1", Sloppy()) == "succeeded"
    assert runs.get(c.id)["result"]["answer"] == "saw invalid_arguments"
