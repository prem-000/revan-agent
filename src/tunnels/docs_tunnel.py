from __future__ import annotations

import asyncio
from typing import List, Tuple
import structlog
from ddgs import DDGS

from src.models.raven import EvidenceItem, SourceTunnelResult
from src.tunnels.base import BaseTunnel

logger = structlog.get_logger(__name__)


class DocsTunnel(BaseTunnel):
    """Official documentation tunnel scoping queries to documentation portals."""

    def __init__(self) -> None:
        super().__init__(name="documentation")

    def is_enabled(self) -> Tuple[bool, str]:
        return True, "Available (Official Docs Search)"

    async def fetch(self, query: str, objective_id: str, limit: int = 5) -> SourceTunnelResult:
        scoped_query = f"{query} (site:docs.* OR site:*.readthedocs.io OR site:developer.mozilla.org OR site:pypi.org OR site:npmjs.com) documentation reference"
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
                            source_type="documentation",
                            canonical_url=url,
                            title=title,
                            publisher_or_repo="Official Docs",
                            excerpt=body[:1500],
                            objective_id=objective_id,
                        )
                    )

            return SourceTunnelResult(
                tunnel_name="documentation",
                status="completed" if items else "completed_empty",
                items=items,
            )
        except Exception as exc:
            logger.warning("tunnel.docs.error", error=str(exc))
            return SourceTunnelResult(
                tunnel_name="documentation",
                status="failed",
                error=str(exc),
                reason="Documentation search failed",
            )
