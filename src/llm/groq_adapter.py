from __future__ import annotations

import asyncio
import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, List, Optional, Type, TypeVar
import structlog
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from src.config import Settings, get_settings
from src.llm.budget import BudgetExhaustedError, RunBudget
from src.llm.redaction import redact_secrets
from src.llm.exceptions import (
    ProviderError,
    AllKeysFailedError,
    AuthenticationError,
    RateLimitExceededError,
    QuotaExhaustedError,
    ServerTemporaryError,
    NetworkTimeoutError,
    BadRequestError,
    PayloadTooLargeError,
)

logger = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


@dataclass
class KeyHealth:
    slot_name: str
    key: str
    is_valid: bool = True
    cooldown_until: float = 0.0
    failure_count: int = 0
    total_calls: int = 0
    last_error: Optional[str] = None

    def is_available(self, current_time: Optional[float] = None) -> bool:
        now = current_time if current_time is not None else time.time()
        return self.is_valid and (self.cooldown_until <= now)


class LLMProviderAdapter(ABC):
    """Base interface for all LLM provider adapters."""

    @abstractmethod
    async def ainvoke(
        self,
        messages: List[BaseMessage],
        temperature: float = 0.2,
        seed: Optional[int] = None,
        top_p: Optional[float] = None,
        budget: Optional[RunBudget] = None,
    ) -> str:
        """Execute a text generation call across configured keys with failover."""
        pass

    @abstractmethod
    async def ainvoke_structured(
        self,
        messages: List[BaseMessage],
        schema: Type[T],
        temperature: float = 0.1,
        seed: Optional[int] = None,
        budget: Optional[RunBudget] = None,
    ) -> T:
        """Execute and parse structured Pydantic response."""
        pass


