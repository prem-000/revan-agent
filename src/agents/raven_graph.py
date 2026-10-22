from __future__ import annotations

import time
from typing import Any, Dict, List
import structlog
from langgraph.graph import END, START, StateGraph

from src.agents.analyzer import Analyzer
from src.agents.answer_drafter import AnswerDrafter
from src.agents.critic import Critic
from src.agents.planner import Planner
from src.agents.raven_state import RavenState, RavenWorkflowEvent
from src.agents.self_check import SelfChecker
from src.agents.synthesizer import Synthesizer
from src.config import get_settings
from src.evaluation.answer_eval import evaluate_answer_quality
from src.llm.budget import RunBudget
from src.llm.provider import get_llm_adapter
from src.memory.layered_memory import LayeredMemorySystem
from src.memory.project_inspector import ProjectInspector
from src.models.raven import (
    AnalysisResult,
    CritiqueReport,
    DraftAnswer,
    EvidenceItem,
    FinalRavenReport,
    ResearchPlan,
    SelfCheckVerdict,
    VerificationState,
)
from src.tunnels.dispatcher import TunnelDispatcher
from src.tunnels.relevance import RelevanceFilter

logger = structlog.get_logger(__name__)


def _record_event(state: RavenState, step: str, status: str, details: Dict[str, Any]) -> None:
    events = state.setdefault("events", [])
    seq = len(events) + 1
    event: RavenWorkflowEvent = {
        "seq": seq,
        "step": step,
        "status": status,
        "details": details,
        "timestamp": time.time(),
    }
    events.append(event)


# ── Node Implementations ───────────────────────────────────────────

async def analyzer_node(state: RavenState) -> RavenState:
    query = state.get("query", "")
    project_path = state.get("project_path")
    budget_dict = state.get("budget", {})
    budget = RunBudget.from_dict(budget_dict) if budget_dict else RunBudget()

    llm = get_llm_adapter()
    inspector = ProjectInspector()
    analyzer = Analyzer(llm_adapter=llm, inspector=inspector)

    _record_event(state, "analyzer", "running", {"query": query})
    analysis = await analyzer.analyze(query, project_path_override=project_path, budget=budget)

    state["analysis"] = analysis.model_dump()
    state["budget"] = budget.to_dict()
    _record_event(state, "analyzer", "completed", {"intent": analysis.intent.value, "goal": analysis.goal})
    return state


async def memory_retriever_node(state: RavenState) -> RavenState:
    query = state.get("query", "")
    analysis_dict = state.get("analysis", {})
    analysis = AnalysisResult.model_validate(analysis_dict) if analysis_dict else None

    project_name = None
    if analysis and analysis.project_inspection and analysis.project_inspection.root_path:
        project_name = analysis.project_inspection.root_path.split("\\")[-1].split("/")[-1]

    _record_event(state, "memory_retriever", "running", {"project": project_name})
    memory = LayeredMemorySystem()
    context = memory.retrieve_context_for_run(query, project_name=project_name)

    state["memory_context"] = context
    _record_event(state, "memory_retriever", "completed", {"context_bytes": len(context)})
    return state


async def planner_node(state: RavenState) -> RavenState:
    query = state.get("query", "")
    analysis_dict = state.get("analysis", {})
    analysis = AnalysisResult.model_validate(analysis_dict)
    memory_context = state.get("memory_context", "")
    profile = state.get("profile", "standard")
    budget_dict = state.get("budget", {})
    budget = RunBudget.from_dict(budget_dict) if budget_dict else RunBudget()

    llm = get_llm_adapter()
    planner = Planner(llm_adapter=llm)

    _record_event(state, "planner", "running", {"profile": profile})
    plan = await planner.plan(
        query=query,
        analysis=analysis,
        memory_context=memory_context,
        profile=profile,
        budget=budget,
    )

    state["plan"] = plan.model_dump()
    state["is_plan_approved"] = plan.is_approved
    state["budget"] = budget.to_dict()
    _record_event(
        state,
        "planner",
        "completed",
        {"objectives_count": len(plan.objectives), "approved": plan.is_approved},
    )
    return state


