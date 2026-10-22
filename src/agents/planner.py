from __future__ import annotations

import json
from typing import Optional
import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from src.llm.groq_adapter import LLMProviderAdapter
from src.llm.budget import RunBudget
from src.models.raven import (
    AnalysisResult,
    FreshnessState,
    IntentType,
    ResearchObjective,
    ResearchPlan,
)

logger = structlog.get_logger(__name__)

PLANNER_SYSTEM_PROMPT = """You are RAVEN's Planner. You produce a structured, rigorous Research Plan with clear objectives.
For each objective:
- Assign an id like 'obj_1', 'obj_2'.
- Give a concise description.
- Provide 2-3 specific research questions.
- Define 2-3 measurable acceptance criteria.
- Select relevant source tunnels from: ['github', 'documentation', 'papers', 'web', 'community', 'local_project'].
- Specify freshness requirement: 'fresh' (needs recent 2024-2026 data/releases), 'aging', or 'unknown'.

Ensure the plan has 2-4 focused objectives covering the user's core goals, risks, and comparisons.
"""


class Planner:
    """Produces structured research objectives, criteria, and tunnel assignments."""

    def __init__(self, llm_adapter: LLMProviderAdapter) -> None:
        self.llm = llm_adapter

    async def plan(
        self,
        query: str,
        analysis: AnalysisResult,
        memory_context: str = "",
        profile: str = "standard",
        approval_policy: str = "auto",
        budget: Optional[RunBudget] = None,
    ) -> ResearchPlan:
        """Generate structured research plan."""
        context_prompt = (
            f"User Goal: {analysis.goal}\n"
            f"Intent: {analysis.intent.value}\n"
            f"Constraints: {', '.join(analysis.constraints) or 'None'}\n"
            f"Unknowns: {', '.join(analysis.unknowns) or 'None'}\n"
            f"Profile: {profile}\n"
        )
        if analysis.project_inspection and analysis.project_inspection.is_valid:
            context_prompt += (
                f"\nLocal Project:\n"
                f"Stack: {analysis.project_inspection.detected_stack}\n"
                f"Summary: {analysis.project_inspection.structure_summary}\n"
                f"Risks: {analysis.project_inspection.risks_and_gaps}\n"
            )
        if memory_context:
            context_prompt += f"\nRelevant Memory Context:\n{memory_context}\n"

        messages = [
            SystemMessage(content=PLANNER_SYSTEM_PROMPT),
            HumanMessage(content=context_prompt),
        ]

        try:
            plan = await self.llm.ainvoke_structured(
                messages,
                ResearchPlan,
                temperature=0.1,
                budget=budget,
            )
            plan.profile = profile
            plan.approval_policy = approval_policy
            plan.is_approved = (approval_policy == "auto")

            # Ensure local_project tunnel is assigned if intent is PROJECT
            if analysis.intent == IntentType.PROJECT and plan.objectives:
                if "local_project" not in plan.objectives[0].assigned_tunnels:
                    plan.objectives[0].assigned_tunnels.append("local_project")

            return plan

        except Exception as e:
            logger.warning("planner.llm_fallback", error=str(e))
            # Fallback deterministic plan
            tunnels = ["web", "papers", "documentation"]
            if analysis.intent == IntentType.PROJECT:
                tunnels.append("local_project")

            obj1 = ResearchObjective(
                id="obj_1",
                description=f"Investigate core requirements and state of the art for: {query[:80]}",
                questions=[f"What are key architectural patterns for {query[:60]}?"],
                acceptance_criteria=["Identify verified best practices and evidence-supported solutions"],
                assigned_tunnels=tunnels,
                freshness_requirement=FreshnessState.FRESH,
            )
            return ResearchPlan(
                profile=profile,
                objectives=[obj1],
                is_approved=True,
                approval_policy=approval_policy,
            )
