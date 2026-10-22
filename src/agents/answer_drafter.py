from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional
import structlog

from src.llm.budget import RunBudget
from src.llm.provider import LLMProviderAdapter
from src.models.raven import (
    AnalysisResult,
    ClaimItem,
    CritiqueReport,
    DraftAnswer,
    EvidenceItem,
    SelfCheckVerdict,
    VerificationState,
)

logger = structlog.get_logger(__name__)

_ANSWER_DRAFTER_SYSTEM_PROMPT = """You are RAVEN's Direct Answer Engine.
Your task is to draft a SHORT, authoritative, evidence-grounded answer to the user's research query.

CRITICAL RULES:
1. STRICT LENGTH: Exactly 2 to 4 sentences. Never fewer than 2 sentences, never more than 4 sentences.
2. CITATION REQUIREMENT: EVERY sentence must make at least one specific technical claim and MUST cite one or more evidence IDs in square brackets, e.g. [ev_123].
3. STRICT GROUNDING: You may ONLY cite evidence IDs that are explicitly provided below. Inventing evidence IDs is strictly forbidden.
4. NO FILLER OR META-TALK:
   - Do NOT write general introductions like "A linked list is a fundamental computer science concept...".
   - Do NOT write transitions like "In this report..." or "Based on the collected sources...".
   - Jump immediately to the direct technical facts, mechanisms, and trade-offs.
5. CONCISENESS & PRECISION: Include exact algorithmic complexities (e.g. O(1), O(n)), memory trade-offs, language constructs (e.g. collections.deque), or verified empirical details.

OUTPUT FORMAT:
Return ONLY a valid JSON object matching this schema:
{
  "text": "Sentence 1 with [ev_id]. Sentence 2 with [ev_id]. Sentence 3 with [ev_id].",
  "sentences": [
    "Sentence 1 with [ev_id].",
    "Sentence 2 with [ev_id].",
    "Sentence 3 with [ev_id]."
  ],
  "claims": [
    {"claim_text": "Claim description", "cited_evidence_ids": ["ev_id"]}
  ]
}
"""


def _split_sentences(text: str) -> List[str]:
    """Split text into sentences while respecting common abbreviations."""
    cleaned = text.strip()
    if not cleaned:
        return []
    # Split on sentence terminals followed by space
    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    return [p.strip() for p in parts if p.strip()]


def _extract_evidence_ids(text: str) -> List[str]:
    """Extract all [ev_...] identifiers cited in text."""
    matches = re.findall(r"\[(ev_[a-zA-Z0-9_-]+)\]", text)
    # Deduplicate while preserving order of appearance
    seen = set()
    result = []
    for m in matches:
        if m not in seen:
            seen.add(m)
            result.append(m)
    return result