async def source_tunnels_node(state: RavenState) -> RavenState:
    query = state.get("query", "")
    plan_dict = state.get("plan", {})
    plan = ResearchPlan.model_validate(plan_dict)
    analysis_dict = state.get("analysis", {})
    analysis = AnalysisResult.model_validate(analysis_dict) if analysis_dict else None
    inspection = analysis.project_inspection if analysis else None

    budget_dict = state.get("budget", {})
    budget = RunBudget.from_dict(budget_dict) if budget_dict else RunBudget()

    dispatcher = TunnelDispatcher(inspection=inspection)

    _record_event(
        state,
        "source_tunnels",
        "running",
        {"objectives": [o.id for o in plan.objectives]},
    )

    verified_evidence, tunnel_results = await dispatcher.execute_plan(
        plan.objectives,
        default_query=query,
        budget=budget,
    )

    # Accumulate evidence in state
    existing_evidence = [EvidenceItem.model_validate(e) for e in state.get("evidence", [])]
    existing_hashes = {e.content_hash for e in existing_evidence}

    for item in verified_evidence:
        if item.content_hash not in existing_hashes:
            existing_evidence.append(item)
            existing_hashes.add(item.content_hash)

    state["evidence"] = [e.model_dump() for e in existing_evidence]
    state["tunnel_results"] = [tr.model_dump() for tr in tunnel_results]
    state["budget"] = budget.to_dict()

    _record_event(
        state,
        "source_tunnels",
        "completed",
        {
            "new_evidence_count": len(verified_evidence),
            "total_evidence_count": len(existing_evidence),
            "tunnels_status": {tr.tunnel_name: tr.status for tr in tunnel_results},
        },
    )
    return state


async def relevance_filter_node(state: RavenState) -> RavenState:
    query = state.get("query", "")
    analysis_dict = state.get("analysis", {})
    analysis = AnalysisResult.model_validate(analysis_dict) if analysis_dict else None
    plan_dict = state.get("plan", {})
    plan = ResearchPlan.model_validate(plan_dict) if plan_dict else None
    evidence_dicts = state.get("evidence", [])
    evidence = [EvidenceItem.model_validate(e) for e in evidence_dicts]

    _record_event(state, "relevance_filter", "running", {"total_evidence_count": len(evidence)})

    rf = RelevanceFilter()
    filtered_evidence = rf.filter_evidence(evidence, analysis=analysis, plan=plan, query=query)

    state["evidence"] = [e.model_dump() for e in filtered_evidence]

    accepted_count = sum(1 for e in filtered_evidence if e.verification_state != VerificationState.REJECTED)
    rejected_count = len(filtered_evidence) - accepted_count

    _record_event(
        state,
        "relevance_filter",
        "completed",
        {"accepted_count": accepted_count, "rejected_count": rejected_count},
    )
    return state


async def critic_node(state: RavenState) -> RavenState:
    plan_dict = state.get("plan", {})
    plan = ResearchPlan.model_validate(plan_dict)
    evidence_dicts = state.get("evidence", [])
    evidence = [
        EvidenceItem.model_validate(e)
        for e in evidence_dicts
        if e.get("verification_state") != VerificationState.REJECTED.value
    ]
    round_count = state.get("round_count", 1)
    max_rounds = state.get("max_rounds", 3)

    budget_dict = state.get("budget", {})
    budget = RunBudget.from_dict(budget_dict) if budget_dict else RunBudget()

    llm = get_llm_adapter()
    critic = Critic(llm_adapter=llm)

    _record_event(state, "critic", "running", {"round": round_count, "max_rounds": max_rounds})
    critique_report = await critic.critique(
        plan=plan,
        evidence=evidence,
        round_number=round_count,
        max_rounds=max_rounds,
        budget=budget,
    )

    state["critique"] = critique_report.model_dump()
    state["budget"] = budget.to_dict()
    state["stop_reason"] = critique_report.reason

    _record_event(
        state,
        "critic",
        "completed",
        {
            "coverage_score": critique_report.coverage_score,
            "should_continue": critique_report.should_continue,
            "unresolved_gaps": len(critique_report.unresolved_gaps),
            "reason": critique_report.reason,
        },
    )
    return state


