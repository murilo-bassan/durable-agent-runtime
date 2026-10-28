"""The agent loop, written to be interrupted.

State lives only in the persisted transcript. On every (re)start the loop
looks at the last message and decides what is owed:

  * assistant message with unanswered tool calls → run those tools
  * assistant message without tool calls         → that was the final answer
  * anything else                                → ask the model for the next move

After each model call and each tool result it checkpoints. A crash costs at
most the step in flight; write tools are deduplicated by `run_id:call_id`.
"""
from __future__ import annotations

import json
import time
from typing import Any, Literal

from jsonschema import Draft202012Validator

from runtime import agents, approvals, budget, prompts, runs
from runtime.llm import LLM, TransientLLMError, estimate_tokens
from runtime.runs import Claimed
from runtime.tools.base import once

Outcome = Literal["succeeded", "failed", "cancelled", "paused"]


class _Stop(Exception):
    def __init__(self, outcome: Outcome) -> None:
        self.outcome = outcome


def _initial_transcript(run: Claimed, spec: agents.AgentSpec) -> list[dict[str, Any]]:
    system = prompts.get(run.prompt_name, run.prompt_version).render({"org_name": "Acme Synthetic Co."})
    return [{"role": "system", "content": system},
            {"role": "user", "content": json.dumps(run.input, ensure_ascii=False)}]


def _unanswered(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    answered = {m["tool_call_id"] for m in transcript if m["role"] == "tool"}
    last_assistant = next((m for m in reversed(transcript) if m["role"] == "assistant"), None)
    if not last_assistant:
        return []
    return [c for c in last_assistant.get("tool_calls") or [] if c["id"] not in answered]


def execute(run: Claimed, worker_id: str, llm: LLM) -> Outcome:
    spec = agents.get(run.agent)
    transcript = run.transcript or _initial_transcript(run, spec)
    step_no = sum(1 for m in transcript if m["role"] in ("assistant", "tool"))

    def save(**usage: Any) -> None:
        if runs.checkpoint(run.id, worker_id, transcript, **usage):
            runs.finish(run.id, worker_id, "cancelled", error={"reason": "cancelled_by_user", "at_step": step_no})
            raise _Stop("cancelled")

    def record(kind: str, name: str | None, input: Any, output: Any, status: str, started: float) -> None:
        runs.add_step(run.id, step_no, kind, name, input, output, status, run.attempts,
                      int((time.perf_counter() - started) * 1000))

    try:
        if not run.transcript:
            save()
        while True:
            pending = _unanswered(transcript)
            if pending:
                for call in pending:
                    step_no += 1
                    _run_tool(run, worker_id, spec, call, transcript, record)
                    save()
                continue

            last = transcript[-1]
            if last["role"] == "assistant" and not last.get("tool_calls"):
                runs.finish(run.id, worker_id, "succeeded", result={"answer": last["content"]})
                return "succeeded"

            if sum(1 for m in transcript if m["role"] == "assistant") >= run.max_steps:
                runs.finish(run.id, worker_id, "failed", error={"reason": "max_steps", "max_steps": run.max_steps})
                return "failed"

            step_no += 1
            started = time.perf_counter()
            reservation = None
            try:
                reservation = budget.reserve(run.owner, run.id, estimate_tokens(transcript) + spec.max_output_tokens)
                response = _complete_with_retry(llm, run.model, transcript, spec)
            except budget.BudgetExceeded as e:
                record("system", "budget", {"needed": e.needed}, {"remaining": e.remaining}, "error", started)
                runs.finish(run.id, worker_id, "failed",
                            error={"reason": "budget_exceeded", "needed": e.needed, "remaining": e.remaining})
                return "failed"
            except Exception:
                if reservation:
                    budget.release(reservation)
                raise
            budget.settle(reservation, response.tokens_in + response.tokens_out)
            message = {"role": "assistant", "content": response.content, "tool_calls": response.tool_calls}
            transcript.append(message)
            record("llm", response.model, {"messages": len(transcript) - 1},
                   {"content": response.content, "tool_calls": response.tool_calls,
                    "tokens": response.tokens_in + response.tokens_out}, "ok", started)
            save(tokens_in=response.tokens_in, tokens_out=response.tokens_out, cost_usd=response.cost_usd)
    except _Stop as stop:
        return stop.outcome


def _complete_with_retry(llm: LLM, model: str, transcript: list, spec: agents.AgentSpec, attempts: int = 3):
    delay = 0.5
    for i in range(attempts):
        try:
            return llm.complete(model, transcript, [t.schema() for t in spec.tools], spec.max_output_tokens)
        except TransientLLMError:
            if i == attempts - 1:
                raise
            time.sleep(delay)
            delay *= 2


def _run_tool(run: Claimed, worker_id: str, spec: agents.AgentSpec, call: dict[str, Any],
              transcript: list[dict[str, Any]], record) -> None:
    started = time.perf_counter()
    tool = spec.tool(call["name"])

    def reply(payload: dict[str, Any], status: str = "ok") -> None:
        transcript.append({"role": "tool", "tool_call_id": call["id"], "name": call["name"],
                           "content": json.dumps(payload, ensure_ascii=False)})
        record("tool", call["name"], call["arguments"], payload, status, started)

    if tool is None:
        return reply({"error": "unknown_tool", "available": [t.name for t in spec.tools]}, "error")
    errors = [e.message for e in Draft202012Validator(tool.parameters).iter_errors(call["arguments"])]
    if errors:
        return reply({"error": "invalid_arguments", "details": errors[:5]}, "error")

    if tool.writes:
        state, row = approvals.state_for(run.id, tool.name, call["arguments"])
        if state in ("none", "pending"):
            if state == "none":
                approval_id = approvals.request(run.id, tool.name, call["arguments"])
                record("approval", tool.name, call["arguments"], {"approval_id": approval_id}, "pending", started)
            runs.checkpoint(run.id, worker_id, transcript)
            runs.pause_for_approval(run.id, worker_id)
            raise _Stop("paused")
        if state in ("rejected", "expired"):
            approvals.close_stale_for_run(run.id)
            return reply({"error": "rejected_by_human" if state == "rejected" else "approval_expired",
                          "note": (row or {}).get("note") or ""}, "error")
        approvals.consume(str(row["id"]))
        result = once(f"{run.id}:{call['id']}", tool.name, lambda: tool.fn(**call["arguments"]))
        return reply(result)

    try:
        return reply(tool.fn(**call["arguments"]))
    except Exception as e:  # a tool bug becomes an observation the model can react to
        return reply({"error": "tool_crashed", "type": type(e).__name__}, "error")
