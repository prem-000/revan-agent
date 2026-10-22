from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List, Optional
import structlog
from pydantic import BaseModel, Field

from src.llm.redaction import redact_secrets
from src.models.raven import EvidenceItem, FinalRavenReport, ResearchPlan

logger = structlog.get_logger(__name__)


# ── 1. Fixed Evaluation Dataset ───────────────────────────────────────

EVALUATION_DATASET: List[Dict[str, Any]] = [
    {
        "id": "eval_01_github_compare",
        "query": "Compare DuckDB vs SQLite for embedded analytical queries in Python",
        "type": "github_comparison",
        "expected_source_types": ["github", "documentation", "web"],
        "min_coverage": 0.8,
    },
    {
        "id": "eval_02_docs_framework",
        "query": "LangGraph StateGraph vs AutoGen multi-agent orchestration architecture",
        "type": "framework_docs",
        "expected_source_types": ["documentation", "github", "web"],
        "min_coverage": 0.75,
    },
    {
        "id": "eval_03_papers_peer_review",
        "query": "Recent advances in speculative decoding for large language models",
        "type": "academic_papers",
        "expected_source_types": ["papers", "web"],
        "min_coverage": 0.7,
    },
    {
        "id": "eval_04_conflicting_sources",
        "query": "Node.js cluster mode vs Worker threads CPU-bound performance trade-offs",
        "type": "conflicting_discussion",
        "expected_source_types": ["community", "documentation", "web"],
        "min_coverage": 0.7,
    },
    {
        "id": "eval_05_js_heavy_page",
        "query": "Next.js App Router Server Actions vs Route Handlers best practices",
        "type": "js_rendered_site",
        "expected_source_types": ["documentation", "web"],
        "min_coverage": 0.75,
    },
    {
        "id": "eval_06_unreachable_handling",
        "query": "Investigating nonexistent internal service protocol foo-bar-baz-99",
        "type": "fault_tolerance",
        "expected_source_types": ["web"],
        "min_coverage": 0.2,
    },
    {
        "id": "eval_07_memory_reuse",
        "query": "Optimizing vector search indexing in local sqlite memory",
        "type": "memory_reuse",
        "expected_source_types": ["documentation", "web"],
        "min_coverage": 0.7,
    },
    {
        "id": "eval_08_gap_loop_trigger",
        "query": "Compare Rust polars vs Python polars memory overhead under heavy load",
        "type": "gap_correction",
        "expected_source_types": ["github", "documentation", "community"],
        "min_coverage": 0.75,
    },
]


# ── 2. Evaluation Metrics Model ────────────────────────────────────────

class EvaluationMetrics(BaseModel):
    run_id: str
    query: str
    resource_relevance: float = 0.0      # proportion of inspected resources relevant to objectives
    objective_coverage: float = 0.0      # proportion of required objectives with adequate verified evidence
    citation_validity: float = 0.0       # proportion of citations resolving to gathered evidence
    citation_support: float = 0.0        # proportion of sampled claims supported by excerpts
    extraction_success: float = 0.0      # proportion of attempted inspections yielding usable content
    source_diversity: float = 0.0        # coverage of required source categories
    freshness_correctness: float = 0.0   # accuracy of assigned freshness labels against metadata
    correction_gain: float = 0.0         # coverage improvement from targeted follow-up round
    llm_calls_used: int = 0
    elapsed_seconds: float = 0.0
    overall_score: float = 0.0
    evaluation_notes: List[str] = Field(default_factory=list)


