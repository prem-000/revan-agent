from __future__ import annotations

import asyncio
from typing import List, Tuple
import structlog
from ddgs import DDGS

from src.models.raven import EvidenceItem, SourceTunnelResult
from src.tunnels.base import BaseTunnel

logger = structlog.get_logger(__name__)


class CommunityTunnel(BaseTunnel):
    """Developer communities tunnel (Stack Overflow, GitHub Discussions, Reddit)."""

    def __init__(self) -> None:
        super().__init__(name="community")

    def is_enabled(self) -> Tuple[bool, str]:
        return True, "Available (Community Search via DDGS)"

    async def fetch(self, query: str, objective_id: str, limit: int = 5) -> SourceTunnelResult:
        scoped_query = f"{query} (site:stackoverflow.com OR site:github.com/*/discussions OR site:reddit.com/r/programming OR site:news.ycombinator.com)"
        items: List[EvidenceItem] = []

        try:
            def _search():
                results = []
                with DDGS() as ddgs:
                    for r in ddgs.text(scoped_query, max_results=limit):
                        results.append(r)
                return results

            hits = await asyncio.to_thread(_search)
            for h in hits:
                url = h.get("href") or h.get("url") or ""
                body = h.get("body") or h.get("content") or ""
                title = h.get("title") or ""
                if url and body:
                    items.append(
                        EvidenceItem(
                            evidence_id="",
                            source_type="community",
                            canonical_url=url,
                            title=title,
                            publisher_or_repo="Developer Community",
                            excerpt=body[:1500],
                            objective_id=objective_id,
                        )
                    )

            return SourceTunnelResult(
                tunnel_name="community",
                status="completed" if items else "completed_empty",
                items=items,
            )
        except Exception as exc:
            logger.warning("tunnel.community.error", error=str(exc))
            return SourceTunnelResult(
                tunnel_name="community",
                status="failed",
                error=str(exc),
                reason="Community query failed",
            )
