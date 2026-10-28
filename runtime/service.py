"""Use-case layer shared by the HTTP API and the CLI."""
from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator

from runtime import agents, prompts, runs


class InvalidInput(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def submit(owner: str, agent: str, input: dict[str, Any], idempotency_key: str | None = None,
           model: str | None = None) -> tuple[dict[str, Any], bool]:
    spec = agents.get(agent)
    if spec.input_schema:
        errors = [f"{'/'.join(map(str, e.path)) or '/'}: {e.message}"
                  for e in Draft202012Validator(spec.input_schema).iter_errors(input)]
        if errors:
            raise InvalidInput(errors)
    prompt = prompts.active(spec.prompt_name)   # the run is pinned to this exact version
    return runs.enqueue(owner, agent, input, prompt_name=prompt.name, prompt_version=prompt.version,
                        model=model or agents.default_model(), max_steps=spec.max_steps,
                        idempotency_key=idempotency_key)
