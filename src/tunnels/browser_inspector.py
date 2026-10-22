from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import sys
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
import aiohttp
from bs4 import BeautifulSoup
import structlog

from src.memory.content_store import get_content_store
from src.models.raven import EvidenceItem, FreshnessState, VerificationState
from src.utils.ssl_context import create_aiohttp_connector

logger = structlog.get_logger(__name__)

ALLOWED_SCHEMES = {"http", "https"}
BLOCKED_HOSTS = {
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.aws.internal",
    "localhost",
    "127.0.0.1",
}


def safe_validate_url(url: str) -> str:
    """SSRF guard: validates scheme and verifies host does not resolve to private IPs."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise ValueError(f"Blocked invalid URL scheme: {parsed.scheme}")

    host = (parsed.hostname or "").lower()
    if not host or host in BLOCKED_HOSTS:
        raise ValueError(f"Blocked restricted host: {host}")

    try:
        addr_infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError(f"Host resolution failed for: {host}") from exc

    for info in addr_infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
            ):
                raise ValueError(f"Target resolves to restricted IP: {ip} for host: {host}")
        except ValueError:
            continue

    return url


class InspectedResource:
    """Structured result of inspecting an external web resource."""

    def __init__(
        self,
        url: str,
        canonical_url: str = "",
        title: str = "",
        main_text: str = "",
        headings: Optional[List[str]] = None,
        date_metadata: Optional[str] = None,
        relevant_links: Optional[List[str]] = None,
        extraction_method: str = "http",
        status: str = "success",  # success, failed, blocked
        error: Optional[str] = None,
    ) -> None:
        self.url = url
        self.canonical_url = canonical_url or url
        self.title = title
        self.main_text = main_text
        self.headings = headings or []
        self.date_metadata = date_metadata
        self.relevant_links = relevant_links or []
        self.extraction_method = extraction_method
        self.status = status
        self.error = error
        self.retrieved_at = datetime.now(timezone.utc).isoformat()


class BrowserInspector:
    """Hybrid HTTP and Playwright browser extractor with SSRF protections and resilient fallbacks."""

    def __init__(self, max_body_bytes: int = 5 * 1024 * 1024, timeout_sec: int = 10) -> None:
        self.max_body_bytes = max_body_bytes
        self.timeout_sec = timeout_sec
        self.content_store = get_content_store()
        self._browser_available: Optional[bool] = None
        self._browser_disabled_reason: Optional[str] = None

    def is_browser_available(self) -> bool:
        """Determines if browser inspection via Playwright can run in the current environment."""
        if self._browser_available is not None:
            return self._browser_available

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if sys.platform == "win32" and loop is not None:
            # SelectorEventLoop on Windows raises NotImplementedError on asyncio.create_subprocess_exec
            selector_types = (asyncio.SelectorEventLoop,)
            win_selector = getattr(asyncio, "_WindowsSelectorEventLoop", None)
            if win_selector is not None:
                selector_types = selector_types + (win_selector,)
            if isinstance(loop, selector_types):
                self._browser_available = False
                self._browser_disabled_reason = (
                    "Windows SelectorEventLoop does not support asyncio subprocesses. "
                    "Browser inspection disabled; using safe HTTP extraction fallback."
                )
                logger.info(
                    "inspector.browser_disabled",
                    reason=self._browser_disabled_reason,
                    platform=sys.platform,
                    loop=type(loop).__name__,
                )
                return False

        try:
            import playwright.async_api  # noqa: F401
            self._browser_available = True
            self._browser_disabled_reason = None
            return True
        except ImportError as imp_err:
            self._browser_available = False
            self._browser_disabled_reason = f"Playwright library not installed: {imp_err}"
            logger.info("inspector.browser_disabled", reason=self._browser_disabled_reason)
            return False

    async def inspect_resource(self, url: str, mode: str = "auto") -> InspectedResource:
        """Inspects web page content via safe HTTP or Playwright headless browser."""
        try:
            validated_url = safe_validate_url(url)
        except ValueError as ssrf_err:
            logger.warning("inspector.ssrf_blocked", url=url, error=str(ssrf_err))
            return InspectedResource(
                url=url,
                status="blocked",
                error=f"SSRF safety check failed: {ssrf_err}",
                extraction_method="none",
            )

        http_result: Optional[InspectedResource] = None

        # 1. Prefer HTTP extraction
        if mode in ("auto", "http"):
            http_result = await self._fetch_via_http(validated_url)
            # If static extraction succeeds and has substantive content, return it directly
            if (
                http_result.status == "success"
                and len(http_result.main_text.strip()) > 300
                and "enable javascript" not in http_result.main_text.lower()
            ):
                return http_result
            if mode == "http":
                return http_result

        # 2. Check browser availability before attempting Playwright
        if not self.is_browser_available():
            if http_result and http_result.status == "success" and http_result.main_text.strip():
                # HTTP extraction succeeded with partial/shorter content; preserve it
                return http_result
            return http_result or InspectedResource(
                url=validated_url,
                status="failed",
                error=f"Browser extraction unavailable: {self._browser_disabled_reason}",
                extraction_method="none",
            )

        # 3. Fall back to Playwright if needed and available
        pw_result = await self._fetch_via_playwright(validated_url)
        if pw_result.status == "success" and pw_result.main_text.strip():
            return pw_result

        # If browser extraction failed but HTTP extraction succeeded with partial content, preserve HTTP!
        if http_result and http_result.status == "success" and http_result.main_text.strip():
            logger.info(
                "inspector.preserved_http_fallback",
                url=validated_url,
                http_chars=len(http_result.main_text),
                browser_error=pw_result.error,
            )
            return http_result

        return pw_result

    async def _fetch_via_http(self, url: str) -> InspectedResource:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RAVEN-Intelligence/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        try:
            connector = create_aiohttp_connector()
            timeout = aiohttp.ClientTimeout(total=self.timeout_sec)
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                async with session.get(url, headers=headers, allow_redirects=True) as resp:
                    final_url = str(resp.url)
                    safe_validate_url(final_url)

                    if resp.status >= 400:
                        return InspectedResource(
                            url=url,
                            status="failed",
                            error=f"HTTP {resp.status} status error",
                            extraction_method="http",
                        )

                    body = await resp.read()
                    if len(body) > self.max_body_bytes:
                        body = body[: self.max_body_bytes]

                    html = body.decode("utf-8", errors="replace")
                    return self._parse_html(html, final_url, "http")

        except aiohttp.ClientConnectorCertificateError as ssl_err:
            logger.warning("inspector.http_ssl_error", url=url, error=str(ssl_err))
            return InspectedResource(
                url=url,
                status="failed",
                error=f"TLS certificate verification failed: {ssl_err}",
                extraction_method="http",
            )
        except Exception as e:
            logger.warning("inspector.http_failed", url=url, error=str(e))
            return InspectedResource(
                url=url,
                status="failed",
                error=str(e),
                extraction_method="http",
            )

    async def _fetch_via_playwright(self, url: str) -> InspectedResource:
        """Renders JS-heavy pages using Playwright with lifecycle-safe cleanup."""
        browser = None
        playwright_cm = None
        try:
            from playwright.async_api import async_playwright

            playwright_cm = async_playwright()
            p = await playwright_cm.start()
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(url, timeout=self.timeout_sec * 1000, wait_until="domcontentloaded")
                html = await page.content()
                final_url = page.url
                safe_validate_url(final_url)
                return self._parse_html(html, final_url, "playwright")
            except Exception as page_err:
                raise page_err
        except NotImplementedError as nie:
            self._browser_available = False
            self._browser_disabled_reason = f"Event loop does not support subprocess: {nie}"
            logger.warning(
                "inspector.playwright_not_implemented",
                url=url,
                error=str(nie),
                hint="Use ProactorEventLoop on Windows or fallback to safe HTTP extraction",
            )
            return InspectedResource(
                url=url,
                status="failed",
                error=f"Browser extraction not supported on current runtime: {nie}",
                extraction_method="playwright",
            )
        except Exception as pw_err:
            logger.warning("inspector.playwright_failed", url=url, error=str(pw_err))
            return InspectedResource(
                url=url,
                status="failed",
                error=f"Browser extraction failed: {pw_err}",
                extraction_method="playwright",
            )
        finally:
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass
            if playwright_cm is not None:
                try:
                    await playwright_cm.__aexit__(None, None, None)
                except Exception:
                    pass

    def _parse_html(self, html: str, final_url: str, method: str) -> InspectedResource:
        soup = BeautifulSoup(html, "html.parser")

        # Extract title
        title_tag = soup.find("title")
        title = title_tag.get_text().strip() if title_tag else ""

        # Remove scripts, styles, forms, navigation
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "svg"]):
            tag.decompose()

        # Extract headings
        headings = [h.get_text().strip() for h in soup.find_all(["h1", "h2", "h3"]) if h.get_text().strip()]

        # Extract main text
        main_text = re.sub(r"\s+", " ", soup.get_text()).strip()

        # Extract publish date from meta tags
        date_meta = None
        for meta in soup.find_all("meta"):
            prop = meta.get("property") or meta.get("name") or ""
            if any(k in prop.lower() for k in ("published_time", "release_date", "date", "updated_time")):
                val = meta.get("content")
                if val:
                    date_meta = val
                    break

        # Extract relevant primary links
        links: Set[str] = set()
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith("http") and not any(ign in href for ign in ("twitter.com", "facebook.com", "linkedin.com")):
                links.add(href)

        # Store full raw text in BoundedContentStore
        content_ref = self.content_store.put(final_url, main_text)

        res = InspectedResource(
            url=final_url,
            canonical_url=final_url,
            title=title,
            main_text=main_text,
            headings=headings[:10],
            date_metadata=date_meta,
            relevant_links=list(links)[:10],
            extraction_method=method,
            status="success",
        )
        return res

    def extract_evidence(self, resource: InspectedResource, objective_id: str) -> List[EvidenceItem]:
        """Extracts structured EvidenceItems from an InspectedResource."""
        if resource.status != "success" or not resource.main_text:
            return []

        # Find substantive paragraphs
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\.\s{2,}", resource.main_text) if len(p.strip()) > 80]
        selected_text = " ".join(paragraphs[:3])[:1500] if paragraphs else resource.main_text[:1500]

        content_ref = self.content_store.put(resource.canonical_url, resource.main_text)

        item = EvidenceItem(
            evidence_id="",
            source_type="web",
            canonical_url=resource.canonical_url,
            title=resource.title or resource.canonical_url,
            publisher_or_repo=urllib.parse.urlparse(resource.canonical_url).netloc,
            published_date=resource.date_metadata,
            updated_date=resource.date_metadata,
            excerpt=selected_text,
            objective_id=objective_id,
            objective_ids=[objective_id],
            extraction_method=resource.extraction_method,
            extracted_text_ref=content_ref,
            resource_explanation={
                "what_it_is": f"Inspected web resource from {urllib.parse.urlparse(resource.canonical_url).netloc}",
                "what_it_provides": f"Headings: {'; '.join(resource.headings[:3]) if resource.headings else 'General content'}",
                "technical_details": f"Extracted via {resource.extraction_method.upper()}; {len(resource.main_text)} characters analyzed",
                "recommendation": "Use for primary architectural and domain evidence",
                "limitations": "Third-party content; subject to external reachability and updates",
            },
        )
        return [item]

    async def expand_relevant_links(self, resource: InspectedResource, max_links: int = 2) -> List[InspectedResource]:
        """Optionally inspects linked primary sources within configured limits."""
        sub_results: List[InspectedResource] = []
        for link in resource.relevant_links[:max_links]:
            try:
                sub = await self.inspect_resource(link, mode="http")
                if sub.status == "success":
                    sub_results.append(sub)
            except Exception:
                continue
        return sub_results
