"""Settings from the environment, overridable in tests."""
from __future__ import annotations

import os
from functools import lru_cache

from pydantic import BaseModel, Field


class Settings(BaseModel):
    database_url: str = "postgresql://runtime:runtime@localhost:55432/runtime"
    worker_slots: int = Field(default=4, ge=1)
    poll_interval_s: float = 0.5
    heartbeat_interval_s: float = 2.0
    stale_after_s: float = 15.0
    max_attempts: int = 3
    approval_ttl_s: int = 3600
    default_daily_tokens: int = 200_000
    default_max_concurrent: int = 2
    # "scripted" runs fully offline and deterministically; "openai" talks to any
    # OpenAI-compatible endpoint (OpenRouter, vLLM, Ollama...) using LLM_API_KEY.
    llm_provider: str = "scripted"
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: str | None = None
    default_model: str = "scripted/planner-v1"

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        return cls(
            database_url=env("DATABASE_URL", cls.model_fields["database_url"].default),
            worker_slots=int(env("WORKER_SLOTS", "4")),
            poll_interval_s=float(env("WORKER_POLL_INTERVAL_S", "0.5")),
            heartbeat_interval_s=float(env("WORKER_HEARTBEAT_INTERVAL_S", "2")),
            stale_after_s=float(env("WORKER_STALE_AFTER_S", "15")),
            max_attempts=int(env("RUN_MAX_ATTEMPTS", "3")),
            approval_ttl_s=int(env("APPROVAL_TTL_S", "3600")),
            llm_provider=env("LLM_PROVIDER", "scripted"),
            llm_base_url=env("LLM_BASE_URL", "https://openrouter.ai/api/v1"),
            llm_api_key=env("LLM_API_KEY") or None,
            default_model=env("DEFAULT_MODEL", "scripted/planner-v1"),
        )


_override: Settings | None = None


@lru_cache(maxsize=1)
def _from_env() -> Settings:
    return Settings.from_env()


def get_settings() -> Settings:
    return _override or _from_env()


def override(settings: Settings | None) -> None:
    global _override
    _override = settings
