from __future__ import annotations

import asyncio
from typing import Dict, List, Optional
import structlog

from src.llm.budget import RunBudget
from src.models.raven import (
    EvidenceItem,
    ProjectInspection,
    ResearchObjective,
    SourceTunnelResult,
)
from src.tunnels.base import BaseTunnel
from src.tunnels.community_tunnel import CommunityTunnel
from src.tunnels.docs_tunnel import DocsTunnel
from src.tunnels.github_tunnel import GitHubTunnel
from src.tunnels.local_project_tunnel import LocalProjectTunnel
from src.tunnels.normalizer import normalize_evidence_items
from src.tunnels.papers_tunnel import PapersTunnel
from src.tunnels.verifier import EvidenceVerifier
from src.tunnels.web_tunnel import WebTunnel

logger = structlog.get_logger(__name__)


class TunnelDispatcher:
    """Orchestrates concurrent execution of parallel source tunnels."""

    def __init__(self, inspection: Optional[ProjectInspection] = None) -> None:
        self.tunnels: Dict[str, BaseTunnel] = {
            "web": WebTunnel(),
            "papers": PapersTunnel(),
            "github": GitHubTunnel(),
            "documentation": DocsTunnel(),
            "community": CommunityTunnel(),
            "local_project": LocalProjectTunnel(inspection=inspection),
        }

    async def execute_objective(
        self,
        objective: ResearchObjective,
        query: str,
        limit_per_tunnel: int = 4,
    ) -> List[SourceTunnelResult]:
        """Dispatch assigned tunnels concurrently for a single objective with strict failure isolation."""
        task_tunnels: List[str] = []
        coros = []
        assigned = objective.assigned_tunnels or ["web", "papers"]

        for tunnel_name in assigned:
            tunnel = self.tunnels.get(tunnel_name)
            if not tunnel:
                continue

            task_tunnels.append(tunnel_name)
            enabled, reason = tunnel.is_enabled()
            if not enabled:
                async def _disabled(tname=tunnel_name, r=reason):
                    return SourceTunnelResult(
                        tunnel_name=tname,
                        status="disabled",
                        reason=r,
                    )
                coros.append(_disabled())
            else:
                coros.append(tunnel.fetch(query, objective.id, limit=limit_per_tunnel))

        raw_results = await asyncio.gather(*coros, return_exceptions=True)
        results: List[SourceTunnelResult] = []

        for tunnel_name, res in zip(task_tunnels, raw_results):
            if isinstance(res, asyncio.CancelledError):
                logger.warning("tunnel.cancelled", tunnel=tunnel_name, objective_id=objective.id)
                raise res
            elif isinstance(res, Exception):
                logger.error(
                    "tunnel.execution_exception",
                    tunnel=tunnel_name,
                    objective_id=objective.id,
                    error=str(res),
                    error_type=type(res).__name__,
                )
                results.append(
                    SourceTunnelResult(
                        tunnel_name=tunnel_name,
                        status="failed",
                        error=f"{type(res).__name__}: {res}",
                        reason=f"Tunnel execution failed: {type(res).__name__}",
                    )
                )
            elif isinstance(res, SourceTunnelResult):
                results.append(res)
            else:
                results.append(
                    SourceTunnelResult(
                        tunnel_name=tunnel_name,
                        status="failed",
                        error=f"Unexpected return type {type(res).__name__}",
                    )
                )
        return results

    async def execute_plan(
        self,
        objectives: List[ResearchObjective],
        default_query: str,
        budget: Optional[RunBudget] = None,
    ) -> tuple[List[EvidenceItem], List[SourceTunnelResult]]:
        """Run tunnels across all plan objectives concurrently, then normalize and verify."""
        all_tunnel_results: List[SourceTunnelResult] = []
        raw_evidence: List[EvidenceItem] = []

        tasks = []
        for obj in objectives:
            search_term = f"{default_query} {' '.join(obj.questions[:1])}"
            tasks.append(self.execute_objective(obj, search_term))

        raw_objective_results = await asyncio.gather(*tasks, return_exceptions=True)
        for idx, obj_res in enumerate(raw_objective_results):
            if isinstance(obj_res, asyncio.CancelledError):
                raise obj_res
            elif isinstance(obj_res, Exception):
                logger.error("objective.execution_exception", objective_idx=idx, error=str(obj_res))
                all_tunnel_results.append(
                    SourceTunnelResult(
                        tunnel_name="dispatcher",
                        status="failed",
                        error=str(obj_res),
                        reason=f"Objective task failed: {type(obj_res).__name__}",
                    )
                )
            elif isinstance(obj_res, list):
                for tr in obj_res:
                    if isinstance(tr, SourceTunnelResult):
                        all_tunnel_results.append(tr)
                        raw_evidence.extend(tr.items)

        # Normalize and Deduplicate
        normalized = normalize_evidence_items(raw_evidence)

        # Verify
        verified = EvidenceVerifier.verify_batch(normalized)

        logger.info(
            "tunnels.execution_complete",
            raw_count=len(raw_evidence),
            verified_count=len(verified),
            tunnels_run=len(all_tunnel_results),
        )

        return verified, all_tunnel_results
