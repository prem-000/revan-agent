from __future__ import annotations

import re
from typing import Iterable

# Common patterns for API keys
_KEY_PATTERNS = [
    re.compile(r"gsk_[A-Za-z0-9_\-]{20,}", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}", re.IGNORECASE),
    re.compile(r"tvly-[A-Za-z0-9_\-]{20,}", re.IGNORECASE),
    re.compile(r"ghp_[A-Za-z0-9_\-]{20,}", re.IGNORECASE),
    re.compile(r"github_pat_[A-Za-z0-9_\-]{20,}", re.IGNORECASE),
]


def redact_secrets(text: str, custom_secrets: Iterable[str] | None = None) -> str:
    """Redact sensitive keys and tokens from a text string.
    
    Replaces matched tokens with '[REDACTED_SECRET]'.
    """
    if not text:
        return text

    sanitized = text
    # Redact explicit custom secrets
    if custom_secrets:
        for secret in custom_secrets:
            if secret and len(secret) >= 6:
                sanitized = sanitized.replace(secret, "[REDACTED_SECRET]")

    # Redact known regex patterns
    for pattern in _KEY_PATTERNS:
        sanitized = pattern.sub("[REDACTED_SECRET]", sanitized)

    return sanitized
