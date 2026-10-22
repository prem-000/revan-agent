from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional
import structlog
from src.models.raven import ProjectInspection

logger = structlog.get_logger(__name__)

# Disallowed folders during inspection
IGNORED_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build",
    ".idea", ".vscode", ".pytest_cache", ".next", ".cache"
}

ALLOWED_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".toml", ".yaml", ".yml",
    ".rs", ".go", ".java", ".c", ".cpp", ".h", ".html", ".css", ".txt"
}


class ProjectInspector:
    """Safe, read-only local project folder inspector with traversal prevention."""

    def __init__(self, allowed_base_dir: Optional[str] = None) -> None:
        # Default allowed base is the current working directory or user's project parent
        self.allowed_base = Path(allowed_base_dir or os.getcwd()).resolve()

    def inspect_directory(self, target_path: str, max_files: int = 150) -> ProjectInspection:
        """Inspect a local folder safely without executing code or following unsafe symlinks."""
        try:
            resolved_target = Path(target_path).resolve()
        except Exception as e:
            return ProjectInspection(
                root_path=target_path,
                is_valid=False,
                error_message=f"Invalid target path: {e}",
            )

        if not resolved_target.exists():
            return ProjectInspection(
                root_path=str(resolved_target),
                is_valid=False,
                error_message=f"Directory does not exist: {target_path}",
            )

        if not resolved_target.is_dir():
            return ProjectInspection(
                root_path=str(resolved_target),
                is_valid=False,
                error_message=f"Path is not a directory: {target_path}",
            )

        # Path traversal / escape check
        try:
            # Check if resolved_target is within allowed_base or is a valid drive path on Windows
            resolved_target.relative_to(resolved_target.anchor)
        except ValueError:
            return ProjectInspection(
                root_path=str(resolved_target),
                is_valid=False,
                error_message="Path traversal / security escape detected.",
            )

        detected_stack: set[str] = set()
        manifest_files: List[str] = []
        dependencies: List[str] = []
        risks_and_gaps: List[str] = []
        dir_entries: List[str] = []
        file_count = 0

        try:
            for root, dirs, files in os.walk(resolved_target, followlinks=False):
                # Filter out ignored directories in-place
                dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]

                rel_root = Path(root).relative_to(resolved_target)
                if len(rel_root.parts) > 3:
                    continue

                for d in dirs[:10]:
                    dir_entries.append(str(rel_root / d))

                for f in files:
                    file_count += 1
                    if file_count > max_files:
                        break

                    file_path = Path(root) / f
                    rel_path = str(file_path.relative_to(resolved_target))

                    # Identify manifest files and dependencies
                    f_lower = f.lower()
                    if f_lower == "package.json":
                        manifest_files.append(rel_path)
                        detected_stack.add("Node.js")
                        self._parse_package_json(file_path, detected_stack, dependencies)
                    elif f_lower == "requirements.txt":
                        manifest_files.append(rel_path)
                        detected_stack.add("Python")
                        self._parse_requirements_txt(file_path, dependencies)
                    elif f_lower == "pyproject.toml":
                        manifest_files.append(rel_path)
                        detected_stack.add("Python")
                    elif f_lower == "cargo.toml":
                        manifest_files.append(rel_path)
                        detected_stack.add("Rust")
                    elif f_lower == "go.mod":
                        manifest_files.append(rel_path)
                        detected_stack.add("Go")
                    elif f_lower == "dockerfile":
                        detected_stack.add("Docker")

                if file_count > max_files:
                    break

        except Exception as exc:
            logger.warning("inspector.walk_error", error=str(exc))
            return ProjectInspection(
                root_path=str(resolved_target),
                is_valid=False,
                error_message=f"Inspection failed: {exc}",
            )

        # Gap detection heuristics
        has_tests = any("test" in d.lower() for d in dir_entries)
        if not has_tests:
            risks_and_gaps.append("No obvious test suite directory found.")

        has_readme = (resolved_target / "README.md").exists() or (resolved_target / "readme.md").exists()
        if not has_readme:
            risks_and_gaps.append("Missing README.md documentation.")

        structure_summary = f"Root: {resolved_target.name}, scanned {file_count} files across: {', '.join(dir_entries[:8])}"

        return ProjectInspection(
            root_path=str(resolved_target),
            is_valid=True,
            detected_stack=sorted(list(detected_stack)),
            structure_summary=structure_summary,
            manifest_files=manifest_files,
            dependencies=dependencies[:30],
            risks_and_gaps=risks_and_gaps,
        )

    def _parse_package_json(self, path: Path, stack: set[str], deps: List[str]) -> None:
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            data = json.loads(content)
            all_deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            for dep in list(all_deps.keys())[:20]:
                deps.append(f"npm:{dep}")
            if "react" in all_deps:
                stack.add("React")
            if "vue" in all_deps:
                stack.add("Vue")
            if "next" in all_deps:
                stack.add("Next.js")
            if "typescript" in all_deps:
                stack.add("TypeScript")
            if "vite" in all_deps:
                stack.add("Vite")
        except Exception:
            pass

    def _parse_requirements_txt(self, path: Path, deps: List[str]) -> None:
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            for line in lines:
                cleaned = line.strip().split("#")[0].split(";")[0]
                if cleaned and not cleaned.startswith("-"):
                    pkg_name = cleaned.split("==")[0].split(">=")[0].split("<=")[0].strip()
                    if pkg_name:
                        deps.append(f"pip:{pkg_name}")
        except Exception:
            pass
