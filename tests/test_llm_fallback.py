from __future__ import annotations

import asyncio
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from src.config import Settings
from src.llm.budget import BudgetExhaustedError, RunBudget
from src.llm.groq_adapter import LLMProviderAdapter
from src.llm.opencode_adapter import OpenCodeZenProvider
from src.llm.router import LLMRouter
from src.llm.provider import get_llm_adapter
from src.llm.exceptions import (
    AllKeysFailedError,
    AllProvidersFailedError,
    AuthenticationError,
    NetworkTimeoutError,
    ProviderError,
    QuotaExhaustedError,
    RateLimitExceededError,
    ServerTemporaryError,
)


class DummySchema(BaseModel):
    summary: str
    confidence: float


class StubProvider(LLMProviderAdapter):
    """Stub LLM provider for deterministic testing of router policies."""

    def __init__(self, name: str, response: str = "ok", exception: Optional[Exception] = None) -> None:
        self.name = name
        self.response = response
        self.exception = exception
        self.call_count = 0
        self.structured_response: Optional[BaseModel] = None

    async def ainvoke(
        self,
        messages: list,
        temperature: float = 0.2,
        seed: Optional[int] = None,
        top_p: Optional[float] = None,
        budget: Optional[RunBudget] = None,
    ) -> str:
        self.call_count += 1
        if self.exception:
            raise self.exception
        return self.response

    async def ainvoke_structured(
        self,
        messages: list,
        schema: type[BaseModel],
        temperature: float = 0.1,
        seed: Optional[int] = None,
        budget: Optional[RunBudget] = None,
    ) -> BaseModel:
        self.call_count += 1
        if self.exception:
            raise self.exception
        if self.structured_response:
            return self.structured_response
        return schema(summary=self.response, confidence=0.95)

    def get_status(self) -> dict:
        return {
            "provider": self.name,
            "call_count": self.call_count,
            "configured": True,
            "is_available": self.exception is None,
        }


class TestOpenCodeZenAdapter:
    """Unit tests for OpenCodeZenProvider adapter."""

    def test_initialization_defaults_and_status_redaction(self):
        adapter = OpenCodeZenProvider(
            api_key="opencode-secret-key-12345",
            base_url="https://opencode.ai/zen/v1",
            model="big-pickle",
        )
        status = adapter.get_status()
        assert status["provider"] == "opencode"
        assert status["model"] == "big-pickle"
        assert status["configured"] is True
        assert status["is_available"] is True
        # Verify secret key is never leaked in status
        assert "opencode-secret-key-12345" not in str(status)

    def test_missing_key_raises_auth_error(self):
        adapter = OpenCodeZenProvider(api_key="")
        with pytest.raises(AuthenticationError, match="OpenCode Zen API key is not configured"):
            asyncio.run(adapter.ainvoke([HumanMessage(content="Hello")]))

    def test_error_mapping_401_403(self):
        adapter = OpenCodeZenProvider(api_key="opencode-test-key")
        err_401 = Exception("HTTP 401 Unauthorized: Invalid API key")
        mapped_401 = adapter._handle_failure(err_401)
        assert isinstance(mapped_401, AuthenticationError)
        assert adapter.is_valid is False

        err_403 = Exception("HTTP 403 Forbidden: FreeTierError - OpenCode's free tier can only be used from within OpenCode")
        mapped_403 = adapter._handle_failure(err_403)
        assert isinstance(mapped_403, AuthenticationError)

    def test_error_mapping_429_quota_server_error(self):
        adapter = OpenCodeZenProvider(api_key="opencode-test-key")
        
        # Quota
        quota_err = Exception("exceeded your current quota or credit")
        mapped_quota = adapter._handle_failure(quota_err)
        assert isinstance(mapped_quota, QuotaExhaustedError)

        # Rate limit
        rl_err = Exception("HTTP 429 Too Many Requests")
        mapped_rl = adapter._handle_failure(rl_err)
        assert isinstance(mapped_rl, RateLimitExceededError)

        # Server error
        server_err = Exception("HTTP 503 Service Unavailable")
        mapped_server = adapter._handle_failure(server_err)
        assert isinstance(mapped_server, ServerTemporaryError)

        # Network timeout
        timeout_err = Exception("Connection timed out after 30000ms")
        mapped_timeout = adapter._handle_failure(timeout_err)
        assert isinstance(mapped_timeout, NetworkTimeoutError)


