from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, List, Optional, Type, TypeVar
import structlog
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from src.config import Settings, get_settings
from src.llm.budget import BudgetExhaustedError, RunBudget
from src.llm.groq_adapter import LLMProviderAdapter
from src.llm.redaction import redact_secrets
from src.llm.exceptions import (
    ProviderError,
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


class OpenCodeZenProvider(LLMProviderAdapter):
    """OpenCode Zen LLM adapter using the Big Pickle model (https://opencode.ai/zen/v1).
    
    Serves as the automatic fallback provider when Groq experiences rate limits,
    quota exhaustion, or transient server outages.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.base_url = (
            base_url
            or getattr(self.settings, "opencode_base_url", None)
            or "https://opencode.ai/zen/v1"
        )
        self.model = (
            model
            or getattr(self.settings, "opencode_model", None)
            or "big-pickle"
        )
        if api_key is not None:
            self.api_key = api_key.strip() if api_key else ""
        else:
            discovered = (
                self.settings.get_opencode_key() if hasattr(self.settings, "get_opencode_key") else None
            )
            self.api_key = discovered.strip() if discovered else None

        self.is_valid: bool = True
        self.cooldown_until: float = 0.0
        self.failure_count: int = 0
        self.total_calls: int = 0
        self.last_error: Optional[str] = None

    def is_available(self, current_time: Optional[float] = None) -> bool:
        """Return True if credentials exist, are valid, and not in cooldown."""
        if not self.api_key or not self.is_valid:
            return False
        now = current_time if current_time is not None else time.time()
        return self.cooldown_until <= now

    def get_status(self) -> dict[str, Any]:
        """Observable status without leaking secrets."""
        now = time.time()
        return {
            "provider": "opencode",
            "model": self.model,
            "base_url": self.base_url,
            "configured": bool(self.api_key),
            "is_valid": self.is_valid,
            "is_available": self.is_available(now),
            "cooldown_remaining_seconds": max(0.0, round(self.cooldown_until - now, 1)),
            "failure_count": self.failure_count,
            "total_calls": self.total_calls,
        }

    def _redact(self, msg: str) -> str:
        secrets = [self.api_key] if self.api_key else []
        return redact_secrets(msg, secrets)

    def _handle_failure(self, error: Exception) -> Exception:
        """Map raw client exception into typed LLM exceptions and update health."""
        now = time.time()
        self.failure_count += 1
        err_str = str(error)
        redacted = self._redact(err_str)
        self.last_error = redacted
        lower_err = err_str.lower()

        # 1. Authentication / Permission Error (401 / 403 / FreeTierError)
        if (
            "401" in err_str
            or "403" in err_str
            or "unauthorized" in lower_err
            or "forbidden" in lower_err
            or "invalid_api_key" in lower_err
            or "freetiererror" in lower_err
        ):
            self.is_valid = False
            self.cooldown_until = now + 86400  # 24 hours
            logger.warning("opencode.authentication_error", error=redacted)
            return AuthenticationError(
                f"OpenCode Zen authentication failed: {redacted}",
                provider="opencode",
                status_code=403 if "403" in err_str or "freetier" in lower_err else 401,
            )

        # 2. Quota / Credit exhaustion
        if (
            "insufficient_quota" in lower_err
            or "exceeded your current quota" in lower_err
            or "credit" in lower_err
        ):
            self.cooldown_until = now + 3600
            logger.warning("opencode.quota_exhausted", error=redacted)
            return QuotaExhaustedError(
                f"OpenCode Zen quota or credits exhausted: {redacted}",
                provider="opencode",
                status_code=429,
            )

        # 3. Rate Limit (429)
        if "429" in err_str or "rate_limit" in lower_err or "too many requests" in lower_err:
            cooldown = 15.0 * min(self.failure_count, 4)
            self.cooldown_until = now + cooldown
            logger.info("opencode.rate_limited", cooldown_seconds=cooldown)
            return RateLimitExceededError(
                f"OpenCode Zen rate limited: {redacted}",
                provider="opencode",
                retry_after=cooldown,
            )

        # 4. Bad Request (400)
        if "400" in err_str or "invalid_request_error" in lower_err:
            logger.warning("opencode.bad_request", error=redacted)
            return BadRequestError(
                f"OpenCode Zen bad request (HTTP 400): {redacted}",
                provider="opencode",
                status_code=400,
            )

        # 5. Payload Too Large (413)
        if "413" in err_str or "payload too large" in lower_err or "request entity too large" in lower_err:
            logger.warning("opencode.payload_too_large", error=redacted)
            return PayloadTooLargeError(
                f"OpenCode Zen payload too large (HTTP 413): {redacted}",
                provider="opencode",
                status_code=413,
            )

        # 6. Server Temporary Error (5xx)
        if any(code in err_str for code in ["500", "502", "503", "504"]) or "server_error" in lower_err:
            backoff = min(2.0 ** self.failure_count, 30.0)
            self.cooldown_until = now + backoff
            logger.warning("opencode.server_error", backoff_seconds=backoff, error=redacted)
            return ServerTemporaryError(
                f"OpenCode Zen server temporary error: {redacted}",
                provider="opencode",
                status_code=500,
            )

        # 7. Network / Timeout
        if "timeout" in lower_err or "timed out" in lower_err or "connection" in lower_err:
            backoff = min(2.0 ** self.failure_count, 20.0)
            self.cooldown_until = now + backoff
            logger.warning("opencode.network_timeout", backoff_seconds=backoff, error=redacted)
            return NetworkTimeoutError(
                f"OpenCode Zen network timeout: {redacted}",
                provider="opencode",
            )

        # Default fallback provider error
        return ProviderError(f"OpenCode Zen provider error: {redacted}", provider="opencode")

    async def ainvoke(
        self,
        messages: List[BaseMessage],
        temperature: float = 0.2,
        seed: Optional[int] = None,
        top_p: Optional[float] = None,
        max_tokens: int = 4096,
        budget: Optional[RunBudget] = None,
    ) -> str:
        if not self.api_key:
            raise AuthenticationError(
                "OpenCode Zen API key is not configured. Set OPENCODE_API_KEY in .env.",
                provider="opencode",
                status_code=401,
            )

        if not self.is_valid:
            raise AuthenticationError(
                "OpenCode Zen API key is marked invalid or unauthorized (401/403).",
                provider="opencode",
                status_code=401,
            )

        now = time.time()
        if self.cooldown_until > now:
            wait_time = self.cooldown_until - now
            raise RateLimitExceededError(
                f"OpenCode Zen is in cooldown for {wait_time:.1f}s.",
                provider="opencode",
                retry_after=wait_time,
            )

        if budget:
            budget.check_budget()
            budget.record_call()

        self.total_calls += 1

        try:
            client_kwargs: dict[str, Any] = {
                "model": self.model,
                "api_key": self.api_key,
                "base_url": self.base_url,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "max_retries": 0,
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

            # Success: reset failure count
            self.failure_count = 0
            return content

        except asyncio.CancelledError:
            # Cancellation must never be intercepted or disguised as a provider failure
            raise
        except BudgetExhaustedError:
            raise
        except Exception as exc:
            mapped_exc = self._handle_failure(exc)
            raise mapped_exc from exc

    async def ainvoke_structured(
        self,
        messages: List[BaseMessage],
        schema: Type[T],
        temperature: float = 0.1,
        seed: Optional[int] = None,
        max_tokens: int = 4096,
        budget: Optional[RunBudget] = None,
    ) -> T:
        """Enforce structured JSON output conforming to Pydantic schema."""
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
            # Outermost brace fallback
            first_brace = cleaned.find("{")
            last_brace = cleaned.rfind("}")
            if first_brace != -1 and last_brace > first_brace:
                try:
                    candidate = cleaned[first_brace : last_brace + 1]
                    parsed_dict = json.loads(candidate)
                    return schema.model_validate(parsed_dict)
                except Exception:
                    pass

            logger.warning("opencode.structured_output_parse_error", raw=cleaned[:200])
            # Recovery parse if wrapped in a dictionary key
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
            raise ProviderError(
                f"Failed to parse OpenCode Zen response into schema {schema.__name__}",
                provider="opencode",
            )
