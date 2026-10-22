from __future__ import annotations

import asyncio
import time
from typing import Any, List, Optional, Type, TypeVar
import structlog
from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from src.config import Settings, get_settings
from src.llm.budget import BudgetExhaustedError, RunBudget
from src.llm.groq_adapter import LLMProviderAdapter
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

logger = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMRouter(LLMProviderAdapter):
    """Router coordinating Primary (Groq) and Automatic Fallback (OpenCode Zen Big Pickle).
    
    Guarantees:
    - Primary provider (Groq) is always attempted first when available.
    - Automatic recovery: once primary cooldown expires, subsequent requests prefer primary again.
    - Automatic fallback: when Groq encounters rate-limit, quota exhaustion, 5xx server outages,
      or all-keys-failed, the request is retried seamlessly against OpenCode Zen.
    - Authentication errors (401/403): surfaced directly as configuration errors without fallback masking.
    - Cancellation (asyncio.CancelledError): immediately stops execution without triggering fallback.
    - Secret safe: all events and statuses redact credentials.
    """

    def __init__(
        self,
        primary: Optional[LLMProviderAdapter] = None,
        fallback: Optional[LLMProviderAdapter] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.primary = primary
        self.fallback = fallback

        self.fallback_enabled = getattr(self.settings, "llm_fallback_enabled", True)
        self.max_retries = getattr(self.settings, "llm_max_retries", 2)

        # Observability metrics
        self.primary_calls: int = 0
        self.primary_failures: int = 0
        self.fallback_calls: int = 0
        self.fallback_failures: int = 0
        self.total_fallbacks_triggered: int = 0
        self.last_fallback_time: Optional[float] = None
        self.last_fallback_reason: Optional[str] = None

    def _should_trigger_fallback(self, exc: Exception) -> bool:
        """Determine if an exception warrants fallback to secondary provider."""
        if not self.fallback_enabled or not self.fallback:
            return False

        # Authentication errors are configuration failures, do NOT disguise with fallback
        if isinstance(exc, AuthenticationError):
            return False

        # Budget exhaustion is an intentional limit, do not fallback
        if isinstance(exc, BudgetExhaustedError):
            return False

        # Fallback on quota, rate limits, key exhaustions, server 5xx, or network drops
        if isinstance(
            exc,
            (
                QuotaExhaustedError,
                RateLimitExceededError,
                AllKeysFailedError,
                ServerTemporaryError,
                NetworkTimeoutError,
            ),
        ):
            return True

        # Check for error text patterns in generic ProviderError
        if isinstance(exc, ProviderError):
            err_text = str(exc).lower()
            if any(
                pattern in err_text
                for pattern in [
                    "quota",
                    "rate_limit",
                    "429",
                    "all groq keys",
                    "cooldown",
                    "500",
                    "502",
                    "503",
                    "504",
                    "timeout",
                    "temporarily unavailable",
                ]
            ):
                return True

        return False

    async def ainvoke(
        self,
        messages: List[BaseMessage],
        temperature: float = 0.2,
        seed: Optional[int] = None,
        top_p: Optional[float] = None,
        budget: Optional[RunBudget] = None,
    ) -> str:
        """Invoke primary provider with automatic failover to fallback provider."""
        if not self.primary:
            if self.fallback and self.fallback_enabled:
                return await self.fallback.ainvoke(
                    messages, temperature=temperature, seed=seed, top_p=top_p, budget=budget
                )
            raise ProviderError("No primary or fallback LLM provider configured.")

        # Attempt primary first
        try:
            self.primary_calls += 1
            result = await self.primary.ainvoke(
                messages,
                temperature=temperature,
                seed=seed,
                top_p=top_p,
                budget=budget,
            )
            return result

        except asyncio.CancelledError:
            # Cancellation must halt immediately
            raise

        except BudgetExhaustedError:
            raise

        except AuthenticationError:
            # Re-raise authentication error directly as configuration issue
            self.primary_failures += 1
            raise

        except Exception as exc:
            self.primary_failures += 1
            if not self._should_trigger_fallback(exc):
                raise

            # Fallback path
            self.total_fallbacks_triggered += 1
            self.last_fallback_time = time.time()
            self.last_fallback_reason = str(exc)

            logger.warning(
                "llm.fallback_activated",
                from_provider="groq",
                to_provider="opencode",
                reason=str(exc),
            )

            try:
                self.fallback_calls += 1
                result = await self.fallback.ainvoke(
                    messages,
                    temperature=temperature,
                    seed=seed,
                    top_p=top_p,
                    budget=budget,
                )
                return result
            except asyncio.CancelledError:
                raise
            except Exception as fb_exc:
                self.fallback_failures += 1
                logger.error(
                    "llm.all_providers_failed",
                    primary_error=str(exc),
                    fallback_error=str(fb_exc),
                )
                raise AllProvidersFailedError(
                    f"Both primary and fallback providers failed. Primary: {exc}; Fallback: {fb_exc}"
                ) from fb_exc

    async def ainvoke_structured(
        self,
        messages: List[BaseMessage],
        schema: Type[T],
        temperature: float = 0.1,
        seed: Optional[int] = None,
        budget: Optional[RunBudget] = None,
    ) -> T:
        """Structured JSON invocation with automatic failover to fallback provider."""
        if not self.primary:
            if self.fallback and self.fallback_enabled:
                return await self.fallback.ainvoke_structured(
                    messages, schema=schema, temperature=temperature, seed=seed, budget=budget
                )
            raise ProviderError("No primary or fallback LLM provider configured.")

        try:
            self.primary_calls += 1
            result = await self.primary.ainvoke_structured(
                messages,
                schema=schema,
                temperature=temperature,
                seed=seed,
                budget=budget,
            )
            return result

        except asyncio.CancelledError:
            raise

        except BudgetExhaustedError:
            raise

        except AuthenticationError:
            self.primary_failures += 1
            raise

        except Exception as exc:
            self.primary_failures += 1
            if not self._should_trigger_fallback(exc):
                raise

            self.total_fallbacks_triggered += 1
            self.last_fallback_time = time.time()
            self.last_fallback_reason = str(exc)

            logger.warning(
                "llm.fallback_activated",
                from_provider="groq",
                to_provider="opencode",
                reason=str(exc),
                schema=schema.__name__,
            )

            try:
                self.fallback_calls += 1
                result = await self.fallback.ainvoke_structured(
                    messages,
                    schema=schema,
                    temperature=temperature,
                    seed=seed,
                    budget=budget,
                )
                return result
            except asyncio.CancelledError:
                raise
            except Exception as fb_exc:
                self.fallback_failures += 1
                logger.error(
                    "llm.all_providers_failed",
                    primary_error=str(exc),
                    fallback_error=str(fb_exc),
                    schema=schema.__name__,
                )
                raise AllProvidersFailedError(
                    f"Both primary and fallback providers failed for structured output {schema.__name__}. Primary: {exc}; Fallback: {fb_exc}"
                ) from fb_exc

    def get_status(self) -> dict[str, Any]:
        """Observable provider router status and health metrics."""
        primary_status = self.primary.get_status() if hasattr(self.primary, "get_status") else {}
        fallback_status = self.fallback.get_status() if self.fallback and hasattr(self.fallback, "get_status") else None

        return {
            "primary_provider": getattr(self.settings, "llm_primary_provider", "groq"),
            "fallback_provider": getattr(self.settings, "llm_fallback_provider", "opencode"),
            "fallback_enabled": self.fallback_enabled,
            "metrics": {
                "primary_calls": self.primary_calls,
                "primary_failures": self.primary_failures,
                "fallback_calls": self.fallback_calls,
                "fallback_failures": self.fallback_failures,
                "total_fallbacks_triggered": self.total_fallbacks_triggered,
                "last_fallback_time": self.last_fallback_time,
                "last_fallback_reason": self.last_fallback_reason,
            },
            "primary": primary_status,
            "fallback": fallback_status,
            # Backwards compatibility fields for frontend/status callers
            "configured_keys_count": primary_status.get("configured_keys_count", 0),
            "available_keys_count": primary_status.get("available_keys_count", 0),
            "slots": primary_status.get("slots", []),
        }
