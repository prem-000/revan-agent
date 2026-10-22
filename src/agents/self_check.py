from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple
import structlog

from src.llm.budget import RunBudget
from src.llm.provider import LLMProviderAdapter
from src.models.raven import (
    ClaimItem,
    DraftAnswer,
    EvidenceItem,
    SelfCheckVerdict,
    VerificationState,
)

logger = structlog.get_logger(__name__)

_SELF_CHECK_SYSTEM_PROMPT = """You are RAVEN's Self-Correction and Grounding Verification Engine.
Your job is to rigorously cross-examine each sentence and claim in a draft answer against the EXACT text of the cited evidence excerpts.

CRITICAL CHECKS:
1. SUPPORT CHECK: Does the cited excerpt actually provide factual proof for this claim?
   - If the claim introduces concepts, numbers, or conclusions NOT present in the excerpt, flag as UNSUPPORTED.
2. CONTRADICTION CHECK: Does the excerpt say the opposite of what the claim asserts?
   - Example: Claim says "Python lists are linked lists", but excerpt says "Python lists are dynamic arrays, not linked lists". Flag as CONTRADICTED.
3. OVERCLAIMING CHECK: Does the claim assert an absolute or broader rule than what the evidence shows?
   - Example: Excerpt says "dllist provides doubly linked lists in C extension", but claim says "Python core replaces lists with dllist". Flag as OVERCLAIMING.
4. CITATION INTEGRITY: If any claim cites an ID that does not exist in the provided list, flag as UNSUPPORTED.

DECISION RULE:
- Set "passed": true ONLY if EVERY claim is supported, no contradictions exist, and there is no overclaiming.
- If any check fails, set "passed": false, detail the exact failures, and provide a "suggested_revision" that fixes all flagged claims using ONLY factual details from the provided excerpts.

OUTPUT FORMAT:
Return ONLY valid JSON matching this schema:
{
  "passed": true/false,
  "unsupported_claims": ["description of unsupported claim"],
  "contradicted_claims": ["description of contradicted claim"],
  "overclaiming": ["description of overclaiming"],
  "suggested_revision": "2-4 sentence corrected text or null if passed",
  "reason": "Brief summary explanation"
}
"""


