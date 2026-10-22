from __future__ import annotations

from typing import Optional


class LLMError(Exception):
    """Base exception for all LLM errors in RAVEN."""
    pass


class ProviderError(LLMError):
    """General LLM provider failure with redacted details."""

    def __init__(self, message: str, provider: Optional[str] = None, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


class AllKeysFailedError(ProviderError):
    """Raised when every configured key in a provider's key pool has failed."""
    pass


class AuthenticationError(AllKeysFailedError):
    """Raised when provider returns HTTP 401 or 403 (Invalid credentials or unauthorized)."""
    pass


class RateLimitExceededError(ProviderError):
    """Raised when provider returns HTTP 429 (Rate limit reached)."""

    def __init__(
        self,
        message: str,
        provider: Optional[str] = None,
        retry_after: float = 15.0,
    ) -> None:
        super().__init__(message, provider=provider, status_code=429)
        self.retry_after = retry_after


class QuotaExhaustedError(ProviderError):
    """Raised when provider account has exhausted credits or quota."""
    pass


class ServerTemporaryError(ProviderError):
    """Raised when provider returns HTTP 5xx (500, 502, 503, 504)."""
    pass


class NetworkTimeoutError(ProviderError):
    """Raised on connection timeout, SSL failure, or network drop."""
    pass


class BadRequestError(ProviderError):
    """Raised when provider returns HTTP 400 (Invalid request)."""
    pass


class PayloadTooLargeError(ProviderError):
    """Raised when prompt exceeds model context window or token limits (HTTP 413)."""
    pass


class AllProvidersFailedError(ProviderError):
    """Raised when both primary and all fallback providers fail."""
    pass
