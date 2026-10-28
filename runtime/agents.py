"""Agent definitions: which prompt, which tools, which model, how many steps."""
from __future__ import annotations

from dataclasses import dataclass

from runtime import prompts
from runtime.config import get_settings
from runtime.tools import procurement
from runtime.tools.base import ToolDef

PROCUREMENT_PROMPT = """You are a procurement assistant for {org_name}.
Goal: buy what the user asks for at the lowest unit price that respects their limit.
Rules:
- Compare at least two quotes when two suppliers exist.
- Never create a purchase order above the user's max unit price.
- A purchase order is a write: it will wait for a human. If it is rejected, explain and stop.
- Finish with one paragraph citing the quotes you compared."""


@dataclass(frozen=True)
class AgentSpec:
    name: str
    prompt_name: str
    tools: tuple[ToolDef, ...]
    max_steps: int = 12
    max_output_tokens: int = 800
    input_schema: dict | None = None

    def tool(self, name: str) -> ToolDef | None:
        return next((t for t in self.tools if t.name == name), None)


AGENTS: dict[str, AgentSpec] = {
    "procurement": AgentSpec(
        name="procurement", prompt_name="procurement.system", tools=tuple(procurement.TOOLS),
        input_schema={"type": "object", "required": ["item", "quantity", "max_unit_price"], "additionalProperties": False,
                      "properties": {"item": {"type": "string"}, "quantity": {"type": "integer", "minimum": 1},
                                     "max_unit_price": {"type": "number", "exclusiveMinimum": 0}}},
    ),
}


def get(name: str) -> AgentSpec:
    if name not in AGENTS:
        raise KeyError(f"unknown agent '{name}'")
    return AGENTS[name]


def seed_prompts() -> None:
    prompts.publish("procurement.system", PROCUREMENT_PROMPT, author="seed")


def default_model() -> str:
    return get_settings().default_model