async def gap_planner_node(state: RavenState) -> RavenState:
    plan_dict = state.get("plan", {})
    plan = ResearchPlan.model_validate(plan_dict)
    critique_dict = state.get("critique", {})
    critique = CritiqueReport.model_validate(critique_dict)

    gap_objectives = Critic.create_gap_objectives(plan, critique)
    plan.objectives = gap_objectives
    plan.revision_count += 1

    current_round = state.get("round_count", 1)
    state["round_count"] = current_round + 1
    state["plan"] = plan.model_dump()

    _record_event(
        state,
        "gap_planner",
        "completed",
        {"new_round": state["round_count"], "gap_objectives_count": len(gap_objectives)},
    )
    return state


async def answer_drafter_node(state: RavenState) -> RavenState:
    query = state.get("query", "")
    analysis_dict = state.get("analysis", {})
    analysis = AnalysisResult.model_validate(analysis_dict) if analysis_dict else None
    evidence_dicts = state.get("evidence", [])
    evidence = [
        EvidenceItem.model_validate(e)
        for e in evidence_dicts
        if e.get("verification_state") != VerificationState.REJECTED.value
    ]
    critique_dict = state.get("critique", {})
    critique = CritiqueReport.model_validate(critique_dict) if critique_dict else None

    budget_dict = state.get("budget", {})
    budget = RunBudget.from_dict(budget_dict) if budget_dict else RunBudget()

    previous_answer_dict = state.get("draft_answer")
    previous_answer = DraftAnswer.model_validate(previous_answer_dict) if previous_answer_dict else None
    self_check_verdict_dict = state.get("self_check_verdict")
    self_check_verdict = SelfCheckVerdict.model_validate(self_check_verdict_dict) if self_check_verdict_dict else None

    llm = get_llm_adapter()
    drafter = AnswerDrafter(llm_adapter=llm)

    _record_event(
        state,
        "answer_drafter",
        "running",
        {"is_revision": bool(self_check_verdict and not self_check_verdict.passed)},
    )

    draft_answer = await drafter.draft(
        query=query,
        evidence=evidence,
        analysis=analysis,
        critique=critique,
        previous_answer=previous_answer,
        self_check_verdict=self_check_verdict,
        budget=budget,
    )

    state["draft_answer"] = draft_answer.model_dump()
    state["budget"] = budget.to_dict()

    _record_event(
        state,
        "answer_drafter",
        "completed",
        {
            "sentence_count": len(draft_answer.sentences),
            "citations_count": len(draft_answer.cited_evidence_ids),
            "is_hedged": draft_answer.is_hedged_fallback,
        },
    )
    return state


async def self_check_node(state: RavenState) -> RavenState:
    draft_dict = state.get("draft_answer", {})
    draft = DraftAnswer.model_validate(draft_dict) if draft_dict else None
    evidence_dicts = state.get("evidence", [])
    evidence = [
        EvidenceItem.model_validate(e)
        for e in evidence_dicts
        if e.get("verification_state") != VerificationState.REJECTED.value
    ]

    budget_dict = state.get("budget", {})
    budget = RunBudget.from_dict(budget_dict) if budget_dict else RunBudget()

    self_check_rounds = state.get("self_check_rounds", 0) + 1
    state["self_check_rounds"] = self_check_rounds

    settings = get_settings()
    max_rounds = settings.max_self_check_rounds

    _record_event(
        state,
        "self_check",
        "running",
        {"round": self_check_rounds, "max_rounds": max_rounds},
    )

    if not draft:
        verdict = SelfCheckVerdict(passed=True, reason="No draft answer to verify.")
        state["self_check_verdict"] = verdict.model_dump()
        return state

    llm = get_llm_adapter()
    checker = SelfChecker(llm_adapter=llm)

    verdict = await checker.verify(draft=draft, evidence=evidence, budget=budget)

    # If failed and reached max rounds, apply hedged fallback
    if not verdict.passed and self_check_rounds >= max_rounds:
        hedged = SelfChecker.make_hedged_fallback(draft, evidence)
        state["draft_answer"] = hedged.model_dump()
        verdict.reason += " (Max self-check rounds reached; applied hedged fallback answer.)"

    state["self_check_verdict"] = verdict.model_dump()
    state["budget"] = budget.to_dict()

    _record_event(
        state,
        "self_check",
        "completed",
        {
            "passed": verdict.passed,
            "round": self_check_rounds,
            "unsupported_claims": len(verdict.unsupported_claims),
            "contradicted_claims": len(verdict.contradicted_claims),
            "overclaiming": len(verdict.overclaiming),
        },
    )
    return state


