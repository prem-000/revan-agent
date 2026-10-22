from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional
import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from src.llm.groq_adapter import LLMProviderAdapter
from src.llm.budget import RunBudget
from src.memory.project_inspector import ProjectInspector
from src.models.raven import AnalysisResult, IntentType, ProjectInspection

logger = structlog.get_logger(__name__)

ANALYZER_SYSTEM_PROMPT = """You are RAVEN's Analyzer. Your job is to analyze the user request and classify its intent and constraints.
You must be factual, precise, and never invent missing facts.
Classify the intent as either "idea" (technical question, idea, comparison, architecture, research topic) or "project" (involves an existing local codebase/folder).
Extract:
- goal: concise statement of what the user is trying to accomplish
- constraints: explicit requirements or limitations
- assumptions: necessary assumptions made to proceed
- unknowns: specific questions or facts that are currently unknown and need investigation
"""


class Analyzer:
    """Classifies user intent and extracts objectives and project context."""

    def __init__(self, llm_adapter: LLMProviderAdapter, inspector: Optional[ProjectInspector] = None) -> None:
        self.llm = llm_adapter
        self.inspector = inspector or ProjectInspector()

    def _detect_local_path(self, query: str) -> Optional[str]:
        """Detect if the query contains a local directory path."""
        # Check explicit tokens or paths
        tokens = query.strip().split()
        for token in tokens:
            cleaned = token.strip("\"'.,;:")
            # Path patterns: starts with ./ or ../ or C:\ or /
            if (
                cleaned.startswith("./")
                or cleaned.startswith("../")
                or cleaned.startswith("/")
                or (len(cleaned) >= 3 and cleaned[1:3] in (":\\", ":/"))
            ):
                p = Path(cleaned)
                if p.exists() and p.is_dir():
                    return str(p.resolve())

        # Also check if the entire query is a directory path
        try:
            p_full = Path(query.strip().strip("\"'")).resolve()
            if p_full.exists() and p_full.is_dir():
                return str(p_full)
        except Exception:
            pass
        return None

    def _disambiguate_query(self, query: str) -> tuple[str | None, dict[str, str], bool]:
        """Detect ambiguous terms (e.g. DSA), anchor domain, and classify concept queries."""
        q_lower = query.lower()
        terms_map: dict[str, str] = {}
        domain_anchor: str | None = None

        # 1. Ambiguous acronym resolution
        words = re.findall(r"\b[A-Za-z0-9_-]+\b", query)
        for w in words:
            upper_w = w.upper()
            if upper_w == "DSA":
                # Check context
                if any(k in q_lower for k in ["linked", "list", "tree", "array", "stack", "algorithm", "data structure", "queue", "graph", "sort"]):
                    terms_map["DSA"] = "data structures and algorithms"
                    domain_anchor = "data structures and algorithms"
                elif any(k in q_lower for k in ["signature", "crypto", "security", "key", "certificate"]):
                    terms_map["DSA"] = "digital signature algorithm"
                    domain_anchor = "cryptography"
                else:
                    terms_map["DSA"] = "data structures and algorithms"
                    domain_anchor = "data structures and algorithms"
            elif upper_w == "RAG":
                terms_map["RAG"] = "retrieval augmented generation"
                domain_anchor = domain_anchor or "artificial intelligence"
            elif upper_w == "ML":
                terms_map["ML"] = "machine learning"
                domain_anchor = domain_anchor or "machine learning"

        # 2. Concept query detection
        concept_terms = [
            "linked list", "linked-list", "doubly linked", "singly linked",
            "binary tree", "hash table", "hash map", "trie", "binary search",
            "stack", "queue", "heap", "dijkstra", "quicksort", "mergesort",
            "dynamic programming", "recursion", "big o", "time complexity"
        ]
        is_concept = any(term in q_lower for term in concept_terms) or bool(
            re.match(r"^(what is|explain|how does|define)\b", q_lower)
        )
        # If explicitly asking for repository, library, or tools comparison, not pure concept
        if any(tool_w in q_lower for tool_w in ["github", "repo", "library", "framework", "comparison table", "tool", "package"]):
            is_concept = False

        if not domain_anchor and is_concept:
            domain_anchor = "data structures and algorithms"

        return domain_anchor, terms_map, is_concept

    async def analyze(
        self,
        query: str,
        project_path_override: Optional[str] = None,
        budget: Optional[RunBudget] = None,
    ) -> AnalysisResult:
        """Run analysis on the incoming query with deterministic disambiguation."""
        detected_path = project_path_override or self._detect_local_path(query)
        inspection: Optional[ProjectInspection] = None

        if detected_path:
            logger.info("analyzer.detected_project_folder", path=detected_path)
            inspection = self.inspector.inspect_directory(detected_path)

        domain_anchor, disambiguated_terms, is_concept = self._disambiguate_query(query)

        context_prompt = f"User Request: {query}\n"
        if domain_anchor:
            context_prompt += f"Disambiguated Technical Domain Anchor: {domain_anchor}\n"
        if disambiguated_terms:
            context_prompt += f"Resolved Terms: {disambiguated_terms}\n"

        if inspection and inspection.is_valid:
            context_prompt += (
                f"\nLocal Project Context Detected:\n"
                f"- Root: {inspection.root_path}\n"
                f"- Stack: {', '.join(inspection.detected_stack)}\n"
                f"- Structure: {inspection.structure_summary}\n"
                f"- Dependencies: {', '.join(inspection.dependencies[:15])}\n"
                f"- Identified Risks/Gaps: {', '.join(inspection.risks_and_gaps)}\n"
            )

        messages = [
            SystemMessage(content=ANALYZER_SYSTEM_PROMPT),
            HumanMessage(content=context_prompt),
        ]

        try:
            analysis = await self.llm.ainvoke_structured(
                messages,
                AnalysisResult,
                temperature=0.1,
                budget=budget,
            )
            # Override intent to PROJECT if a valid local directory was detected
            if inspection and inspection.is_valid:
                analysis.intent = IntentType.PROJECT
                analysis.project_inspection = inspection
            analysis.domain_anchor = domain_anchor or analysis.domain_anchor
            analysis.disambiguated_terms = disambiguated_terms or analysis.disambiguated_terms
            analysis.is_concept_query = is_concept
            return analysis
        except Exception as e:
            logger.warning("analyzer.llm_fallback", error=str(e))
            # Graceful rule-based fallback if LLM is offline
            intent = IntentType.PROJECT if (inspection and inspection.is_valid) else IntentType.IDEA
            return AnalysisResult(
                intent=intent,
                goal=query[:200],
                constraints=["Adhere to user parameters"],
                assumptions=["Proceed with standard multi-source research"],
                unknowns=["Evidence to be gathered from assigned source tunnels"],
                project_inspection=inspection,
                domain_anchor=domain_anchor,
                disambiguated_terms=disambiguated_terms,
                is_concept_query=is_concept,
            )
