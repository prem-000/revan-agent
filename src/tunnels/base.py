from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Tuple
from src.models.raven import SourceTunnelResult


class BaseTunnel(ABC):
    """Abstract base class for all parallel source tunnels."""

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    def is_enabled(self) -> Tuple[bool, str]:
        """Check if connector prerequisites (API keys, network, tools) are available."""
        pass

    @abstractmethod
    async def fetch(self, query: str, objective_id: str, limit: int = 5) -> SourceTunnelResult:
        """Execute search in this tunnel and return structured items."""
        pass
