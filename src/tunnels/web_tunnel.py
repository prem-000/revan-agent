from __future__ import annotations

import asyncio
from typing import List, Tuple
import structlog
from ddgs import DDGS
from tavily import TavilyClient

from src.config import get_settings
from src.models.raven import EvidenceItem, SourceTunnelResult
from src.tunnels.base import BaseTunnel
from src.tunnels.browser_inspector import BrowserInspector

logger = structlog.get_logger(__name__)


class WebTunnel(BaseTunnel):
    """General web tunnel with search discovery and deep browser/HTTP page extraction."""

    def __init__(self) -> None:
        super().__init__(name="web")
        self.inspector = BrowserInspector()

    def is_enabled(self) -> Tuple[bool, str]:
        return True, "Available (Tavily or DuckDuckGo fallback + Browser Inspection)"

    async def fetch(self, query: str, objective_id: str, limit: int = 5) -> SourceTunnelResult:
        settings = get_settings()
        items: List[EvidenceItem] = []
        candidate_urls: List[dict] = []

        try:
            # 1. Discover candidates via Tavily
            if settings.tavily_api_key and settings.tavily_api_key.startswith("tvly-"):
                try:
                    client = TavilyClient(api_key=settings.tavily_api_key)
                    res = await asyncio.to_thread(
                        client.search, query=query, max_results=limit, search_depth="basic"
                    )
                    for r in res.get("results", []):
                        url = r.get("url", "")
                        if url:
                            candidate_urls.append({
                                "url": url,
                                "title": r.get("title", ""),
                                "snippet": r.get("content", ""),
                                "source": r.get("source", "web"),
                                "published_date": r.get("published_date"),
                            })
                except Exception as e:
                    logger.warning("tunnel.web.tavily_failed", error=str(e))

            # 2. Discover candidates via DuckDuckGo fallback / supplement
            if len(candidate_urls) < limit:
                remaining = limit - len(candidate_urls)
                try:
                    def _ddg_sync():
                        results = []
                        with DDGS() as ddgs:
                            for hit in ddgs.text(query, max_results=remaining):
                                results.append(hit)
                        return results

                    ddg_hits = await asyncio.to_thread(_ddg_sync)
                    for hit in ddg_hits:
                        url = hit.get("href") or hit.get("url") or ""
                        if url:
                            candidate_urls.append({
                                "url": url,
                                "title": hit.get("title") or "",
                                "snippet": hit.get("body") or hit.get("content") or "",
                                "source": "DuckDuckGo Search",
                                "published_date": None,
                            })
                except Exception as exc:
                    logger.warning("tunnel.web.ddg_failed", error=str(exc))

            # 3. Inspect candidates deeply using BrowserInspector (HTTP with Playwright fallback)
            for cand in candidate_urls[:limit]:
                url = cand["url"]
                try:
                    inspected = await self.inspector.inspect_resource(url, mode="auto")
                    if inspected.status == "success" and inspected.main_text:
                        extracted = self.inspector.extract_evidence(inspected, objective_id)
                        items.extend(extracted)
                    else:
                        # Fallback to search snippet if page blocked or unavailable, noting extraction failure
                        snippet = cand.get("snippet", "")
                        if snippet:
                            items.append(
                                EvidenceItem(
                                    evidence_id="",
                                    source_type="web",
                                    canonical_url=url,
                                    title=cand.get("title", url),
                                    publisher_or_repo=cand.get("source", "web"),
                                    excerpt=snippet[:1500],
                                    published_date=cand.get("published_date"),
                                    objective_id=objective_id,
                                    extraction_method="search_snippet",
                                    verification_notes=f"Full extraction fallback: {inspected.error or 'inspected_empty'}",
                                )
                            )
                except Exception as ex_inspect:
                    logger.warning("tunnel.web.inspect_error", url=url, error=str(ex_inspect))

            return SourceTunnelResult(
                tunnel_name="web",
                status="completed" if items else "completed_empty",
                items=items[:limit],
            )

        except Exception as ex:
            logger.error("tunnel.web.error", error=str(ex))
            return SourceTunnelResult(
                tunnel_name="web",
                status="failed",
                error=str(ex),
                reason="Web search or browser extraction failure",
            )