class AnswerDrafter:
    """Drafts short, citation-dense, verified direct answers."""

    def __init__(self, llm_adapter: LLMProviderAdapter) -> None:
        self.llm = llm_adapter

    async def draft(
        self,
        query: str,
        evidence: List[EvidenceItem],
        analysis: Optional[AnalysisResult] = None,
        critique: Optional[CritiqueReport] = None,
        previous_answer: Optional[DraftAnswer] = None,
        self_check_verdict: Optional[SelfCheckVerdict] = None,
        budget: Optional[RunBudget] = None,
    ) -> DraftAnswer:
        # Filter out rejected or invalid evidence
        active_evidence = [
            e for e in evidence
            if e.verification_state != VerificationState.REJECTED and e.canonical_url
        ]

        if not active_evidence:
            return DraftAnswer(
                text=f"Insufficient verified evidence retrieved for '{query}'.",
                sentences=[f"Insufficient verified evidence retrieved for '{query}'."],
                claims=[],
                cited_evidence_ids=[],
                is_hedged_fallback=True,
            )

        # Prioritize top 6 authoritative evidence items
        prioritized = sorted(
            active_evidence,
            key=lambda e: (
                1 if e.verification_state == VerificationState.VERIFIED else 0,
                e.relevance_score,
                1 if e.source_type in ("documentation", "papers") else 0,
                len(e.excerpt or ""),
            ),
            reverse=True,
        )[:6]

        evidence_context = []
        valid_id_map: Dict[str, EvidenceItem] = {}
        for it in prioritized:
            valid_id_map[it.evidence_id] = it
            evidence_context.append(
                f"- ID: [{it.evidence_id}]\n"
                f"  Title: {it.title or it.publisher_or_repo}\n"
                f"  Type: {it.source_type}\n"
                f"  Excerpt: {it.excerpt[:400]}"
            )

        context_str = "\n".join(evidence_context)

        # Build prompt
        prompt_lines = [
            f"User Research Query: {query}",
        ]
        if analysis and analysis.domain_anchor:
            prompt_lines.append(f"Domain Anchor: {analysis.domain_anchor}")

        prompt_lines.append(f"\nAvailable Verified Evidence Items:\n{context_str}")

        # If revising after a failed self-check
        if self_check_verdict and not self_check_verdict.passed and previous_answer:
            prompt_lines.append("\nPREVIOUS DRAFT REVISION REQUIRED:")
            prompt_lines.append(f"Previous Text: {previous_answer.text}")
            if self_check_verdict.unsupported_claims:
                prompt_lines.append(f"Unsupported Claims: {', '.join(self_check_verdict.unsupported_claims)}")
            if self_check_verdict.contradicted_claims:
                prompt_lines.append(f"Contradicted Claims: {', '.join(self_check_verdict.contradicted_claims)}")
            if self_check_verdict.overclaiming:
                prompt_lines.append(f"Overclaiming: {', '.join(self_check_verdict.overclaiming)}")
            if self_check_verdict.suggested_revision:
                prompt_lines.append(f"Suggested Revision Direction: {self_check_verdict.suggested_revision}")
            prompt_lines.append("Please rewrite the 2-4 sentences to directly correct these issues.")

        prompt = "\n".join(prompt_lines)

        try:
            if budget and not budget.can_call_llm():
                raise RuntimeError("Budget exhausted for AnswerDrafter")

            from langchain_core.messages import HumanMessage, SystemMessage
            messages = [
                SystemMessage(content=_ANSWER_DRAFTER_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ]
            resp = await self.llm.ainvoke(
                messages,
                temperature=0.2,
                budget=budget,
            )

            raw = resp.strip() if isinstance(resp, str) else resp.content.strip()
            # Strip markdown json block if present
            if raw.startswith("```"):
                lines = raw.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                raw = "\n".join(lines).strip()

            parsed = json.loads(raw)
            text = parsed.get("text", "").strip()
            sentences = parsed.get("sentences") or _split_sentences(text)
            claims_data = parsed.get("claims") or []

            claims = []
            for c in claims_data:
                c_text = c.get("claim_text", "")
                c_ids = [eid for eid in c.get("cited_evidence_ids", []) if eid in valid_id_map]
                claims.append(ClaimItem(claim_text=c_text, cited_evidence_ids=c_ids))

            cited_ids = _extract_evidence_ids(text)
            # Filter to only valid cited IDs
            valid_cited_ids = [eid for eid in cited_ids if eid in valid_id_map]

            if not text or len(sentences) == 0:
                raise ValueError("LLM returned empty answer text")

            return DraftAnswer(
                text=text,
                sentences=sentences,
                claims=claims,
                cited_evidence_ids=valid_cited_ids,
                is_hedged_fallback=False,
            )

        except Exception as exc:
            logger.warning("answer_drafter.fallback_used", error=str(exc))
            return self._build_deterministic_fallback(query, prioritized)

    def _build_deterministic_fallback(self, query: str, evidence: List[EvidenceItem]) -> DraftAnswer:
        """Construct a safe, strictly grounded fallback answer using top verified evidence excerpts."""
        if not evidence:
            return DraftAnswer(
                text=f"No verified empirical evidence was available for query '{query}'.",
                sentences=[f"No verified empirical evidence was available for query '{query}'."],
                claims=[],
                cited_evidence_ids=[],
                is_hedged_fallback=True,
            )

        # Assemble up to 3 concise sentences directly from excerpts
        sentences = []
        cited_ids = []
        claims = []

        for it in evidence[:3]:
            # Take first clean sentence from excerpt
            excerpt_sentences = _split_sentences(it.excerpt)
            clean_s = excerpt_sentences[0] if excerpt_sentences else it.excerpt[:150]
            if not clean_s.endswith((".", "!", "?")):
                clean_s += "."
            s_with_cite = f"{clean_s} [{it.evidence_id}]"
            sentences.append(s_with_cite)
            cited_ids.append(it.evidence_id)
            claims.append(ClaimItem(claim_text=clean_s, cited_evidence_ids=[it.evidence_id]))

        if len(sentences) == 1 and len(evidence) > 1:
            it2 = evidence[1]
            s2 = f"Additional verified source indicates {it2.title or it2.publisher_or_repo} [{it2.evidence_id}]."
            sentences.append(s2)
            cited_ids.append(it2.evidence_id)
            claims.append(ClaimItem(claim_text=s2, cited_evidence_ids=[it2.evidence_id]))

        full_text = " ".join(sentences)
        return DraftAnswer(
            text=full_text,
            sentences=sentences,
            claims=claims,
            cited_evidence_ids=cited_ids,
            is_hedged_fallback=True,
        )
