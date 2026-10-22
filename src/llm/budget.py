from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


class BudgetExhaustedError(RuntimeError):
    """Raised when the shared model call or time budget for a run is exhausted."""
    pass


@dataclass
class RunBudget:
    """Enforces shared resource limits (LLM calls and wall-clock duration) across all agents."""

    max_calls: int = 30
    max_time_seconds: float = 300.0
    calls_used: int = 0
    tokens_used: int = 0
    start_time: float = field(default_factory=time.time)

    def elapsed_time(self) -> float:
        return time.time() - self.start_time

    def is_time_exhausted(self) -> bool:
        return self.elapsed_time() >= self.max_time_seconds

    def is_calls_exhausted(self) -> bool:
        return self.calls_used >= self.max_calls

    def check_budget(self) -> None:
        """Verify budget constraints; raise BudgetExhaustedError if limits are breached."""
        if self.is_calls_exhausted():
            raise BudgetExhaustedError(
                f"Shared model call budget exhausted ({self.calls_used}/{self.max_calls} calls used)."
            )
        if self.is_time_exhausted():
            raise BudgetExhaustedError(
                f"Shared run time budget exhausted ({self.elapsed_time():.1f}s / {self.max_time_seconds}s limit)."
            )

    def record_call(self, tokens: int = 0) -> None:
        """Record an attempted or completed LLM call."""
        self.calls_used += 1
        if tokens > 0:
            self.tokens_used += tokens

    @property
    def calls_made(self) -> int:
        return self.calls_used

    def to_dict(self) -> dict:
        return {
            "calls_used": self.calls_used,
            "max_calls": self.max_calls,
            "tokens_used": self.tokens_used,
            "elapsed_seconds": round(self.elapsed_time(), 2),
            "max_time_seconds": self.max_time_seconds,
            "is_exhausted": self.is_calls_exhausted() or self.is_time_exhausted(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> RunBudget:
        return cls(
            max_calls=data.get("max_calls", 30),
            max_time_seconds=data.get("max_time_seconds", 300.0),
            calls_used=data.get("calls_used", 0),
            tokens_used=data.get("tokens_used", 0),
        )
