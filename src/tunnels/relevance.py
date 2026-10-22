from __future__ import annotations

import re
import string
from typing import List, Optional, Set, Tuple
import structlog
from pydantic import BaseModel

from src.models.raven import AnalysisResult, EvidenceItem, ResearchPlan, VerificationState

logger = structlog.get_logger(__name__)

# Common web/video platform boilerplate headers/footers
_BOILERPLATE_PATTERNS = [
    re.compile(r"about press copyright contact us creators advertise developers terms privacy", re.IGNORECASE),
    re.compile(r"enable javascript to run this app", re.IGNORECASE),
    re.compile(r"you need to enable javascript to run this app", re.IGNORECASE),
    re.compile(r"access denied|403 forbidden|cloudflare ray id", re.IGNORECASE),
    re.compile(r"please turn javascript on and reload the page", re.IGNORECASE),
]

# Irrelevant domain keywords when CS / Data Structures domain anchor is active
_CS_DOMAIN_DISALLOWED_PATTERNS = [
    re.compile(r"\bdigital signature algorithm\b", re.IGNORECASE),
    re.compile(r"\bvowel harmony\b", re.IGNORECASE),
    re.compile(r"\bphonology\b", re.IGNORECASE),
    re.compile(r"\bcoinmarketcap\b", re.IGNORECASE),
    re.compile(r"\bcryptocurrency price\b", re.IGNORECASE),
]


class RelevanceEvaluation(BaseModel):
    is_relevant: bool
    score: float
    reason: str


class RelevanceFilter:
    """Evaluates raw evidence items for domain relevance, content sanity, and topic alignment."""

    def __init__(self, threshold: float = 0.4) -> None:
        self.threshold = threshold

    def _check_content_sanity(self, text: str) -> Tuple[bool, str]:
        """Check if content is binary garbage, raw PDF headers, or platform footer boilerplate."""
        if not text or not text.strip():
            return False, "Empty or whitespace-only content"

        clean = text.strip()
        # 1. Raw PDF binary stream / header
        if clean.startswith("%PDF-") or "\x00" in clean[:500]:
            return False, "Raw binary PDF header/stream detected"

        # 2. Ratio of non-printable / control characters
        printable_set = set(string.printable)
        non_printable = sum(1 for c in clean if c not in printable_set)
        if len(clean) > 0 and (non_printable / len(clean)) > 0.08:
            return False, f"High non-printable character ratio ({non_printable / len(clean):.1%})"

        # 3. Boilerplate navigation / footer text (e.g. YouTube footer)
        for pattern in _BOILERPLATE_PATTERNS:
            if pattern.search(clean):
                return False, "Page consists only of site navigation/boilerplate footer"

        return True, "Sanity check passed"

    def _check_domain_mismatch(self, item: EvidenceItem, analysis: Optional[AnalysisResult]) -> Tuple[bool, str]:
        """Verify candidate does not suffer from acronym domain collisions (e.g. DSA signature vs data structure)."""
        if not analysis:
            return True, "No analysis anchor provided"

        anchor = (analysis.domain_anchor or "").lower()
        full_text = f"{item.title} {item.canonical_url} {item.excerpt}".lower()

        # If data structures & algorithms anchor is active
        if "data structure" in anchor or "algorithm" in anchor:
            # DSA mismatch check
            terms_lower = {k.lower(): v.lower() for k, v in analysis.disambiguated_terms.items()}
            if "dsa" in terms_lower or "linked" in analysis.goal.lower():
                for pat in _CS_DOMAIN_DISALLOWED_PATTERNS:
                    if pat.search(full_text):
                        return False, f"Domain mismatch: matched irrelevant domain pattern '{pat.pattern}'"

            # Pure concept query check: reject unrelated utility repos (e.g. naming dictionaries)
            if analysis.is_concept_query and item.source_type == "github":
                repo_id = item.publisher_or_repo.lower() or item.canonical_url.lower()
                if "naming" in repo_id or "awesome-naming" in repo_id:
                    return False, "Domain mismatch: naming dictionary repository is irrelevant for core concept query"

        return True, "Domain alignment verified"

    def _calculate_keyword_overlap(self, item: EvidenceItem, query_keywords: Set[str]) -> float:
        """Calculate normalized keyword overlap ratio between query/objectives and evidence."""
        if not query_keywords:
            return 0.5

        content_words = set(re.findall(r"\b[A-Za-z0-9_-]{3,}\b", f"{item.title} {item.excerpt}".lower()))
        if not content_words:
            return 0.0

        matches = query_keywords.intersection(content_words)
        overlap_score = len(matches) / min(len(query_keywords), 8)
        return min(overlap_score, 1.0)

    def evaluate_item(
        self,
        item: EvidenceItem,
        analysis: Optional[AnalysisResult] = None,
        plan: Optional[ResearchPlan] = None,
        query: str = "",
    ) -> RelevanceEvaluation:
        """Evaluate a single evidence item deterministically."""
        # 1. Content sanity check
        sane, sanity_reason = self._check_content_sanity(item.excerpt)
        if not sane:
            return RelevanceEvaluation(is_relevant=False, score=0.0, reason=sanity_reason)

        # 2. Domain mismatch check
        matched, domain_reason = self._check_domain_mismatch(item, analysis)
        if not matched:
            return RelevanceEvaluation(is_relevant=False, score=0.0, reason=domain_reason)

        # 3. Keyword / Concept Overlap
        query_text = f"{query} "
        if plan:
            for obj in plan.objectives:
                query_text += f" {obj.description} {' '.join(obj.questions)}"
        query_words = set(re.findall(r"\b[A-Za-z0-9_-]{3,}\b", query_text.lower()))

        # Remove generic stop words
        stop_words = {"the", "and", "for", "with", "that", "this", "from", "are", "what", "how", "does"}
        query_words.difference_update(stop_words)

        overlap = self._calculate_keyword_overlap(item, query_words)

        # Boost authoritative official documentation or academic papers
        boost = 0.0
        if item.source_type in ("documentation", "papers"):
            boost += 0.15
        if item.extraction_method in ("http", "playwright", "github_api"):
            boost += 0.1

        final_score = min(max(overlap + boost, 0.0), 1.0)

        if final_score < self.threshold:
            return RelevanceEvaluation(
                is_relevant=False,
                score=final_score,
                reason=f"Insufficient term overlap with research objectives (score: {final_score:.2f} < {self.threshold})",
            )

        return RelevanceEvaluation(
            is_relevant=True,
            score=final_score,
            reason=f"Relevant technical content (score: {final_score:.2f})",
        )

    def filter_evidence(
        self,
        items: List[EvidenceItem],
        analysis: Optional[AnalysisResult] = None,
        plan: Optional[ResearchPlan] = None,
        query: str = "",
    ) -> List[EvidenceItem]:
        """Apply relevance evaluations to all evidence items in place."""
        for item in items:
            # If verifier already marked unreachable, record it
            if item.verification_state == VerificationState.UNREACHABLE:
                item.reachable = False

            eval_res = self.evaluate_item(item, analysis=analysis, plan=plan, query=query)
            item.relevance_score = eval_res.score
            item.relevance_reason = eval_res.reason

            if not eval_res.is_relevant:
                item.verification_state = VerificationState.REJECTED
                logger.info(
                    "relevance.item_rejected",
                    evidence_id=item.evidence_id,
                    url=item.canonical_url,
                    reason=eval_res.reason,
                    score=eval_res.score,
                )

        return items