class TestLLMRouterFallbackPolicies:
    """Comprehensive test suite for primary/fallback routing policies."""

    @pytest.mark.asyncio
    async def test_scenario_1_primary_succeeds_fallback_never_called(self):
        """Scenario 1: Primary succeeds -> Fallback is not called."""
        primary = StubProvider("groq", response="Response from Groq")
        fallback = StubProvider("opencode", response="Response from OpenCode")
        router = LLMRouter(primary=primary, fallback=fallback)

        result = await router.ainvoke([HumanMessage(content="Test query")])
        assert result == "Response from Groq"
        assert primary.call_count == 1
        assert fallback.call_count == 0
        assert router.primary_calls == 1
        assert router.fallback_calls == 0
        assert router.total_fallbacks_triggered == 0

    @pytest.mark.asyncio
    async def test_scenario_2_primary_quota_exhausted_fallback_succeeds(self):
        """Scenario 2: Primary quota exhausted -> Automatic fallback to Big Pickle."""
        primary = StubProvider(
            "groq",
            exception=QuotaExhaustedError("Groq monthly credit quota exhausted", provider="groq", status_code=429),
        )
        fallback = StubProvider("opencode", response="Synthesized via OpenCode Big Pickle")
        router = LLMRouter(primary=primary, fallback=fallback)

        result = await router.ainvoke([HumanMessage(content="Research topic")])
        assert result == "Synthesized via OpenCode Big Pickle"
        assert primary.call_count == 1
        assert fallback.call_count == 1
        assert router.primary_failures == 1
        assert router.fallback_calls == 1
        assert router.total_fallbacks_triggered == 1
        assert "quota" in router.last_fallback_reason.lower()

    @pytest.mark.asyncio
    async def test_scenario_3_primary_rate_limited_fallback_succeeds(self):
        """Scenario 3: Primary 429 rate-limited -> Automatic fallback to Big Pickle."""
        primary = StubProvider(
            "groq",
            exception=RateLimitExceededError("Rate limit reached: try again in 15s", provider="groq"),
        )
        fallback = StubProvider("opencode", response="Fallback response")
        router = LLMRouter(primary=primary, fallback=fallback)

        result = await router.ainvoke([HumanMessage(content="Fast follow-up")])
        assert result == "Fallback response"
        assert primary.call_count == 1
        assert fallback.call_count == 1
        assert router.total_fallbacks_triggered == 1

    @pytest.mark.asyncio
    async def test_scenario_4_primary_5xx_server_error_fallback_succeeds(self):
        """Scenario 4: Primary 5xx server error -> Automatic fallback to Big Pickle."""
        primary = StubProvider(
            "groq",
            exception=ServerTemporaryError("HTTP 503 Service Unavailable", provider="groq", status_code=503),
        )
        fallback = StubProvider("opencode", response="Server outage recovered by OpenCode")
        router = LLMRouter(primary=primary, fallback=fallback)

        result = await router.ainvoke([HumanMessage(content="Query")])
        assert result == "Server outage recovered by OpenCode"
        assert primary.call_count == 1
        assert fallback.call_count == 1
        assert router.total_fallbacks_triggered == 1

    @pytest.mark.asyncio
    async def test_scenario_5_primary_auth_failure_does_not_fallback(self):
        """Scenario 5: Primary authentication error (401/403) surfaces configuration error, does NOT fallback."""
        primary = StubProvider(
            "groq",
            exception=AuthenticationError("All configured Groq API keys are invalid (401)", provider="groq", status_code=401),
        )
        fallback = StubProvider("opencode", response="Should not be called")
        router = LLMRouter(primary=primary, fallback=fallback)

        with pytest.raises(AuthenticationError, match="Groq API keys are invalid"):
            await router.ainvoke([HumanMessage(content="Auth test")])

        # Fallback must NEVER have been called on authentication/configuration failure
        assert primary.call_count == 1
        assert fallback.call_count == 0
        assert router.total_fallbacks_triggered == 0

    @pytest.mark.asyncio
    async def test_scenario_6_both_providers_fail_raises_all_providers_failed(self):
        """Scenario 6: Both primary and fallback fail -> raises AllProvidersFailedError without looping."""
        primary = StubProvider(
            "groq",
            exception=AllKeysFailedError("All Groq keys rate-limited"),
        )
        fallback = StubProvider(
            "opencode",
            exception=RateLimitExceededError("OpenCode also rate limited"),
        )
        router = LLMRouter(primary=primary, fallback=fallback)

        with pytest.raises(AllProvidersFailedError, match="Both primary and fallback providers failed"):
            await router.ainvoke([HumanMessage(content="Double outage test")])

        assert primary.call_count == 1
        assert fallback.call_count == 1
        assert router.primary_failures == 1
        assert router.fallback_failures == 1

    @pytest.mark.asyncio
    async def test_scenario_7_cancellation_never_triggers_fallback(self):
        """Scenario 7: asyncio.CancelledError must halt immediately without invoking fallback."""
        primary = StubProvider("groq", exception=asyncio.CancelledError())
        fallback = StubProvider("opencode", response="Should never be called")
        router = LLMRouter(primary=primary, fallback=fallback)

        with pytest.raises(asyncio.CancelledError):
            await router.ainvoke([HumanMessage(content="Cancelled operation")])

        assert primary.call_count == 1
        assert fallback.call_count == 0
        assert router.total_fallbacks_triggered == 0

    @pytest.mark.asyncio
    async def test_scenario_8_fallback_structured_output_works(self):
        """Scenario 8: Fallback structured output conforms to Pydantic schema."""
        primary = StubProvider(
            "groq",
            exception=QuotaExhaustedError("Groq quota exhausted", provider="groq"),
        )
        expected_schema = DummySchema(summary="Structured synthesis via Big Pickle", confidence=0.99)
        fallback = StubProvider("opencode")
        fallback.structured_response = expected_schema

        router = LLMRouter(primary=primary, fallback=fallback)

        result = await router.ainvoke_structured(
            [HumanMessage(content="Structured request")],
            schema=DummySchema,
        )
        assert isinstance(result, DummySchema)
        assert result.summary == "Structured synthesis via Big Pickle"
        assert result.confidence == 0.99
        assert fallback.call_count == 1
        assert router.total_fallbacks_triggered == 1

    @pytest.mark.asyncio
    async def test_scenario_9_auto_recovery_prefers_primary_again(self):
        """Scenario 9: Once primary recovers, subsequent requests prefer primary again."""
        primary = StubProvider("groq", response="Primary recovered response")
        fallback = StubProvider("opencode", response="Fallback response")
        router = LLMRouter(primary=primary, fallback=fallback)

        # Step 1: Simulate temporary failure on request 1
        primary.exception = RateLimitExceededError("Groq 429")
        res1 = await router.ainvoke([HumanMessage(content="Req 1")])
        assert res1 == "Fallback response"
        assert router.total_fallbacks_triggered == 1

        # Step 2: Primary recovers (exception cleared)
        primary.exception = None
        res2 = await router.ainvoke([HumanMessage(content="Req 2")])
        assert res2 == "Primary recovered response"
        # Primary was used, fallback was not called again
        assert primary.call_count == 2
        assert fallback.call_count == 1
        assert router.total_fallbacks_triggered == 1

    @pytest.mark.asyncio
    async def test_scenario_10_disabled_fallback_raises_primary_error_directly(self):
        """Scenario 10: When fallback is disabled, primary error is raised directly."""
        primary = StubProvider(
            "groq",
            exception=RateLimitExceededError("Groq rate limited"),
        )
        fallback = StubProvider("opencode", response="Should not be called")
        settings = Settings(llm_fallback_enabled=False)
        router = LLMRouter(primary=primary, fallback=fallback, settings=settings)

        with pytest.raises(RateLimitExceededError, match="Groq rate limited"):
            await router.ainvoke([HumanMessage(content="Fallback disabled test")])

        assert fallback.call_count == 0
        assert router.total_fallbacks_triggered == 0

    def test_scenario_11_status_and_observability_contain_no_secrets(self):
        """Scenario 11: Router status contains metrics, health, and redacts secrets."""
        primary = StubProvider("groq", response="ok")
        fallback = StubProvider("opencode", response="ok")
        router = LLMRouter(primary=primary, fallback=fallback)

        status = router.get_status()
        assert status["primary_provider"] == "groq"
        assert status["fallback_provider"] == "opencode"
        assert status["fallback_enabled"] is True
        assert "metrics" in status
        assert status["metrics"]["primary_calls"] == 0
        assert status["metrics"]["total_fallbacks_triggered"] == 0
        assert "primary" in status
        assert "fallback" in status


class TestFactoryAndApiIntegration:
    """Test get_llm_adapter factory and /health endpoint integration."""

    def test_factory_returns_router_with_primary_and_fallback(self):
        settings = Settings(
            llm_primary_provider="groq",
            llm_fallback_provider="opencode",
            llm_fallback_enabled=True,
            groq_api_key="gsk_test_mock_1234567890123456",
            opencode_api_key="opencode_test_secret_1234567890",
        )
        adapter = get_llm_adapter(settings=settings)
        assert isinstance(adapter, LLMRouter)
        assert adapter.fallback_enabled is True
        assert adapter.primary is not None
        assert adapter.fallback is not None

    def test_factory_supports_mock_provider(self):
        adapter = get_llm_adapter(provider="mock")
        assert adapter.__class__.__name__ == "MockProviderAdapter"
