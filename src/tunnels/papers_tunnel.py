from __future__ import annotations

import urllib.parse
from typing import List, Tuple
import aiohttp
import defusedxml.ElementTree as ET
import structlog

from src.models.raven import EvidenceItem, SourceTunnelResult
from src.tunnels.base import BaseTunnel
from src.utils.ssl_context import create_aiohttp_connector

logger = structlog.get_logger(__name__)


class PapersTunnel(BaseTunnel):
    """Scholarly research papers tunnel querying arXiv API."""

    def __init__(self) -> None:
        super().__init__(name="papers")

    def is_enabled(self) -> Tuple[bool, str]:
        return True, "Available (arXiv Public API)"

    async def fetch(self, query: str, objective_id: str, limit: int = 5) -> SourceTunnelResult:
        clean_q = query.replace('"', '').replace("'", "")
        encoded_query = urllib.parse.quote_plus(f"all:{clean_q}")
        url = (
            f"https://export.arxiv.org/api/query?"
            f"search_query={encoded_query}&max_results={limit}&sortBy=relevance"
        )

        items: List[EvidenceItem] = []
        namespaces = {
            "atom": "http://www.w3.org/2005/Atom",
            "arxiv": "http://arxiv.org/schemas/atom",
        }

        try:
            connector = create_aiohttp_connector()
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return SourceTunnelResult(
                            tunnel_name="papers",
                            status="failed",
                            reason=f"arXiv API returned HTTP {resp.status}",
                        )
                    xml_data = await resp.text()

            root = ET.fromstring(xml_data)
            for entry in root.findall("atom:entry", namespaces):
                title_elem = entry.find("atom:title", namespaces)
                summary_elem = entry.find("atom:summary", namespaces)
                id_elem = entry.find("atom:id", namespaces)
                published_elem = entry.find("atom:published", namespaces)
                updated_elem = entry.find("atom:updated", namespaces)

                title = title_elem.text.strip().replace("\n", " ") if title_elem is not None and title_elem.text else ""
                summary = summary_elem.text.strip().replace("\n", " ") if summary_elem is not None and summary_elem.text else ""
                paper_url = id_elem.text.strip() if id_elem is not None and id_elem.text else ""
                published = published_elem.text.strip() if published_elem is not None and published_elem.text else None
                updated = updated_elem.text.strip() if updated_elem is not None and updated_elem.text else None

                if paper_url and (summary or title):
                    items.append(
                        EvidenceItem(
                            evidence_id="",
                            source_type="papers",
                            canonical_url=paper_url,
                            title=title,
                            publisher_or_repo="arXiv",
                            published_date=published,
                            updated_date=updated,
                            excerpt=summary[:2000],
                            objective_id=objective_id,
                        )
                    )

            return SourceTunnelResult(
                tunnel_name="papers",
                status="completed" if items else "completed_empty",
                items=items[:limit],
            )

        except aiohttp.ClientConnectorCertificateError as ssl_exc:
            logger.error(
                "tunnel.papers.ssl_verification_failed",
                url=url,
                error=str(ssl_exc),
                hint="Verify CA certificates in environment (e.g. SSL_CERT_FILE / certifi)",
            )
            return SourceTunnelResult(
                tunnel_name="papers",
                status="failed",
                error=f"TLS certificate verification failed: {ssl_exc}",
                reason="arXiv TLS certificate verification failed",
            )
        except Exception as exc:
            logger.warning("tunnel.papers.error", error=str(exc))
            return SourceTunnelResult(
                tunnel_name="papers",
                status="failed",
                error=str(exc),
                reason="arXiv API search exception",
            )
