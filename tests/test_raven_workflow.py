from __future__ import annotations

import os
import pytest
from pathlib import Path

from src.agents.analyzer import Analyzer
from src.agents.critic import Critic
from src.agents.planner import Planner
from src.agents.raven_graph import build_raven_graph
from src.agents.synthesizer import Synthesizer
from src.llm.budget import RunBudget
from src.llm.provider import MockProviderAdapter
from src.memory.layered_memory import LayeredMemorySystem
from src.memory.project_inspector import ProjectInspector
from src.models.raven import (
    AnalysisResult,
    CritiqueReport,
    EvidenceItem,
    FreshnessState,
    IntentType,
    ResearchObjective,
    ResearchPlan,
    VerificationState,
)
from src.tunnels.dispatcher import TunnelDispatcher
from src.tunnels.normalizer import normalize_url


class TestAnalyzerAndInspector:
    def test_inspect_current_directory_safe(self):
        inspector = ProjectInspector()
        inspection = inspector.inspect_directory(os.getcwd())
        assert inspection.is_valid is True
        assert len(inspection.manifest_files) > 0
        assert "Python" in inspection.detected_stack

    @pytest.mark.asyncio
    async def test_analyzer_detects_project(self):
        mock_resp = (
            '{"intent": "project", "goal": "Analyze local code", '
            '"constraints": [], "assumptions": [], "unknowns": []}'
        )
        llm = MockProviderAdapter(canned_response=mock_resp)
        analyzer = Analyzer(llm_adapter=llm)
        analysis = await analyzer.analyze(f"Inspect project at {os.getcwd()}")
        assert analysis.intent == IntentType.PROJECT
        assert analysis.project_inspection is not None
        assert analysis.project_inspection.is_valid is True


class TestNormalizerAndVerifier:
    def test_url_normalization(self):
        dirty = "https://example.com/docs/?utm_source=twitter&utm_medium=social#overview"
        clean = normalize_url(dirty)
        assert "utm_source" not in clean
        assert "utm_medium" not in clean
        assert clean.startswith("https://example.com/docs")

    def test_evidence_verification_and_freshness(self):
        from src.tunnels.verifier import EvidenceVerifier

        item = EvidenceItem(
            evidence_id="",
            source_type="web",
            canonical_url="https://example.com/article",
            title="Modern AI",
            excerpt="A sufficiently comprehensive technical excerpt with more than forty characters for validation.",
            objective_id="obj_1",
            published_date="2026-01-15",
        )
        verified = EvidenceVerifier.verify_item(item)
        assert verified.verification_state == VerificationState.VERIFIED
        assert verified.freshness_state == FreshnessState.FRESH


class TestPlannerAndCritic:
    @pytest.mark.asyncio
    async def test_planner_creates_objectives(self):
        mock_plan = {
            "profile": "standard",
            "is_approved": True,
            "approval_policy": "auto",
            "objectives": [
                {
                    "id": "obj_1",
                    "description": "Analyze multi-agent architecture",
                    "questions": ["How do agents coordinate?"],
                    "acceptance_criteria": ["Evaluate latency and reliability"],
                    "assigned_tunnels": ["github", "papers", "web"],
                    "freshness_requirement": "fresh",
                }
            ],
        }
        import json
        llm = MockProviderAdapter(canned_response=json.dumps(mock_plan))
        planner = Planner(llm_adapter=llm)
        analysis = AnalysisResult(goal="Study multi-agent systems")
        plan = await planner.plan(query="Multi-agent systems", analysis=analysis)
        assert len(plan.objectives) == 1
        assert plan.objectives[0].id == "obj_1"
        assert "github" in plan.objectives[0].assigned_tunnels

    @pytest.mark.asyncio
    async def test_critic_stops_on_budget_exhaustion(self):
        llm = MockProviderAdapter()
        critic = Critic(llm_adapter=llm)
        plan = ResearchPlan(objectives=[ResearchObjective(id="obj_1", description="Test")])
        evidence = [EvidenceItem(evidence_id="ev_1", source_type="web", canonical_url="https://a.com", excerpt="Test excerpt...", objective_id="obj_1")]

        exhausted_budget = RunBudget(max_calls=1, calls_used=1)
        critique = await critic.critique(plan=plan, evidence=evidence, round_number=1, budget=exhausted_budget)
        assert critique.should_continue is False
        assert "budget" in critique.reason.lower()


class TestEndToEndWorkflow:
    @pytest.mark.asyncio
    async def test_full_graph_run_with_mock_llm(self):
        graph = build_raven_graph()
        initial_state = {
            "job_id": "test_run_123",
            "query": "Autonomous research agents architecture",
            "profile": "fast",
            "max_rounds": 1,
            "round_count": 1,
        }

        # Override provider factory temporarily with MockProviderAdapter
        with pytest.MonkeyPatch.context() as mp:
            from src.llm.provider import MockProviderAdapter
            mock_plan = {
                "profile": "fast",
                "is_approved": True,
                "approval_policy": "auto",
                "objectives": [
                    {
                        "id": "obj_1",
                        "description": "Examine state machines",
                        "questions": ["How to structure LangGraph?"],
                        "acceptance_criteria": ["Document nodes"],
                        "assigned_tunnels": ["web"],
                        "freshness_requirement": "unknown",
                    }
                ],
            }
            import json
            mp.setattr(
                "src.agents.raven_graph.get_llm_adapter",
                lambda: MockProviderAdapter(canned_response=json.dumps(mock_plan)),
            )

            result = await graph.ainvoke(initial_state)
            assert "analysis" in result
            assert "plan" in result
            assert "final_report" in result
            assert len(result.get("events", [])) >= 5
