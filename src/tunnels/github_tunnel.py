from __future__ import annotations

import base64
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import aiohttp
import structlog

from src.config import get_settings
from src.memory.content_store import get_content_store
from src.models.raven import EvidenceItem, SourceTunnelResult
from src.tunnels.base import BaseTunnel

logger = structlog.get_logger(__name__)


class GitHubTunnel(BaseTunnel):
    """Deep GitHub repository intelligence tunnel with README and release extraction."""

    def __init__(self) -> None:
        super().__init__(name="github")
        self.content_store = get_content_store()

    def is_enabled(self) -> Tuple[bool, str]:
        settings = get_settings()
        if settings.github_token:
            return True, "Available (Authenticated GitHub Token)"
        return True, "Available (Public GitHub API, rate-limited)"

    async def fetch(self, query: str, objective_id: str, limit: int = 5) -> SourceTunnelResult:
        settings = get_settings()
        clean_q = query.replace('"', '').replace("'", "")
        encoded_query = urllib.parse.quote_plus(f"{clean_q} in:name,description,readme")
        search_url = f"https://api.github.com/search/repositories?q={encoded_query}&sort=stars&order=desc&per_page={limit}"

        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "RAVEN-Research-Agent/1.0",
        }
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"

        items: List[EvidenceItem] = []

        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # 1. Query repository search
                async with session.get(search_url, headers=headers) as resp:
                    if resp.status == 403:
                        return SourceTunnelResult(
                            tunnel_name="github",
                            status="disabled",
                            reason="GitHub API rate limit exceeded. Set GITHUB_TOKEN in .env for higher quota.",
                        )
                    if resp.status != 200:
                        return SourceTunnelResult(
                            tunnel_name="github",
                            status="failed",
                            reason=f"GitHub API error HTTP {resp.status}",
                        )
                    data = await resp.json()

                repositories = data.get("items", [])

                # 2. Inspect each repository deeply (README + Releases)
                for repo in repositories[:limit]:
                    repo_url = repo.get("html_url", "")
                    name = repo.get("full_name", "")
                    desc = repo.get("description") or "No description provided."
                    pushed_at = repo.get("pushed_at")
                    created_at = repo.get("created_at")
                    updated_at = repo.get("updated_at")
                    stars = repo.get("stargazers_count", 0)
                    forks = repo.get("forks_count", 0)
                    open_issues = repo.get("open_issues_count", 0)
                    language = repo.get("language") or "Unknown"
                    is_archived = repo.get("archived", False)
                    license_info = repo.get("license") or {}
                    license_name = license_info.get("spdx_id") or license_info.get("name") or "Unknown License"
                    default_branch = repo.get("default_branch") or "main"

                    # 3. Fetch README and Latest Release
                    readme_text = await self._fetch_readme(session, headers, name, default_branch)
                    latest_release = await self._fetch_latest_release(session, headers, name)

                    # Store full README in BoundedContentStore
                    readme_ref = self.content_store.put(f"{repo_url}#readme", readme_text) if readme_text else None

                    # Extract architecture and capabilities from README
                    features_summary = self._extract_readme_sections(readme_text)

                    # Build detailed excerpt
                    release_label = (
                        f"{latest_release.get('tag')} (published {latest_release.get('date')})"
                        if latest_release
                        else "No official releases tagged"
                    )
                    archived_label = " [ARCHIVED]" if is_archived else ""

                    excerpt_parts = [
                        f"Repository: {name}{archived_label}",
                        f"Primary Language: {language} | Stars: {stars} | Forks: {forks} | Open Issues: {open_issues}",
                        f"License: {license_name}",
                        f"Created: {created_at or 'Unknown'} | Latest Push: {pushed_at or 'Unknown'}",
                        f"Latest Release: {release_label}",
                        f"Description: {desc}",
                    ]
                    if features_summary:
                        excerpt_parts.append(f"\nExtracted Documentation Insights:\n{features_summary}")

                    full_excerpt = "\n".join(excerpt_parts)[:2500]

                    items.append(
                        EvidenceItem(
                            evidence_id="",
                            source_type="github",
                            canonical_url=repo_url,
                            title=name,
                            publisher_or_repo=f"GitHub ({name.split('/')[0]})",
                            published_date=created_at,
                            updated_date=pushed_at,
                            excerpt=full_excerpt,
                            objective_id=objective_id,
                            objective_ids=[objective_id],
                            extraction_method="github_api",
                            extracted_text_ref=readme_ref,
                            repository_meta={
                                "full_name": name,
                                "stars": stars,
                                "forks": forks,
                                "open_issues": open_issues,
                                "language": language,
                                "license": license_name,
                                "is_archived": is_archived,
                                "created_at": created_at,
                                "updated_at": updated_at,
                                "pushed_at": pushed_at,
                                "latest_release": latest_release,
                            },
                            resource_explanation={
                                "what_it_is": f"Open source repository {name} in {language}: {desc}",
                                "what_it_provides": f"Capabilities extracted from documentation: {features_summary[:300] if features_summary else desc}",
                                "technical_details": f"Stack: {language}, License: {license_name}, Archived: {is_archived}, Latest activity: {pushed_at or 'Unknown'}",
                                "recommendation": "Evaluate for component reuse or architectural pattern comparison" if not is_archived else "Do not adopt for production: repository is archived",
                                "limitations": f"Open issues: {open_issues}; verify compatibility with target runtime",
                            },
                        )
                    )

            return SourceTunnelResult(
                tunnel_name="github",
                status="completed" if items else "completed_empty",
                items=items,
            )

        except Exception as exc:
            logger.warning("tunnel.github.error", error=str(exc))
            return SourceTunnelResult(
                tunnel_name="github",
                status="failed",
                error=str(exc),
                reason="GitHub API network or query exception",
            )

    async def _fetch_readme(
        self, session: aiohttp.ClientSession, headers: dict, full_name: str, default_branch: str
    ) -> str:
        """Fetches and decodes the repository README file."""
        # Try GitHub API readme endpoint first
        api_url = f"https://api.github.com/repos/{full_name}/readme"
        try:
            async with session.get(api_url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    content_b64 = data.get("content", "")
                    if content_b64:
                        return base64.b64decode(content_b64).decode("utf-8", errors="replace")
        except Exception:
            pass

        # Fallback to raw githubusercontent
        for branch in (default_branch, "main", "master"):
            for fname in ("README.md", "readme.md", "README.rst"):
                raw_url = f"https://raw.githubusercontent.com/{full_name}/{branch}/{fname}"
                try:
                    async with session.get(raw_url, headers=headers) as resp:
                        if resp.status == 200:
                            return await resp.text(errors="replace")
                except Exception:
                    continue
        return ""

    async def _fetch_latest_release(
        self, session: aiohttp.ClientSession, headers: dict, full_name: str
    ) -> Optional[Dict[str, str]]:
        """Fetches the latest official release tag and date."""
        rel_url = f"https://api.github.com/repos/{full_name}/releases/latest"
        try:
            async with session.get(rel_url, headers=headers) as resp:
                if resp.status == 200:
                    rel_data = await resp.json()
                    return {
                        "tag": rel_data.get("tag_name") or rel_data.get("name") or "Unknown",
                        "date": rel_data.get("published_at") or "Unknown",
                    }
        except Exception:
            pass
        return None

    def _extract_readme_sections(self, readme: str) -> str:
        """Extracts substantive architectural and feature sections from a README."""
        if not readme:
            return ""

        lines = readme.splitlines()
        extracted: List[str] = []
        capture = False
        captured_count = 0

        keywords = ("architecture", "feature", "overview", "quickstart", "installation", "design")

        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("#", "##", "###")):
                header_text = stripped.lstrip("#").strip().lower()
                if any(kw in header_text for kw in keywords):
                    capture = True
                    extracted.append(f"• {stripped.lstrip('#').strip()}:")
                    continue
                else:
                    capture = False

            if capture and stripped:
                extracted.append(f"  {stripped}")
                captured_count += 1
                if captured_count >= 15:
                    break

        if not extracted:
            # Fallback to the first 10 substantive non-badge lines of the README
            clean_lines = [
                l.strip()
                for l in lines
                if l.strip() and not l.strip().startswith("[!") and not l.strip().startswith("<img")
            ]
            return "\n".join(clean_lines[:8])

        return "\n".join(extracted[:15])