class GroqProviderAdapter(LLMProviderAdapter):
    """Groq LLM adapter supporting 1-5 keys with priority failover, cooldown, and secret redaction."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        keys: Optional[List[tuple[str, str]]] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.base_url = base_url or getattr(self.settings, "groq_base_url", "https://api.groq.com/openai/v1")
        self.model = model or getattr(self.settings, "groq_model", "llama-3.3-70b-versatile")
        
        self.fallback_models = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]

        # Discover keys in priority order: GROQ_API_KEY_1..5, fallback to GROQ_API_KEY
        self.key_pool: List[KeyHealth] = []
        self._last_used_index: int = -1
        configured_keys = keys if keys is not None else self._discover_keys()
        for slot, key in configured_keys:
            cleaned = key.strip() if key else ""
            if cleaned:
                self.key_pool.append(KeyHealth(slot_name=slot, key=cleaned))

    def _discover_keys(self) -> List[tuple[str, str]]:
        discovered: List[tuple[str, str]] = []
        for i in range(1, 6):
            slot = f"GROQ_API_KEY_{i}"
            val = getattr(self.settings, f"groq_api_key_{i}", None)
            if val and val.strip():
                discovered.append((slot, val.strip()))
        
        # Fallback to single GROQ_API_KEY if no indexed keys are configured
        if not discovered:
            single = getattr(self.settings, "groq_api_key", None)
            if single and single.strip():
                discovered.append(("GROQ_API_KEY", single.strip()))
        return discovered

    def get_status(self) -> dict[str, Any]:
        """Observable provider and key-chain status without exposing secret values."""
        now = time.time()
        return {
            "provider": "groq",
            "model": self.model,
            "configured_keys_count": len(self.key_pool),
            "available_keys_count": sum(1 for k in self.key_pool if k.is_available(now)),
            "slots": [
                {
                    "slot_name": k.slot_name,
                    "is_valid": k.is_valid,
                    "is_available": k.is_available(now),
                    "failure_count": k.failure_count,
                    "total_calls": k.total_calls,
                }
                for k in self.key_pool
            ],
        }

    @property
    def raw_keys(self) -> List[str]:
        return [kh.key for kh in self.key_pool]

    def get_next_available_key(self, current_time: Optional[float] = None) -> Optional[KeyHealth]:
        """Return the next eligible key in round-robin sequence not currently in cooldown."""
        if not self.key_pool:
            return None
        now = current_time if current_time is not None else time.time()
        n = len(self.key_pool)
        for offset in range(n):
            idx = (self._last_used_index + 1 + offset) % n
            kh = self.key_pool[idx]
            if kh.is_available(now):
                self._last_used_index = idx
                return kh
        return None

    def _redact(self, msg: str) -> str:
        return redact_secrets(msg, self.raw_keys)

    def _parse_retry_after(self, error_msg: str, default_seconds: float = 15.0) -> float:
        """Extract Retry-After duration from error text or headers if present."""
        match = re.search(r"try again in ([\d\.]+)s", error_msg, re.IGNORECASE)
        if match:
            try:
                return max(1.0, float(match.group(1)))
            except ValueError:
                pass
        match_hdr = re.search(r"retry-after[:\s]+(\d+)", error_msg, re.IGNORECASE)
        if match_hdr:
            try:
                return max(1.0, float(match_hdr.group(1)))
            except ValueError:
                pass
        return default_seconds

    def _handle_failure(self, key_health: KeyHealth, error: Exception) -> float:
        """Update key health based on failure category and return backoff duration."""
        err_str = str(error)
        key_health.failure_count += 1
        key_health.last_error = self._redact(err_str)
        now = time.time()

        # Case 1: Unauthorized or Invalid Key (401/403)
        if "401" in err_str or "403" in err_str or "invalid_api_key" in err_str.lower():
            key_health.is_valid = False
            key_health.cooldown_until = now + 86400  # 24 hour cooldown
            logger.warning(
                "groq.key_invalid",
                slot=key_health.slot_name,
                reason="Key invalid or unauthorized (401/403)",
            )
            # If all keys are now invalid, raise immediately
            if not any(k.is_valid for k in self.key_pool):
                raise AuthenticationError(
                    f"All configured Groq API keys are invalid or revoked (401/403): {self._redact(err_str)}",
                    provider="groq",
                    status_code=401,
                )
            return 0.0

        # Case 2: Quota / Credit exhaustion
        if "insufficient_quota" in err_str.lower() or "exceeded your current quota" in err_str.lower() or "credit" in err_str.lower():
            logger.warning("groq.quota_exhausted", slot=key_health.slot_name)
            raise QuotaExhaustedError(
                f"Groq quota or credits exhausted: {self._redact(err_str)}",
                provider="groq",
                status_code=429,
            )

        # Case 3: Rate limit (429)
        if "429" in err_str or "rate_limit" in err_str.lower():
            retry_after = self._parse_retry_after(err_str, default_seconds=15.0)
            key_health.cooldown_until = now + retry_after
            logger.info(
                "groq.key_rate_limited",
                slot=key_health.slot_name,
                retry_after_seconds=retry_after,
            )
            return retry_after

        # Case 4: Model Not Found (404) or Invalid Request (400)
        # These are client errors that switching keys will NOT resolve.
        if "404" in err_str or "model_not_found" in err_str.lower():
            logger.warning("groq.model_not_found", model=self.model, slot=key_health.slot_name)
            for candidate in self.fallback_models:
                if candidate != self.model:
                    logger.info("groq.switching_fallback_model", old=self.model, new=candidate)
                    self.model = candidate
                    return 0.0
            raise ProviderError(f"Groq model '{self.model}' not found or inaccessible: {self._redact(err_str)}", provider="groq", status_code=404)

        if "400" in err_str or "invalid_request_error" in err_str.lower():
            raise BadRequestError(f"Invalid Groq request (HTTP 400): {self._redact(err_str)}", provider="groq", status_code=400)

        if "413" in err_str or "payload too large" in err_str.lower() or "request entity too large" in err_str.lower():
            raise PayloadTooLargeError(f"Groq request payload too large (HTTP 413): {self._redact(err_str)}", provider="groq", status_code=413)

        # Case 5: Transient network or 5xx server error
        backoff = min(2.0 ** key_health.failure_count, 30.0)
        key_health.cooldown_until = now + backoff
        logger.warning(
            "groq.key_transient_error",
            slot=key_health.slot_name,
            backoff_seconds=backoff,
        )
        return backoff

    async def ainvoke(
        self,
        messages: List[BaseMessage],
        temperature: float = 0.2,
        seed: Optional[int] = None,
        top_p: Optional[float] = None,
        max_tokens: int = 4096,
        budget: Optional[RunBudget] = None,
    ) -> str:
        if not self.key_pool:
            raise ProviderError("No Groq API keys configured. Set GROQ_API_KEY_1..5 in .env.")

        attempted_slots: List[str] = []
        max_attempts = len(self.key_pool)

        while True:
            if budget:
                budget.check_budget()

            key_health = self.get_next_available_key()
            if not key_health:
                # Check if all keys are invalid vs in temporary cooldown
                valid_keys = [k for k in self.key_pool if k.is_valid]
                if not valid_keys:
                    raise AllKeysFailedError(
                        "All configured Groq API keys are invalid or revoked (401/403)."
                    )
                # Short wait if keys are in cooldown and time remains
                min_cooldown = min(k.cooldown_until for k in valid_keys)
                wait_time = max(0.0, min_cooldown - time.time())
                if budget and (budget.elapsed_time() + wait_time >= budget.max_time_seconds):
                    raise BudgetExhaustedError("Run time budget exceeded while awaiting key cooldown.")

                # If all keys were checked once, but the earliest key cools down in <= 15s,
                # respect Retry-After by waiting for it (up to 2 cycles)
                if wait_time <= 15.0 and len(attempted_slots) < max_attempts * 2:
                    logger.info(
                        "groq.awaiting_key_cooldown",
                        wait_seconds=round(wait_time, 2),
                        attempted=len(attempted_slots),
                    )
                    await asyncio.sleep(wait_time + 0.2)
                    continue

                raise AllKeysFailedError(
                    f"All Groq keys are currently rate-limited or in cooldown (wait {wait_time:.1f}s). Attempted: {attempted_slots}"
                )

            if budget:
                budget.record_call()

            key_health.total_calls += 1
            attempted_slots.append(key_health.slot_name)

            try:
                client_kwargs: dict[str, Any] = {
                    "model": self.model,
                    "api_key": key_health.key,
                    "base_url": self.base_url,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "max_retries": 0,  # Instant failover across key pool handled by GroqProviderAdapter
                }
                model_kwargs: dict[str, Any] = {}
                if seed is not None:
                    model_kwargs["seed"] = seed
                if top_p is not None:
                    model_kwargs["top_p"] = top_p
                if model_kwargs:
                    client_kwargs["model_kwargs"] = model_kwargs

                client = ChatOpenAI(**client_kwargs)
                response = await client.ainvoke(messages)
                content = response.content if hasattr(response, "content") else str(response)

                # Reset failure count on success
                key_health.failure_count = 0
                return content

            except (BudgetExhaustedError, ProviderError):
                raise
            except Exception as exc:
                self._handle_failure(key_health, exc)
                if len(attempted_slots) >= max_attempts * 2:
                    raise AllKeysFailedError(
                        f"All keys exhausted after multiple retry attempts. Error: {self._redact(str(exc))}"
                    )

    async def ainvoke_structured(
        self,
        messages: List[BaseMessage],
        schema: Type[T],
        temperature: float = 0.1,
        seed: Optional[int] = None,
        max_tokens: int = 4096,
        budget: Optional[RunBudget] = None,
    ) -> T:
        """Enforce structured JSON output matching Pydantic schema."""
        json_schema_prompt = (
            f"\n\nYou MUST respond with valid JSON strictly conforming to this JSON Schema:\n"
            f"{json.dumps(schema.model_json_schema(), indent=2)}\n"
            f"Do not include any text before or after the JSON. Return only the JSON object."
        )
        augmented_messages = list(messages)
        if augmented_messages and isinstance(augmented_messages[-1], HumanMessage):
            last_msg = augmented_messages[-1]
            augmented_messages[-1] = HumanMessage(content=str(last_msg.content) + json_schema_prompt)
        else:
            augmented_messages.append(HumanMessage(content=json_schema_prompt))

        raw_response = await self.ainvoke(
            augmented_messages,
            temperature=temperature,
            seed=seed,
            max_tokens=max_tokens,
            budget=budget,
        )

        cleaned = raw_response.strip()
        if cleaned.startswith("```json") and cleaned.endswith("```"):
            cleaned = cleaned[7:-3].strip()
        elif cleaned.startswith("```") and cleaned.endswith("```"):
            cleaned = cleaned[3:-3].strip()

        try:
            parsed_dict = json.loads(cleaned)
            return schema.model_validate(parsed_dict)
        except Exception:
            # Fallback: find outermost JSON object { ... }
            first_brace = cleaned.find("{")
            last_brace = cleaned.rfind("}")
            if first_brace != -1 and last_brace > first_brace:
                try:
                    candidate = cleaned[first_brace : last_brace + 1]
                    parsed_dict = json.loads(candidate)
                    return schema.model_validate(parsed_dict)
                except Exception:
                    pass

            logger.warning("groq.structured_output_parse_error", raw=cleaned[:200])
            # Attempt a recovery repair parse if it was wrapped in a sub-key
            try:
                data = json.loads(cleaned)
                if isinstance(data, dict):
                    for val in data.values():
                        if isinstance(val, dict):
                            try:
                                return schema.model_validate(val)
                            except Exception:
                                continue
            except Exception:
                pass
            raise ProviderError(f"Failed to parse LLM response into schema {schema.__name__}")
