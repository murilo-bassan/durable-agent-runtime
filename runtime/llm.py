"""Model clients behind one small interface.

`ScriptedPlanner` is an offline, deterministic stand-in for a tool-calling
model. It is a *pure function of the transcript* — the same property a real
agent needs to be resumable — so tests, demos and crash-recovery drills run
without network, keys or flakiness.

`OpenAICompatible` speaks the `/chat/completions` tool-calling dialect used by
OpenRouter, vLLM, Ollama and others. It reads `LLM_API_KEY` from the
environment; the repository never contains one.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from runtime.config import get_settings

Message = dict[str, Any]


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)  # [{"id", "name", "arguments"}]
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    model: str = ""


class LLM(Protocol):
    def complete(self, model: str, messages: list[Message], tools: list[dict[str, Any]],
                 max_tokens: int) -> LLMResponse: ...


class TransientLLMError(Exception):
    """Worth retrying: timeouts, 429, 5xx."""


def estimate_tokens(messages: list[Message]) -> int:
    return sum(len(json.dumps(m, ensure_ascii=False)) for m in messages) // 4 + 1


# ---------------------------------------------------------------- offline planner

class ScriptedPlanner:
    """Plans a procurement task from the transcript alone.

    goal (user message, JSON): {"item", "quantity", "max_unit_price"}
      1. search_suppliers(item)
      2. get_quote(...) for up to 3 suppliers, one per step
      3. cheapest quote within budget → create_purchase_order (a write: needs approval)
      4. answer, quoting the evidence
    A rejected order makes it try the next-cheapest quote once, then give up politely.
    """

    def complete(self, model: str, messages: list[Message], tools: list[dict[str, Any]],
                 max_tokens: int) -> LLMResponse:
        goal = json.loads(next(m["content"] for m in messages if m["role"] == "user"))
        results = {m["tool_call_id"]: json.loads(m["content"]) for m in messages if m["role"] == "tool"}
        calls = [c for m in messages if m["role"] == "assistant" for c in m.get("tool_calls") or []]

        def outputs(name: str) -> list[tuple[dict, dict]]:
            return [(c["arguments"], results[c["id"]]) for c in calls if c["name"] == name and c["id"] in results]

        step = len(calls) + 1
        tokens_in = estimate_tokens(messages)

        def call(name: str, arguments: dict[str, Any]) -> LLMResponse:
            return LLMResponse(content=None, tool_calls=[{"id": f"call_{step}", "name": name, "arguments": arguments}],
                               tokens_in=tokens_in, tokens_out=40, model=model)

        def answer(text: str) -> LLMResponse:
            return LLMResponse(content=text, tokens_in=tokens_in, tokens_out=len(text) // 4 + 1, model=model)

        searches = outputs("search_suppliers")
        if not searches:
            return call("search_suppliers", {"item": goal["item"]})
        suppliers = searches[-1][1].get("suppliers", [])[:3]
        if not suppliers:
            return answer(f"No supplier offers '{goal['item']}'. Nothing was ordered.")

        quoted = {a["supplier_id"] for a, _ in outputs("get_quote")}
        for s in suppliers:
            if s["id"] not in quoted:
                return call("get_quote", {"supplier_id": s["id"], "item": goal["item"], "quantity": goal["quantity"]})

        quotes = sorted((r for _, r in outputs("get_quote") if "unit_price" in r), key=lambda q: q["unit_price"])
        within = [q for q in quotes if q["unit_price"] <= goal["max_unit_price"]]
        if not within:
            best = quotes[0]["unit_price"] if quotes else None
            return answer(f"No quote within {goal['max_unit_price']:.2f}/unit (best was {best}). Nothing was ordered.")

        orders = outputs("create_purchase_order")
        placed = [r for _, r in orders if r.get("status") == "created"]
        if placed:
            po = placed[-1]
            return answer(f"Ordered {po['quantity']}× {po['item']} from {po['supplier_id']} at "
                          f"{po['unit_price']:.2f}/unit (PO {po['po_number']}, total {po['total']:.2f}). "
                          f"Compared {len(quotes)} quotes; this was the cheapest within budget.")
        rejected = [a["supplier_id"] for a, r in orders if r.get("error") == "rejected_by_human"]
        remaining = [q for q in within if q["supplier_id"] not in rejected]
        if not remaining or len(rejected) >= 2:
            return answer("The purchase order was rejected by a reviewer. Nothing was ordered.")
        q = remaining[0]
        return call("create_purchase_order", {"supplier_id": q["supplier_id"], "item": goal["item"],
                                              "quantity": goal["quantity"], "unit_price": q["unit_price"]})


# ---------------------------------------------------------------- OpenAI-compatible

class OpenAICompatible:
    def __init__(self, base_url: str, api_key: str, transport: httpx.BaseTransport | None = None,
                 timeout_s: float = 60) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout_s, transport=transport,
                                    headers={"Authorization": f"Bearer {api_key}"})

    def complete(self, model: str, messages: list[Message], tools: list[dict[str, Any]],
                 max_tokens: int) -> LLMResponse:
        wire = []
        for m in messages:
            if m["role"] == "assistant" and m.get("tool_calls"):
                wire.append({"role": "assistant", "content": m.get("content"), "tool_calls": [
                    {"id": c["id"], "type": "function",
                     "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}} for c in m["tool_calls"]]})
            else:
                wire.append({k: v for k, v in m.items() if k in ("role", "content", "tool_call_id")})
        body = {"model": model, "messages": wire, "max_tokens": max_tokens,
                "tools": [{"type": "function", "function": t} for t in tools]}
        started = time.perf_counter()
        try:
            r = self._client.post("/chat/completions", json=body)
        except httpx.TimeoutException as e:
            raise TransientLLMError("timeout") from e
        if r.status_code == 429 or r.status_code >= 500:
            raise TransientLLMError(f"upstream {r.status_code}")
        r.raise_for_status()
        data = r.json()
        msg = data["choices"][0]["message"]
        calls = [{"id": c["id"], "name": c["function"]["name"], "arguments": json.loads(c["function"]["arguments"] or "{}")}
                 for c in msg.get("tool_calls") or []]
        usage = data.get("usage", {})
        _ = time.perf_counter() - started
        return LLMResponse(content=msg.get("content"), tool_calls=calls, tokens_in=usage.get("prompt_tokens", 0),
                           tokens_out=usage.get("completion_tokens", 0), cost_usd=float(usage.get("cost", 0) or 0),
                           model=data.get("model", model))


def from_settings() -> LLM:
    s = get_settings()
    if s.llm_provider == "openai":
        if not s.llm_api_key:
            raise RuntimeError("LLM_PROVIDER=openai needs LLM_API_KEY in the environment")
        return OpenAICompatible(s.llm_base_url, s.llm_api_key)
    return ScriptedPlanner()