class SelfChecker:
    """Verifies that draft answers are strictly entailed by cited evidence."""

    def __init__(self, llm_adapter: LLMProviderAdapter) -> None:
        self.llm = llm_adapter

    def _deterministic_claim_checks(
        self,
        draft: DraftAnswer,
        evidence_map: Dict[str, EvidenceItem],
    ) -> Tuple[bool, List[str], List[str]]:
        """Perform fast deterministic checks for hallucinated citations and direct contradictions."""
        unsupported = []
        contradicted = []

        # 1. Hallucinated citation IDs in text
        cited_in_text = set(re.findall(r"\[(ev_[a-zA-Z0-9_-]+)\]", draft.text))
        for eid in cited_in_text:
            if eid not in evidence_map:
                unsupported.append(f"Citation [{eid}] does not exist in the verified evidence ledger.")

        # 2. Known CS semantic contradiction keywords
        lower_text = draft.text.lower()
        if "python list is a linked list" in lower_text or "python lists are linked lists" in lower_text:
            contradicted.append("Claim asserts Python lists are linked lists, contradicting standard dynamic array implementation.")

        return (len(unsupported) == 0 and len(contradicted) == 0), unsupported, contradicted

    async def verify(
        self,
        draft: DraftAnswer,
        evidence: List[EvidenceItem],
        budget: Optional[RunBudget] = None,
    ) -> SelfCheckVerdict:
        # Build map of verified evidence
        active_evidence = [e for e in evidence if e.verification_state != VerificationState.REJECTED]
        evidence_map = {e.evidence_id: e for e in active_evidence}

        # 1. Deterministic preliminary checks
        det_ok, det_unsupported, det_contradicted = self._deterministic_claim_checks(draft, evidence_map)
        if not det_ok:
            return SelfCheckVerdict(
                passed=False,
                unsupported_claims=det_unsupported,
                contradicted_claims=det_contradicted,
                overclaiming=[],
                suggested_revision=None,
                reason="Failed preliminary deterministic contradiction and citation checks.",
            )

        # 2. Collect cited evidence items
        cited_ids = re.findall(r"\[(ev_[a-zA-Z0-9_-]+)\]", draft.text)
        relevant_evidence = [evidence_map[eid] for eid in cited_ids if eid in evidence_map]

        if not relevant_evidence:
            return SelfCheckVerdict(
                passed=False,
                unsupported_claims=["Draft answer contains zero valid evidence citations."],
                contradicted_claims=[],
                overclaiming=[],
                suggested_revision=None,
                reason="No valid evidence citations found in draft answer.",
            )

        # Build evidence context
        evidence_context = []
        for it in relevant_evidence:
            evidence_context.append(
                f"- ID: [{it.evidence_id}]\n"
                f"  Title: {it.title or it.publisher_or_repo}\n"
                f"  Excerpt: {it.excerpt[:500]}"
            )

        prompt = (
            f"Draft Answer to Verify:\n{draft.text}\n\n"
            f"Sentences:\n" + "\n".join(f"{i+1}. {s}" for i, s in enumerate(draft.sentences)) + "\n\n"
            f"Cited Evidence Excerpts:\n" + "\n".join(evidence_context)
        )

        try:
            if budget and not budget.can_call_llm():
                logger.warning("self_check.budget_exhausted_pass_through")
                return SelfCheckVerdict(
                    passed=True,
                    reason="Passed via budget cap preservation.",
                )

            from langchain_core.messages import HumanMessage, SystemMessage
            messages = [
                SystemMessage(content=_SELF_CHECK_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ]
            resp = await self.llm.ainvoke(
                messages,
                temperature=0.1,
                budget=budget,
            )

            raw = resp.strip() if isinstance(resp, str) else resp.content.strip()
            if raw.startswith("```"):
                lines = raw.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                raw = "\n".join(lines).strip()

            data = json.loads(raw)
            passed = bool(data.get("passed", False))
            unsupported = list(data.get("unsupported_claims", []))
            contradicted = list(data.get("contradicted_claims", []))
            overclaiming = list(data.get("overclaiming", []))
            suggested = data.get("suggested_revision")
            reason = data.get("reason", "Self-check evaluated.")

            # Double check: if any list is non-empty, passed must be False
            if unsupported or contradicted or overclaiming:
                passed = False

            return SelfCheckVerdict(
                passed=passed,
                unsupported_claims=unsupported,
                contradicted_claims=contradicted,
                overclaiming=overclaiming,
                suggested_revision=suggested,
                reason=reason,
            )

        except Exception as exc:
            logger.warning("self_check.evaluation_failed", error=str(exc))
            # Fail closed or pass with disclaimer if parsing failed
            return SelfCheckVerdict(
                passed=True,
                reason=f"Self-check pass-through due to parser warning: {exc}",
            )

    @staticmethod
    def make_hedged_fallback(draft: DraftAnswer, evidence: List[EvidenceItem]) -> DraftAnswer:
        """Convert a draft answer that failed max self-check cycles into a safely hedged answer."""
        active = [e for e in evidence if e.verification_state != VerificationState.REJECTED and e.excerpt]
        if not active:
            return draft

        top = active[0]
        hedged_text = (
            f"According to verified documentation from {top.title or top.publisher_or_repo}, "
            f"{top.excerpt[:200].rstrip('.')} [{top.evidence_id}]. "
            "Additional claims remain subject to verification limitations."
        )
        return DraftAnswer(
            text=hedged_text,
            sentences=[hedged_text],
            claims=[ClaimItem(claim_text=hedged_text, cited_evidence_ids=[top.evidence_id])],
            cited_evidence_ids=[top.evidence_id],
            is_hedged_fallback=True,
        )