async def synthesizer_node(state: RavenState) -> RavenState:
    query = state.get("query", "")
    analysis_dict = state.get("analysis", {})
    analysis = AnalysisResult.model_validate(analysis_dict) if analysis_dict else None
    plan_dict = state.get("plan", {})
    plan = ResearchPlan.model_validate(plan_dict)
    evidence_dicts = state.get("evidence", [])
    evidence = [
        EvidenceItem.model_validate(e)
        for e in evidence_dicts
        if e.get("verification_state") != VerificationState.REJECTED.value
    ]
    critique_dict = state.get("critique", {})
    critique = CritiqueReport.model_validate(critique_dict) if critique_dict else None
    tunnel_results = state.get("tunnel_results", [])

    failed_tunnels = [
        tr["tunnel_name"] for tr in tunnel_results if tr.get("status") in ("failed", "disabled")
    ]
    stop_reason = state.get("stop_reason", "Research successfully completed")

    budget_dict = state.get("budget", {})
    budget = RunBudget.from_dict(budget_dict) if budget_dict else RunBudget()

    llm = get_llm_adapter()
    synth = Synthesizer(llm_adapter=llm)

    _record_event(state, "synthesizer", "running", {"evidence_items": len(evidence)})
    report = await synth.synthesize(
        query=query,
        plan=plan,
        evidence=evidence,
        analysis=analysis,
        critique=critique,
        failed_tunnels=failed_tunnels,
        stop_reason=stop_reason,
        budget=budget,
    )

    # If answer_first produced a verified draft answer, synchronize direct_answer
    draft_dict = state.get("draft_answer")
    if draft_dict and draft_dict.get("text"):
        report.direct_answer = draft_dict["text"]
        report.formatted_markdown = synth._format_markdown(report, evidence, analysis=analysis)

    state["final_report"] = report.model_dump()
    state["markdown_output"] = report.formatted_markdown
    state["budget"] = budget.to_dict()

    _record_event(
        state,
        "synthesizer",
        "completed",
        {"report_title": report.title, "facts_count": len(report.sourced_facts)},
    )
    return state


async def memory_updater_node(state: RavenState) -> RavenState:
    job_id = state.get("job_id", "unknown")
    query = state.get("query", "")
    final_report_dict = state.get("final_report", {})
    summary = final_report_dict.get("executive_summary", "")

    memory = LayeredMemorySystem()
    memory.record_run(run_id=job_id, query=query, summary=summary, status="completed")

    # Evaluate run quality metrics & record to state
    try:
        from src.evaluation.evaluator import RavenEvaluator
        evaluator = RavenEvaluator()
        plan = ResearchPlan.model_validate(state.get("plan", {}))
        evidence = [EvidenceItem.model_validate(e) for e in state.get("evidence", [])]
        report = FinalRavenReport.model_validate(final_report_dict)
        budget_dict = state.get("budget", {})
        budget = RunBudget.from_dict(budget_dict)

        metrics = evaluator.evaluate(
            run_id=job_id,
            query=query,
            plan=plan,
            evidence=evidence,
            report=report,
            round_count=state.get("round_count", 1),
            llm_calls=budget.calls_used,
            duration=budget.elapsed_time(),
        )
        state["metrics"] = metrics.model_dump()

        # Evaluate direct answer quality metrics
        draft_dict = state.get("draft_answer")
        draft_obj = DraftAnswer.model_validate(draft_dict) if draft_dict else None
        verdict_dict = state.get("self_check_verdict")
        verdict_obj = SelfCheckVerdict.model_validate(verdict_dict) if verdict_dict else None
        answer_metrics = evaluate_answer_quality(draft_obj, evidence, verdict_obj)
        state["answer_eval"] = answer_metrics.model_dump()
        if "metrics" in state and isinstance(state["metrics"], dict):
            state["metrics"]["answer_quality_score"] = answer_metrics.overall_quality_score
    except Exception as e:
        logger.warning("graph.evaluation_failed", error=str(e))

    _record_event(state, "finalizer", "completed", {"saved_to_memory": True})
    return state