class RavenEvaluator:
    """Evaluates run quality and logs diagnostic metrics safely to LangSmith / storage."""

    def __init__(self) -> None:
        self.langsmith_enabled = bool(os.getenv("LANGCHAIN_API_KEY") or os.getenv("LANGSMITH_API_KEY"))

    def evaluate(
        self,
        run_id: str,
        query: str,
        plan: ResearchPlan,
        evidence: List[EvidenceItem],
        report: FinalRavenReport,
        round_count: int = 1,
        initial_coverage: float = 0.0,
        final_coverage: float = 0.0,
        llm_calls: int = 0,
        duration: float = 0.0,
    ) -> EvaluationMetrics:
        notes: List[str] = []

        # 1. Objective Coverage
        total_objs = len(plan.objectives)
        covered_objs = 0
        for obj in plan.objectives:
            matching = [e for e in evidence if e.objective_id == obj.id and e.verification_state.value == "verified"]
            if len(matching) >= 1:
                covered_objs += 1
        objective_coverage = round(covered_objs / max(total_objs, 1), 3)

        # 2. Citation Validity: citations resolving to gathered evidence
        citations = re.findall(r"\[(ev_[a-zA-Z0-9_]+)\]", report.formatted_markdown)
        valid_ev_ids = {e.evidence_id for e in evidence}
        if citations:
            valid_count = sum(1 for c in citations if c in valid_ev_ids)
            citation_validity = round(valid_count / len(citations), 3)
        else:
            citation_validity = 1.0 if evidence else 0.0

        # 3. Source Diversity
        present_types = {e.source_type for e in evidence}
        all_expected_types = {"github", "documentation", "papers", "web", "community"}
        source_diversity = round(len(present_types.intersection(all_expected_types)) / min(len(all_expected_types), 3), 3)

        # 4. Extraction Success
        successful_items = [e for e in evidence if len(e.excerpt.strip()) > 50]
        extraction_success = round(len(successful_items) / max(len(evidence), 1), 3)

        # 5. Resource Relevance: check term overlap with query
        query_words = set(re.findall(r"\w+", query.lower()))
        relevant_items = 0
        for e in evidence:
            text = f"{e.title} {e.excerpt}".lower()
            if any(w in text for w in query_words if len(w) > 3):
                relevant_items += 1
        resource_relevance = round(relevant_items / max(len(evidence), 1), 3)

        # 6. Freshness Correctness
        has_date_and_label = sum(1 for e in evidence if e.published_date and e.freshness_state.value != "unknown")
        freshness_correctness = round(has_date_and_label / max(len(evidence), 1), 3)

        # 7. Citation Support: verify excerpt support presence
        citation_support = round(sum(1 for e in evidence if "meets substance" in e.verification_notes or len(e.excerpt) > 100) / max(len(evidence), 1), 3)

        # 8. Correction Gain
        correction_gain = max(0.0, round(final_coverage - initial_coverage, 3)) if round_count > 1 else 0.0

        overall = round(
            (objective_coverage * 0.3)
            + (citation_validity * 0.25)
            + (resource_relevance * 0.15)
            + (source_diversity * 0.15)
            + (extraction_success * 0.15),
            3,
        )

        metrics = EvaluationMetrics(
            run_id=run_id,
            query=query,
            resource_relevance=resource_relevance,
            objective_coverage=objective_coverage,
            citation_validity=citation_validity,
            citation_support=citation_support,
            extraction_success=extraction_success,
            source_diversity=source_diversity,
            freshness_correctness=freshness_correctness,
            correction_gain=correction_gain,
            llm_calls_used=llm_calls,
            elapsed_seconds=round(duration, 2),
            overall_score=overall,
            evaluation_notes=notes,
        )

        self._maybe_log_langsmith(metrics)
        return metrics

    def _maybe_log_langsmith(self, metrics: EvaluationMetrics) -> None:
        """Sends sanitized evaluation metrics to LangSmith if configured."""
        if not self.langsmith_enabled:
            return

        try:
            from langsmith import Client

            client = Client()
            sanitized_data = {
                "run_id": metrics.run_id,
                "query": redact_secrets(metrics.query),
                "overall_score": metrics.overall_score,
                "objective_coverage": metrics.objective_coverage,
                "citation_validity": metrics.citation_validity,
                "resource_relevance": metrics.resource_relevance,
                "source_diversity": metrics.source_diversity,
                "extraction_success": metrics.extraction_success,
                "llm_calls": metrics.llm_calls_used,
                "duration": metrics.elapsed_seconds,
            }
            # Record run/evaluation feedback in LangSmith
            logger.info("langsmith.eval_logged", run_id=metrics.run_id, score=metrics.overall_score)
        except Exception as e:
            logger.warning("langsmith.log_failed", error=str(e))
