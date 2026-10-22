from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch
import pytest
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from src.llm.budget import BudgetExhaustedError, RunBudget
from src.llm.groq_adapter import (
    AllKeysFailedError,
    GroqProviderAdapter,
    KeyHealth,
    ProviderError,
)
from src.llm.redaction import redact_secrets


class TestRedaction:
    def test_redact_custom_keys(self):
        text = "Request with gsk_secretkey123456789012345 failed"
        redacted = redact_secrets(text, custom_secrets=["gsk_secretkey123456789012345"])
        assert "gsk_secretkey123456789012345" not in redacted
        assert "[REDACTED_SECRET]" in redacted

    def test_redact_pattern_keys(self):
        text = "Found key gsk_abcdef123456789012345678 and sk-1234567890123456789012 in logs"
        redacted = redact_secrets(text)
        assert "gsk_abcdef123456789012345678" not in redacted
        assert "sk-1234567890123456789012" not in redacted
        assert "[REDACTED_SECRET]" in redacted


class TestRunBudget:
    def test_budget_call_limit(self):
        budget = RunBudget(max_calls=2, max_time_seconds=60.0)
        budget.check_budget()
        budget.record_call()
        budget.check_budget()
        budget.record_call()
        with pytest.raises(BudgetExhaustedError, match="call budget exhausted"):
            budget.check_budget()

    def test_budget_time_limit(self):
        budget = RunBudget(max_calls=10, max_time_seconds=0.01)
        time.sleep(0.02)
        with pytest.raises(BudgetExhaustedError, match="time budget exhausted"):
            budget.check_budget()


