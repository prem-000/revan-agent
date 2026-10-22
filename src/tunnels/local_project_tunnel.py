from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Tuple
import structlog

from src.models.raven import EvidenceItem, ProjectInspection, SourceTunnelResult
from src.tunnels.base import BaseTunnel

logger = structlog.get_logger(__name__)


class LocalProjectTunnel(BaseTunnel):
    """Read-only local project files tunnel extracting safe contextual excerpts."""

    def __init__(self, inspection: Optional[ProjectInspection] = None) -> None:
        super().__init__(name="local_project")
        self.inspection = inspection

    def is_enabled(self) -> Tuple[bool, str]:
        if self.inspection and self.inspection.is_valid and self.inspection.root_path:
            return True, f"Available (Inspecting {self.inspection.root_path})"
        return False, "Disabled (No local project directory attached to run)"

    async def fetch(self, query: str, objective_id: str, limit: int = 5) -> SourceTunnelResult:
        enabled, reason = self.is_enabled()
        if not enabled:
            return SourceTunnelResult(
                tunnel_name="local_project",
                status="disabled",
                reason=reason,
            )

        items: List[EvidenceItem] = []
        root = Path(self.inspection.root_path)

        # 1. Project Manifest / Structure Overview
        structure_excerpt = (
            f"Detected Stack: {', '.join(self.inspection.detected_stack)}\n"
            f"Structure: {self.inspection.structure_summary}\n"
            f"Known Manifests: {', '.join(self.inspection.manifest_files)}\n"
            f"Identified Risks/Gaps: {'; '.join(self.inspection.risks_and_gaps) or 'None'}"
        )
        items.append(
            EvidenceItem(
                evidence_id="",
                source_type="local_project",
                canonical_url=str(root / "project_structure"),
                title=f"Project Profile ({root.name})",
                publisher_or_repo="Local Filesystem",
                excerpt=structure_excerpt,
                objective_id=objective_id,
            )
        )

        # 2. Key manifest file excerpts (e.g., package.json, requirements.txt, README.md)
        for manifest_rel in self.inspection.manifest_files[:3]:
            manifest_path = root / manifest_rel
            if manifest_path.exists() and manifest_path.is_file():
                try:
                    content = manifest_path.read_text(encoding="utf-8", errors="ignore")[:1500]
                    items.append(
                        EvidenceItem(
                            evidence_id="",
                            source_type="local_project",
                            canonical_url=str(manifest_path),
                            title=f"Manifest: {manifest_rel}",
                            publisher_or_repo=f"Local Project ({root.name})",
                            excerpt=content,
                            objective_id=objective_id,
                        )
                    )
                except Exception:
                    pass

        return SourceTunnelResult(
            tunnel_name="local_project",
            status="completed",
            items=items[:limit],
        )
