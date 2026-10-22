from __future__ import annotations

import re
from typing import List, Optional
from src.models.raven import (
    AnswerEvaluationMetrics,
    DraftAnswer,
    EvidenceItem,
    SelfCheckVerdict,
    VerificationState,
)


def evaluate_answer_quality(
    answer: Optional[DraftAnswer],
    evidence: List[EvidenceItem],
    verdict: Optional[SelfCheckVerdict] = None,
) -> AnswerEvaluationMetrics:
    """Deterministically evaluate the quality, length, density, and grounding of the direct answer."""
    if not answer or not answer.text.strip():
        return AnswerEvaluationMetrics(
            length_ok=False,
            sentence_count=0,
            citation_density=0.0,
            citation_density_ok=False,
            zero_hallucinated_citations=True,
            self_check_passed=False,
            overall_quality_score=0.0,
        )

    # 1. Sentence count & length check (2-4 sentences)
    raw_sentences = answer.sentences or [s.strip() for s in re.split(r"(?<=[.!?])\s+", answer.text.strip()) if s.strip()]
    sentence_count = len(raw_sentences)
    length_ok = (2 <= sentence_count <= 4)

    # 2. Extract cited IDs from text
    cited_ids = re.findall(r"\[(ev_[a-zA-Z0-9_-]+)\]", answer.text)
    citation_density = len(cited_ids) / max(sentence_count, 1)
    citation_density_ok = (citation_density >= 1.0)

    # 3. Check for hallucinated citations
    valid_evidence_ids = {
        e.evidence_id
        for e in evidence
        if e.verification_state != VerificationState.REJECTED
    }
    zero_hallucinated_citations = all(eid in valid_evidence_ids for eid in cited_ids) if cited_ids else False

    # 4. Self check passed check
    self_check_passed = bool(
        verdict
        and verdict.passed
        and not answer.is_hedged_fallback
    )

    # 5. Composite quality score (0.0 to 1.0)
    score = 0.0
    if length_ok:
        score += 0.25
    if citation_density_ok:
        score += 0.25
    if zero_hallucinated_citations:
        score += 0.30
    if self_check_passed:
        score += 0.20

    return AnswerEvaluationMetrics(
        length_ok=length_ok,
        sentence_count=sentence_count,
        citation_density=round(citation_density, 2),
        citation_density_ok=citation_density_ok,
        zero_hallucinated_citations=zero_hallucinated_citations,
        self_check_passed=self_check_passed,
        overall_quality_score=round(score, 2),
    )
