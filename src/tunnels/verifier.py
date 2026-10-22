from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import List, Optional
from src.models.raven import EvidenceItem, FreshnessState, VerificationState


class EvidenceVerifier:
    """Computes deterministic freshness and validates evidence provenance and excerpt support."""

    @staticmethod
    def compute_freshness(published_date: Optional[str], updated_date: Optional[str]) -> FreshnessState:
        """Deterministic freshness calculation based on dates, not LLM guesses."""
        date_str = updated_date or published_date
        if not date_str:
            return FreshnessState.UNKNOWN

        # 1. Try parsing ISO / standard date formats
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d", "%Y/%m/%d"):
            try:
                # Strip microsecond or offset quirks if necessary
                clean_str = date_str.split(".")[0].rstrip("Z")
                dt = datetime.strptime(clean_str, fmt.split("%z")[0].rstrip("Z")).replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                diff_days = (now - dt).days
                if diff_days <= 365:
                    return FreshnessState.FRESH
                elif diff_days <= 3 * 365:
                    return FreshnessState.AGING
                else:
                    return FreshnessState.STALE
            except Exception:
                continue

        # 2. Fallback to 4-digit year match
        year_match = re.search(r"\b(20\d\d|19\d\d)\b", date_str)
        if not year_match:
            return FreshnessState.UNKNOWN

        try:
            year = int(year_match.group(1))
            current_year = datetime.now(timezone.utc).year
            if year >= current_year - 1:
                return FreshnessState.FRESH
            elif year >= current_year - 3:
                return FreshnessState.AGING
            else:
                return FreshnessState.STALE
        except Exception:
            return FreshnessState.UNKNOWN

    @classmethod
    def verify_item(cls, item: EvidenceItem, objective_text: Optional[str] = None) -> EvidenceItem:
        """Verify an evidence item and update its status, notes, and freshness."""
        notes: List[str] = []

        # 1. Compute deterministic freshness
        item.freshness_state = cls.compute_freshness(item.published_date, item.updated_date)
        if item.freshness_state == FreshnessState.UNKNOWN:
            notes.append("No reliable date metadata found; marked freshness unknown.")

        # 2. Excerpt support and length quality check
        excerpt_clean = item.excerpt.strip()
        if len(excerpt_clean) < 40:
            item.verification_state = VerificationState.UNVERIFIED
            notes.append("Excerpt too brief to serve as standalone evidence.")
        else:
            item.verification_state = VerificationState.VERIFIED
            notes.append("Excerpt meets substance and length criteria.")

        # 3. Snippet vs Inspected Content Check
        if item.extraction_method == "search_snippet":
            notes.append("Extracted from search snippet only; flagged as weak proof for high-impact claims.")
        elif item.extraction_method in ("http", "playwright", "github_api"):
            notes.append(f"Direct resource inspected via {item.extraction_method}.")

        # 4. Source identity and URL validation
        is_local = item.source_type == "local_project"
        if not is_local:
            if not (item.canonical_url.startswith("http://") or item.canonical_url.startswith("https://")):
                item.verification_state = VerificationState.UNVERIFIED
                notes.append("Invalid or untrusted remote URL scheme.")

        # 5. Objective relevance check
        if objective_text:
            obj_words = set(re.findall(r"\w+", objective_text.lower()))
            exc_words = set(re.findall(r"\w+", excerpt_clean.lower()))
            overlap = obj_words.intersection(exc_words)
            if len(overlap) >= 2:
                notes.append(f"Relevance confirmed ({len(overlap)} matching domain terms).")
            else:
                notes.append("Low term overlap with target objective; treat as indirect lead.")

        item.verification_notes = " ".join(notes)
        return item

    @classmethod
    def verify_batch(cls, items: List[EvidenceItem], objective_text: Optional[str] = None) -> List[EvidenceItem]:
        return [cls.verify_item(it, objective_text) for it in items]
