from __future__ import annotations

from typing import List, Optional
import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from src.llm.groq_adapter import LLMProviderAdapter
from src.llm.budget import RunBudget
from src.models.raven import (
    CritiqueGap,
    CritiqueReport,
    EvidenceItem,
    ResearchObjective,
    ResearchPlan,
)

logger = structlog.get_logger(__name__)

CRITIC_SYSTEM_PROMPT = """You are RAVEN's Critic. You evaluate whether gathered evidence sufficiently addresses each research objective.
Be rigorous and skeptical.
Identify:
- coverage_score: 0.0 to 1.0 (1.0 = comprehensive, fully verified evidence across all objectives)
- unresolved_gaps: specific objectives lacking solid proof or missing concrete comparisons
- contradictions: any conflicting findings between different sources (do not force a false consensus)
- risks: potential architectural or implementation risks identified in the evidence
- should_continue: boolean indicating whether another targeted research pass is recommended
- reason: concise explanation for continue/stop recommendation
"""


class Critic:
    """Evaluates evidence coverage, flags contradictions/risks, and plans targeted gap tasks."""

    def __init__(self, llm_adapter: LLMProviderAdapter) -> None:
        self.llm = llm_adapter

    async def critique(
        self,
        plan: ResearchPlan,
        evidence: List[EvidenceItem],
        round_number: int,
        max_rounds: int = 3,
        budget: Optional[RunBudget] = None,
    ) -> CritiqueReport:
        # Check hard budget stop conditions first
        if budget and (budget.is_calls_exhausted() or budget.is_time_exhausted()):
            return CritiqueReport(
                coverage_score=0.7,
                should_continue=False,
                reason="Shared run budget (calls or time) reached; proceeding to synthesis.",
            )

        if round_number >= max_rounds:
            return CritiqueReport(
                coverage_score=0.8,
                should_continue=False,
                reason=f"Maximum research rounds ({max_rounds}) reached.",
            )

        if not evidence:
            return CritiqueReport(
                coverage_score=0.1,
                unresolved_gaps=[
                    CritiqueGap(
                        objective_id=obj.id,
                        description="No verified evidence gathered yet.",
                        severity="blocker",
                        suggested_tunnel="web",
                    )
                    for obj in plan.objectives
                ],
                should_continue=True,
                reason="Initial evidence gathering yielded no items; retry with targeted search.",
            )

        # Prepare summary of objectives vs gathered evidence
        summary_lines = []
        for obj in plan.objectives:
            matching = [e for e in evidence if e.objective_id == obj.id]
            summary_lines.append(
                f"Objective [{obj.id}]: {obj.description}\n"
                f"  Evidence items gathered: {len(matching)}\n"
                f"  Sample excerpts: {'; '.join(e.excerpt[:150] for e in matching[:3])}"
            )

        messages = [
            SystemMessage(content=CRITIC_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"Research Round: {round_number}/{max_rounds}\n"
                    f"Evidence Summary:\n" + "\n\n".join(summary_lines)
                )
            ),
        ]

        try:
            report = await self.llm.ainvoke_structured(
                messages,
                CritiqueReport,
                temperature=0.1,
                budget=budget,
            )
            # Enforce hard round limit override
            if round_number >= max_rounds:
                report.should_continue = False
            return report

        except Exception as e:
            logger.warning("critic.llm_fallback", error=str(e))
            # Fallback heuristic
            has_enough = len(evidence) >= (len(plan.objectives) * 2)
            return CritiqueReport(
                coverage_score=0.75 if has_enough else 0.4,
                should_continue=not has_enough and round_number < max_rounds,
                reason="Heuristic evaluation based on evidence volume.",
            )

    @staticmethod
    def create_gap_objectives(
        original_plan: ResearchPlan,
        critique: CritiqueReport,
    ) -> List[ResearchObjective]:
        """Produce targeted follow-up tasks strictly bounded by unresolved gaps in original objectives."""
        gap_objs: List[ResearchObjective] = []
        for gap in critique.unresolved_gaps:
            # Locate parent objective to maintain scope integrity
            parent = next((o for o in original_plan.objectives if o.id == gap.objective_id), None)
            if parent:
                gap_objs.append(
                    ResearchObjective(
                        id=f"{gap.objective_id}_gap",
                        description=f"Targeted follow-up: {gap.description}",
                        questions=[gap.description],
                        acceptance_criteria=parent.acceptance_criteria,
                        assigned_tunnels=[gap.suggested_tunnel or "web"],
                        freshness_requirement=parent.freshness_requirement,
                    )
                )
        return gap_objs