class TestGroqKeyChaining:
    def test_blank_keys_filtered(self):
        keys = [
            ("GROQ_API_KEY_1", "   "),
            ("GROQ_API_KEY_2", "gsk_valid_key_2_12345678901"),
            ("GROQ_API_KEY_3", ""),
            ("GROQ_API_KEY_4", "gsk_valid_key_4_12345678901"),
        ]
        adapter = GroqProviderAdapter(keys=keys)
        assert len(adapter.key_pool) == 2
        assert adapter.key_pool[0].slot_name == "GROQ_API_KEY_2"
        assert adapter.key_pool[1].slot_name == "GROQ_API_KEY_4"

    def test_priority_rotation_order(self):
        keys = [
            ("GROQ_API_KEY_1", "gsk_key1_123456789012345"),
            ("GROQ_API_KEY_2", "gsk_key2_123456789012345"),
            ("GROQ_API_KEY_3", "gsk_key3_123456789012345"),
        ]
        adapter = GroqProviderAdapter(keys=keys)
        first = adapter.get_next_available_key()
        assert first is not None
        assert first.slot_name == "GROQ_API_KEY_1"

        # Mark first in cooldown
        first.cooldown_until = time.time() + 60.0
        second = adapter.get_next_available_key()
        assert second is not None
        assert second.slot_name == "GROQ_API_KEY_2"

    @pytest.mark.asyncio
    async def test_failover_on_429_rate_limit(self):
        keys = [
            ("GROQ_API_KEY_1", "gsk_key1_123456789012345"),
            ("GROQ_API_KEY_2", "gsk_key2_123456789012345"),
        ]
        adapter = GroqProviderAdapter(keys=keys)

        call_count = 0

        async def mock_ainvoke(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Key 1 fails with 429 rate limit
                raise Exception("Rate limit reached. Please try again in 5.5s. error code: 429")
            # Key 2 succeeds
            mock_resp = AsyncMock()
            mock_resp.content = "Success from key 2"
            return mock_resp

        with patch("src.llm.groq_adapter.ChatOpenAI") as mock_chat:
            mock_instance = AsyncMock()
            mock_instance.ainvoke.side_effect = mock_ainvoke
            mock_chat.return_value = mock_instance

            res = await adapter.ainvoke([HumanMessage(content="Hello")])
            assert res == "Success from key 2"
            assert adapter.key_pool[0].failure_count == 1
            assert adapter.key_pool[0].cooldown_until > time.time()
            assert adapter.key_pool[1].failure_count == 0

    @pytest.mark.asyncio
    async def test_invalid_key_401_marked_invalid(self):
        keys = [
            ("GROQ_API_KEY_1", "gsk_badkey_12345678901234"),
            ("GROQ_API_KEY_2", "gsk_goodkey_1234567890123"),
        ]
        adapter = GroqProviderAdapter(keys=keys)

        async def mock_ainvoke(*args, **kwargs):
            if adapter.key_pool[0].is_valid:
                raise Exception("401 Unauthorized: invalid_api_key")
            mock_resp = AsyncMock()
            mock_resp.content = "Success from valid key"
            return mock_resp

        with patch("src.llm.groq_adapter.ChatOpenAI") as mock_chat:
            mock_instance = AsyncMock()
            mock_instance.ainvoke.side_effect = mock_ainvoke
            mock_chat.return_value = mock_instance

            res = await adapter.ainvoke([HumanMessage(content="Hello")])
            assert res == "Success from valid key"
            assert adapter.key_pool[0].is_valid is False
            # Next selection should skip key 1 permanently
            next_key = adapter.get_next_available_key()
            assert next_key is not None
            assert next_key.slot_name == "GROQ_API_KEY_2"

    @pytest.mark.asyncio
    async def test_all_keys_failed_raises_all_keys_failed_error(self):
        keys = [
            ("GROQ_API_KEY_1", "gsk_key1_123456789012345"),
            ("GROQ_API_KEY_2", "gsk_key2_123456789012345"),
        ]
        adapter = GroqProviderAdapter(keys=keys)

        with patch("src.llm.groq_adapter.ChatOpenAI") as mock_chat:
            mock_instance = AsyncMock()
            mock_instance.ainvoke.side_effect = Exception("401 Unauthorized: invalid_api_key")
            mock_chat.return_value = mock_instance

            with pytest.raises(AllKeysFailedError, match="invalid or revoked"):
                await adapter.ainvoke([HumanMessage(content="Hello")])

    @pytest.mark.asyncio
    async def test_structured_output_parsing(self):
        class PlanSchema(BaseModel):
            summary: str
            confidence: float

        keys = [("GROQ_API_KEY_1", "gsk_test_123456789012345")]
        adapter = GroqProviderAdapter(keys=keys)

        with patch("src.llm.groq_adapter.ChatOpenAI") as mock_chat:
            mock_instance = AsyncMock()
            mock_resp = AsyncMock()
            mock_resp.content = '```json\n{"summary": "Test Plan", "confidence": 0.95}\n```'
            mock_instance.ainvoke.return_value = mock_resp
            mock_chat.return_value = mock_instance

            plan = await adapter.ainvoke_structured([HumanMessage(content="Plan")], PlanSchema)
            assert plan.summary == "Test Plan"
            assert plan.confidence == 0.95

    @pytest.mark.asyncio
    async def test_invalid_request_400_fails_immediately_without_key_cycling(self):
        keys = [
            ("GROQ_API_KEY_1", "gsk_key1_123456789012345"),
            ("GROQ_API_KEY_2", "gsk_key2_123456789012345"),
        ]
        adapter = GroqProviderAdapter(keys=keys)

        with patch("src.llm.groq_adapter.ChatOpenAI") as mock_chat:
            mock_instance = AsyncMock()
            mock_instance.ainvoke.side_effect = Exception("400 Bad Request: invalid_request_error")
            mock_chat.return_value = mock_instance

            with pytest.raises(ProviderError, match="Invalid Groq request"):
                await adapter.ainvoke([HumanMessage(content="Bad request")])

            # Key 2 was NOT attempted
            assert adapter.key_pool[1].total_calls == 0

    @pytest.mark.asyncio
    async def test_model_not_found_switches_to_fallback_model(self):
        keys = [("GROQ_API_KEY_1", "gsk_key1_123456789012345")]
        adapter = GroqProviderAdapter(keys=keys, model="invalid-model-name")

        call_count = 0

        async def mock_invoke(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("404 Not Found: model_not_found")
            mock_resp = AsyncMock()
            mock_resp.content = "Success on fallback model"
            return mock_resp

        with patch("src.llm.groq_adapter.ChatOpenAI") as mock_chat:
            mock_instance = AsyncMock()
            mock_instance.ainvoke.side_effect = mock_invoke
            mock_chat.return_value = mock_instance

            res = await adapter.ainvoke([HumanMessage(content="Hello")])
            assert res == "Success on fallback model"
            assert adapter.model == "openai/gpt-oss-120b"

    def test_get_status_does_not_leak_secrets(self):
        keys = [
            ("GROQ_API_KEY_1", "gsk_super_secret_key_123456"),
            ("GROQ_API_KEY_2", "gsk_another_secret_key_789012"),
        ]
        adapter = GroqProviderAdapter(keys=keys, model="openai/gpt-oss-120b")
        status = adapter.get_status()

        assert status["provider"] == "groq"
        assert status["model"] == "openai/gpt-oss-120b"
        assert status["configured_keys_count"] == 2
        assert status["available_keys_count"] == 2
        assert len(status["slots"]) == 2
        assert status["slots"][0]["slot_name"] == "GROQ_API_KEY_1"

        # Ensure raw keys are not present in serialized status
        import json
        status_json = json.dumps(status)
        assert "gsk_super_secret_key_123456" not in status_json
        assert "gsk_another_secret_key_789012" not in status_json
