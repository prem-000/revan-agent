from __future__ import annotations

import json
import re
from typing import Dict, List, Optional
import structlog
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage, SystemMessage

from src.llm.groq_adapter import LLMProviderAdapter
from src.llm.budget import RunBudget
from src.models.raven import (
    AnalysisResult,
    CritiqueReport,
    EvidenceItem,
    FinalRavenReport,
    ResearchPlan,
    VerificationState,
)

logger = structlog.get_logger(__name__)

SYNTHESIZER_SYSTEM_PROMPT = """You are RAVEN's Senior Research Synthesizer. Your job is to transform raw evidence and inspected technical resources into a comprehensive, authoritative, editorial-grade intelligence report.

CRITICAL OPERATIONAL RULES:
1. CONTENT-FIRST ANSWERS: Provide a substantive, detailed technical answer, not just a bibliography of links.
   - For algorithmic or data structure queries (e.g., linked lists, trees, caches, graphs): explain the fundamental concept, node structure and memory layout, step-by-step mechanics of primary operations (insertion, deletion, traversal, search), time and space complexity breakdown (best, average, worst, auxiliary), trade-offs vs alternatives (e.g. arrays), common variants (singly, doubly, circular, sentinel nodes), implementation examples in code, and common pitfalls.
   - For framework, library, or architecture queries: deliver concrete technical depth, design trade-offs, code examples, comparative benchmarks, and integration guidelines supported by inspected source content.
2. CITATION DISCIPLINE: Always cite claims using the exact Evidence ID in brackets, e.g. [ev_web_12345678] or [ev_doc_abcd1234]. Do NOT invent [S1], [S2] numbers yourself; use the exact Evidence IDs provided in the ledger. Every key technical claim must cite an evidence ID from the ledger.
3. OUTPUT FIELDS:
   - direct_answer: An immediate, definitive 1-2 paragraph answer addressing the user's query directly, citing evidence IDs.
   - detailed_explanation: A comprehensive, multi-section markdown explanation with subheadings (###), code blocks, complexity tables, and operational steps. Cite evidence IDs.
   - key_findings: 3 to 6 verified facts discovered in the inspected resources.
   - practical_guidance: Concrete engineering advice, implementation tips, or best practices.
   - limitations_and_uncertainty: Unresolved trade-offs, inaccessible endpoints, or items that could not be fully verified.
4. RIGOR: Never invent citations or facts. If a claim cannot be verified, state it clearly in limitations.
"""


class SynthesizerLLMOutput(BaseModel):
    """Concise structured schema for LLM generation to respect Groq request payload limits."""
    title: str = "Technical Intelligence Report"
    direct_answer: str = Field(description="Direct 1-2 paragraph explanatory answer to the user's question citing Evidence IDs like [ev_web_...].")
    detailed_explanation: str = Field(description="Comprehensive multi-section explanation formatted in Markdown with subheadings, mechanics, complexity tables, code examples, and trade-offs citing Evidence IDs.")
    key_findings: List[str] = Field(default_factory=list, description="3 to 6 key takeaways grounded in the inspected evidence.")
    practical_guidance: List[str] = Field(default_factory=list, description="Concrete operational steps, engineering advice, or implementation tips.")
    limitations_and_uncertainty: List[str] = Field(default_factory=list, description="Unresolved questions, trade-offs, or items that could not be fully verified.")
    executive_summary: str = Field(default="", description="A concise executive overview.")


def remap_citations_in_text(
    text: str,
    valid_evidence: Dict[str, EvidenceItem],
    id_to_index: Dict[str, int],
    ordered_evidence: List[EvidenceItem],
) -> str:
    """Scan text for evidence IDs [ev_...] and replace them with [S#] according to order of appearance."""
    if not text:
        return ""

    ev_pattern = re.compile(r"\[?(ev_[a-zA-Z0-9_-]+)\]?")

    def _replace_id(m: re.Match) -> str:
        eid = m.group(1)
        if eid in valid_evidence:
            if eid not in id_to_index:
                ordered_evidence.append(valid_evidence[eid])
                id_to_index[eid] = len(ordered_evidence)
            idx = id_to_index[eid]
            return f"[S{idx}]"
        # Phantom ID not present in valid evidence ledger -> strip it
        return ""

    remapped = ev_pattern.sub(_replace_id, text)

    # In case the model already output legacy [S#] numbers, ensure they are bounded
    s_pattern = re.compile(r"\[S(\d+)\]")

    def _validate_s(m: re.Match) -> str:
        num = int(m.group(1))
        if 1 <= num <= len(valid_evidence):
            return m.group(0)
        return ""

    return s_pattern.sub(_validate_s, remapped)