def _critic_router(state: RavenState) -> str:
    settings = get_settings()
    critique_dict = state.get("critique", {})
    critique = CritiqueReport.model_validate(critique_dict) if critique_dict else None
    round_count = state.get("round_count", 1)
    max_rounds = state.get("max_rounds", 3)

    if critique and critique.should_continue and round_count < max_rounds:
        return "gap_planner"
    if settings.answer_first:
        return "answer_drafter"
    return "synthesizer"


def _self_check_router(state: RavenState) -> str:
    settings = get_settings()
    if not settings.self_check:
        return "synthesizer"

    verdict_dict = state.get("self_check_verdict", {})
    verdict = SelfCheckVerdict.model_validate(verdict_dict) if verdict_dict else None
    rounds = state.get("self_check_rounds", 0)
    max_rounds = settings.max_self_check_rounds

    if verdict and not verdict.passed and rounds < max_rounds:
        return "answer_drafter"
    return "synthesizer"


# ── Build Master RAVEN Workflow Graph ──────────────────────────────

def build_raven_graph() -> Any:
    settings = get_settings()
    workflow = StateGraph(RavenState)

    workflow.add_node("analyzer", analyzer_node)
    workflow.add_node("memory_retriever", memory_retriever_node)
    workflow.add_node("planner", planner_node)
    workflow.add_node("source_tunnels", source_tunnels_node)
    if settings.relevance_filter:
        workflow.add_node("relevance_filter", relevance_filter_node)
    workflow.add_node("critic", critic_node)
    workflow.add_node("gap_planner", gap_planner_node)

    if settings.answer_first:
        workflow.add_node("answer_drafter", answer_drafter_node)
        if settings.self_check:
            workflow.add_node("self_check", self_check_node)

    workflow.add_node("synthesizer", synthesizer_node)
    workflow.add_node("memory_updater", memory_updater_node)

    # Define edges matching RAVEN architecture
    workflow.add_edge(START, "analyzer")
    workflow.add_edge("analyzer", "memory_retriever")
    workflow.add_edge("memory_retriever", "planner")
    workflow.add_edge("planner", "source_tunnels")

    if settings.relevance_filter:
        workflow.add_edge("source_tunnels", "relevance_filter")
        workflow.add_edge("relevance_filter", "critic")
    else:
        workflow.add_edge("source_tunnels", "critic")

    critic_branches = {"gap_planner": "gap_planner"}
    if settings.answer_first:
        critic_branches["answer_drafter"] = "answer_drafter"
    else:
        critic_branches["synthesizer"] = "synthesizer"

    workflow.add_conditional_edges("critic", _critic_router, critic_branches)
    workflow.add_edge("gap_planner", "source_tunnels")

    if settings.answer_first:
        if settings.self_check:
            workflow.add_edge("answer_drafter", "self_check")
            workflow.add_conditional_edges(
                "self_check",
                _self_check_router,
                {
                    "answer_drafter": "answer_drafter",
                    "synthesizer": "synthesizer",
                },
            )
        else:
            workflow.add_edge("answer_drafter", "synthesizer")

    workflow.add_edge("synthesizer", "memory_updater")
    workflow.add_edge("memory_updater", END)

    return workflow.compile()
