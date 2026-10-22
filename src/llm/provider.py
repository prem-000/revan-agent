from __future__ import annotations

from typing import Optional
from langchain_core.messages import BaseMessage
from pydantic import BaseModel
from src.config import Settings, get_settings
from src.llm.groq_adapter import GroqProviderAdapter, LLMProviderAdapter
from src.llm.opencode_adapter import OpenCodeZenProvider
from src.llm.router import LLMRouter
from src.llm.budget import RunBudget


class MockProviderAdapter(LLMProviderAdapter):
    """Mock LLM provider for fast unit tests without network or credentials."""

    def __init__(self, canned_response: str = '{"status": "ok"}') -> None:
        self.canned_response = canned_response
        self.call_history: list[dict] = []

    async def ainvoke(
        self,
        messages: list[BaseMessage],
        temperature: float = 0.2,
        seed: Optional[int] = None,
        top_p: Optional[float] = None,
        budget: Optional[RunBudget] = None,
    ) -> str:
        if budget:
            budget.check_budget()
            budget.record_call()
        self.call_history.append({"messages": messages, "temperature": temperature})
        return self.canned_response

    async def ainvoke_structured(
        self,
        messages: list[BaseMessage],
        schema: type[BaseModel],
        temperature: float = 0.1,
        seed: Optional[int] = None,
        budget: Optional[RunBudget] = None,
    ) -> BaseModel:
        if budget:
            budget.check_budget()
            budget.record_call()
        import json
        self.call_history.append({"messages": messages, "schema": schema.__name__})
        return schema.model_validate(json.loads(self.canned_response))


def get_llm_adapter(
    provider: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> LLMProviderAdapter:
    """Factory to get the configured provider adapter with automatic fallback routing."""
    cfg = settings or get_settings()
    selected_provider = (provider or getattr(cfg, "llm_primary_provider", "groq")).lower()

    if selected_provider == "mock":
        return MockProviderAdapter()

    if selected_provider == "opencode":
        return OpenCodeZenProvider(settings=cfg)

    # Primary: Groq
    groq_adapter = GroqProviderAdapter(settings=cfg)

    # Fallback: OpenCode Zen Big Pickle (if fallback is enabled)
    fallback_adapter = None
    if getattr(cfg, "llm_fallback_enabled", True):
        fallback_adapter = OpenCodeZenProvider(settings=cfg)

    return LLMRouter(
        primary=groq_adapter,
        fallback=fallback_adapter,
        settings=cfg,
    )