class Synthesizer:
    """Synthesizes the final evidence-grounded research report."""

    def __init__(self, llm_adapter: LLMProviderAdapter) -> None:
        self.llm = llm_adapter

    async def synthesize(
        self,
        query: str,
        plan: ResearchPlan,
        evidence: List[EvidenceItem],
        analysis: Optional[AnalysisResult] = None,
        critique: Optional[CritiqueReport] = None,
        failed_tunnels: Optional[List[str]] = None,
        stop_reason: str = "Research goals achieved",
        budget: Optional[RunBudget] = None,
    ) -> FinalRavenReport:
        failed = failed_tunnels or []
        critique_summary = critique.reason if critique else "Initial cycle completed."

        # Exclude rejected evidence from synthesis
        active_evidence = [e for e in evidence if e.verification_state != VerificationState.REJECTED]

        # Prepare evidence ledger summary with prioritized substantive excerpts
        prioritized_evidence = sorted(
            active_evidence,
            key=lambda e: (
                1 if e.verification_state.value == "verified" else 0,
                e.relevance_score,
                1 if e.extraction_method in ("http", "playwright", "github_api") else 0,
                len(e.excerpt or ""),
            ),
            reverse=True,
        )[:6]

        ledger_entries = []
        for it in prioritized_evidence:
            exp = it.resource_explanation or {}
            ledger_entries.append(
                f"ID: [{it.evidence_id}] | Source: {it.source_type} ({it.canonical_url})\n"
                f"  Title: {it.title or it.publisher_or_repo}\n"
                f"  Freshness: {it.freshness_state.value} | Verified: {it.verification_state.value} | Method: {it.extraction_method}\n"
                f"  Summary: {exp.get('what_it_provides', 'Technical reference')}\n"
                f"  Extracted Content: {it.excerpt[:350]}"
            )

        context_prompt = (
            f"User Question / Research Topic: {query}\n"
            f"Stop Reason: {stop_reason}\n"
            f"Critique Summary: {critique_summary}\n"
            f"Failed / Disabled Tunnels: {', '.join(failed) or 'None'}\n\n"
            f"=== VERIFIED EVIDENCE LEDGER ({len(prioritized_evidence)} primary items) ===\n"
            + ("\n\n".join(ledger_entries) if ledger_entries else "No evidence collected.")
        )

        messages = [
            SystemMessage(content=SYNTHESIZER_SYSTEM_PROMPT),
            HumanMessage(content=context_prompt),
        ]

        valid_evidence_by_id = {e.evidence_id: e for e in active_evidence}

        try:
            llm_output = await self.llm.ainvoke_structured(
                messages,
                SynthesizerLLMOutput,
                temperature=0.2,
                budget=budget,
            )

            # Deterministic citation remapping: [ev_...] -> [S1], [S2] in order of first appearance
            id_to_index: Dict[str, int] = {}
            ordered_cited_evidence: List[EvidenceItem] = []

            direct_answer = remap_citations_in_text(
                llm_output.direct_answer, valid_evidence_by_id, id_to_index, ordered_cited_evidence
            )
            detailed_explanation = remap_citations_in_text(
                llm_output.detailed_explanation, valid_evidence_by_id, id_to_index, ordered_cited_evidence
            )
            key_findings = [
                remap_citations_in_text(f, valid_evidence_by_id, id_to_index, ordered_cited_evidence)
                for f in llm_output.key_findings
            ]
            practical_guidance = [
                remap_citations_in_text(g, valid_evidence_by_id, id_to_index, ordered_cited_evidence)
                for g in llm_output.practical_guidance
            ]

            # Build evidence references matching exact [S#] renumbering order
            # Append any un-cited prioritized evidence at the end
            for it in active_evidence:
                if it.evidence_id not in id_to_index:
                    ordered_cited_evidence.append(it)
                    id_to_index[it.evidence_id] = len(ordered_cited_evidence)

            report = FinalRavenReport(
                title=llm_output.title or f"Technical Intelligence: {query}",
                direct_answer=direct_answer,
                detailed_explanation=detailed_explanation,
                key_findings=key_findings,
                practical_guidance=practical_guidance,
                limitations_and_uncertainty=llm_output.limitations_and_uncertainty,
                executive_summary=llm_output.executive_summary or direct_answer,
                failed_tunnels=failed,
                stop_reason=stop_reason,
                run_status="completed",
            )

            report.evidence_citations = [
                {
                    "index": idx,
                    "id": e.evidence_id,
                    "url": e.canonical_url,
                    "title": e.title or e.publisher_or_repo,
                    "source_type": e.source_type,
                    "freshness": e.freshness_state.value,
                    "verification": e.verification_state.value,
                    "method": e.extraction_method,
                    "excerpt": e.excerpt[:300],
                }
                for idx, e in enumerate(ordered_cited_evidence, 1)
            ]

            report.formatted_markdown = self._format_markdown(report, ordered_cited_evidence, analysis=analysis)
            return report

        except Exception as exc:
            logger.error("synthesizer.llm_failed", error=str(exc))
            fallback_report = FinalRavenReport(
                title=f"Technical Intelligence: {query}",
                direct_answer=f"Synthesis could not be completed via Groq ({exc}). Retrieved sources are preserved below.",
                executive_summary=f"Partial result: {len(active_evidence)} sources collected, but LLM synthesis was interrupted.",
                detailed_explanation="",
                key_findings=[f"Gathered {len(active_evidence)} candidate sources across active tunnels."],
                limitations_and_uncertainty=[f"Automated synthesis interrupted: {exc}"],
                stop_reason=f"Synthesis interrupted: {exc}",
                failed_tunnels=failed,
                run_status="partial",
            )
            fallback_report.formatted_markdown = self._format_markdown(fallback_report, active_evidence, analysis=analysis)
            return fallback_report

    def _format_markdown(
        self,
        report: FinalRavenReport,
        evidence: List[EvidenceItem],
        analysis: Optional[AnalysisResult] = None,
    ) -> str:
        sections = [f"# {report.title}\n"]

        if report.run_status == "partial":
            sections.append(
                "> **Warning: Partial Research Report**\n"
                "> LLM synthesis could not be completed by Groq. The inspected sources and extracted data are preserved below for reference.\n"
            )

        # 1. Direct Answer
        direct = report.direct_answer or report.executive_summary
        if direct:
            sections.append("## Direct Answer\n")
            sections.append(f"{direct}\n")

        # 2. Key Findings
        findings = report.key_findings or report.sourced_facts
        if findings:
            sections.append("## Key Findings\n")
            for f in findings:
                sections.append(f"- {f}")
            sections.append("")

        # 3. Detailed Explanation
        if report.detailed_explanation:
            sections.append("## Detailed Explanation\n")
            sections.append(f"{report.detailed_explanation}\n")
        elif report.findings_by_objective:
            sections.append("## Detailed Findings\n")
            for obj_id, finding in report.findings_by_objective.items():
                sections.append(f"### {obj_id}\n{finding}\n")

        # 4. Inferences & Comparative Synthesis
        if report.inferences:
            sections.append("## Analysis & Inferences\n")
            for inf in report.inferences:
                sections.append(f"- {inf}")
            sections.append("")

        # 5. Practical Guidance & Recommendations
        guidance = report.practical_guidance or report.recommendations
        if guidance:
            sections.append("## Practical Guidance\n")
            for item in guidance:
                sections.append(f"- {item}")
            sections.append("")

        # 6. Limitations & Uncertainties
        limitations = report.limitations_and_uncertainty or report.unknowns_and_limitations
        if limitations:
            sections.append("## Limitations & Uncertainties\n")
            for unk in limitations:
                sections.append(f"- {unk}")
            sections.append("")

        # 7. Repository Comparison (Only if NOT a concept query and relevant GitHub evidence exists)
        is_concept = getattr(analysis, "is_concept_query", False) if analysis else False
        gh_items = [
            e for e in evidence
            if e.source_type == "github"
            and e.repository_meta
            and e.verification_state != VerificationState.REJECTED
        ]
        if gh_items and not is_concept:
            sections.append("## Repository Comparison\n")
            sections.append(
                "| Repository | Language | Stars | License | Latest Push | Recommendation |\n"
                "|---|---|---|---|---|---|"
            )
            for gh in gh_items:
                meta = gh.repository_meta or {}
                name = meta.get("full_name") or gh.title
                url = gh.canonical_url
                lang = meta.get("language") or "Unknown"
                stars = meta.get("stars", 0)
                license_str = meta.get("license") or "Unknown"
                pushed = (meta.get("pushed_at") or "Unknown").split("T")[0]
                rec = (gh.resource_explanation or {}).get("recommendation", "Evaluate for reuse")
                sections.append(
                    f"| [{name}]({url}) | {lang} | {stars} | {license_str} | {pushed} | {rec} |"
                )
            sections.append("")

        # 8. Sources & Verified Evidence (Compact & Secondary)
        if evidence:
            sections.append("## Sources & Verified Evidence\n")
            for idx, item in enumerate(evidence, 1):
                exp = item.resource_explanation or {}
                provides = exp.get("what_it_provides", "Technical evidence")
                method = item.extraction_method.upper()
                freshness = item.freshness_state.value
                verification = item.verification_state.value
                card = [
                    f"**[S{idx}] [{item.title or item.canonical_url}]({item.canonical_url})** — `{item.source_type}`",
                    f"- *Extraction:* `{method}` | *Freshness:* `{freshness}` | *Status:* `{verification}`",
                    f"- *Summary:* {provides}",
                ]
                if item.excerpt:
                    clean_excerpt = item.excerpt[:240].replace("\n", " ").strip()
                    card.append(f"- *Excerpt:* \"{clean_excerpt}...\"")
                sections.append("\n".join(card) + "\n")

        # 9. Tunnel Disclosures
        if report.failed_tunnels:
            sections.append("## Tunnel Disclosures\n")
            sections.append(
                f"The following source tunnels were unavailable during this run: {', '.join(report.failed_tunnels)}.\n"
            )

        return "\n".join(sections)
