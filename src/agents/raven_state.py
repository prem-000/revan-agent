from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict


class RavenWorkflowEvent(TypedDict):
    seq: int
    step: str
    status: str  # "started", "running", "completed", "failed", "disabled", "skipped"
    details: Dict[str, Any]
    timestamp: float


class RavenState(TypedDict, total=False):
    """Strongly-typed execution state for RAVEN's LangGraph workflow."""
    job_id: str
    query: str
    project_path: Optional[str]
    profile: str  # fast, standard, deep

    # Phase 2 artifacts
    analysis: Dict[str, Any]
    memory_context: str
    plan: Dict[str, Any]
    is_plan_approved: bool

    # Phase 3 artifacts
    evidence: List[Dict[str, Any]]
    tunnel_results: List[Dict[str, Any]]

    # Phase 4 artifacts
    critique: Dict[str, Any]
    round_count: int
    max_rounds: int
    stop_reason: str

    # Synthesis artifacts
    final_report: Dict[str, Any]
    markdown_output: str

    # Shared Resource Budget
    budget: Dict[str, Any]

    # Event stream journal for clients and Mermaid rendering
    events: List[RavenWorkflowEvent]
