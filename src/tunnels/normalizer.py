from __future__ import annotations

import re
import urllib.parse
from typing import Dict, List, Optional
from src.models.raven import EvidenceItem

_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid"
})

_ARXIV_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:)(\d{4}\.\d{4,5}(?:v\d+)?)", re.IGNORECASE)
_DOI_RE = re.compile(r"(?:doi\.org/|doi:)(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", re.IGNORECASE)


def normalize_url(raw_url: str) -> str:
    """Deterministic URL normalizer removing tracking params, fragments, and normalizing slashes."""
    if not raw_url:
        return ""

    try:
        parsed = urllib.parse.urlparse(raw_url.strip())
        if not parsed.scheme or not parsed.netloc:
            return raw_url.strip()

        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()

        # Filter query params
        query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
        clean_pairs = [(k, v) for k, v in query_pairs if k.lower() not in _TRACKING_PARAMS]
        clean_query = urllib.parse.urlencode(sorted(clean_pairs))

        # Normalise path: strip trailing slash unless root path
        path = parsed.path
        if path.endswith("/") and len(path) > 1:
            path = path[:-1]

        # Strip fragment completely so sections point to same canonical source
        clean_url = urllib.parse.urlunparse((
            scheme,
            netloc,
            path,
            parsed.params,
            clean_query,
            "",  # fragment stripped
        ))
        return clean_url
    except Exception:
        return raw_url.strip()


def extract_paper_id(url: str) -> Optional[str]:
    """Extract canonical paper identifier (arXiv ID or DOI) from URL if present."""
    arxiv_m = _ARXIV_RE.search(url)
    if arxiv_m:
        # Normalize without version suffix to merge v1/v2
        aid = arxiv_m.group(1).split("v")[0]
        return f"arxiv:{aid.lower()}"

    doi_m = _DOI_RE.search(url)
    if doi_m:
        return f"doi:{doi_m.group(1).lower().rstrip('/')}"

    return None


def normalize_evidence_items(items: List[EvidenceItem]) -> List[EvidenceItem]:
    """Deduplicate items on canonical URL first, then DOI/arXiv ID, then content hash.
    
    Merges duplicate entries and accumulates objective associations.
    """
    url_map: Dict[str, EvidenceItem] = {}
    paper_map: Dict[str, EvidenceItem] = {}
    hash_map: Dict[str, EvidenceItem] = {}
    normalized: List[EvidenceItem] = []

    for item in items:
        clean_url = normalize_url(item.canonical_url)
        item.canonical_url = clean_url
        item.content_hash = EvidenceItem.compute_hash(clean_url, item.excerpt)
        paper_id = extract_paper_id(clean_url)

        # Check existing match in priority order:
        # 1. Canonical URL match
        existing: Optional[EvidenceItem] = url_map.get(clean_url)
        # 2. Paper ID match
        if not existing and paper_id and paper_id in paper_map:
            existing = paper_map[paper_id]
        # 3. Content hash match
        if not existing and item.content_hash in hash_map:
            existing = hash_map[item.content_hash]

        if existing:
            # Accumulate objective associations
            for oid in item.objective_ids or ([item.objective_id] if item.objective_id else []):
                if oid and oid not in existing.objective_ids:
                    existing.objective_ids.append(oid)
            if not existing.objective_id and item.objective_id:
                existing.objective_id = item.objective_id

            # Upgrade extraction method and excerpt if incoming item is more complete
            if len(item.excerpt or "") > len(existing.excerpt or ""):
                existing.excerpt = item.excerpt
                existing.content_hash = item.content_hash
            if item.extraction_method in ("http", "playwright", "github_api") and existing.extraction_method == "search_snippet":
                existing.extraction_method = item.extraction_method
            if item.repository_meta and not existing.repository_meta:
                existing.repository_meta = item.repository_meta
            if item.resource_explanation and not existing.resource_explanation:
                existing.resource_explanation = item.resource_explanation
            continue

        # New distinct evidence item
        item.evidence_id = f"ev_{item.source_type[:3]}_{item.content_hash[:8]}"
        normalized.append(item)

        url_map[clean_url] = item
        if paper_id:
            paper_map[paper_id] = item
        hash_map[item.content_hash] = item

    return normalized
