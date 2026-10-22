from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import structlog
from pydantic import BaseModel, Field

from src.llm.redaction import redact_secrets

logger = structlog.get_logger(__name__)


class MemoryEntry(BaseModel):
    id: str
    category: str
    content: str
    created_at: float = Field(default_factory=time.time)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EvidenceCacheEntry(BaseModel):
    url_or_key: str
    content: str
    title: str = ""
    source_type: str = "web"
    cached_at: float = Field(default_factory=time.time)
    ttl_seconds: float = 86400.0  # 24 hours default

    def is_expired(self, now: Optional[float] = None) -> bool:
        current = now if now is not None else time.time()
        return (current - self.cached_at) > self.ttl_seconds


class LayeredMemorySystem:
    """Multi-layer safe memory store with SQLite FTS5 search, secret sanitization, and TTL cache."""

    def __init__(self, base_storage_dir: Optional[str] = None) -> None:
        self.base_dir = Path(base_storage_dir or "storage/memory").resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.episodic_file = self.base_dir / "episodic.json"
        self.project_file = self.base_dir / "project.json"
        self.decision_file = self.base_dir / "decisions.json"
        self.cache_file = self.base_dir / "evidence_cache.json"
        self.preference_file = self.base_dir / "preferences.json"
        self.proposals_file = self.base_dir / "proposals.json"
        self.failures_file = self.base_dir / "failures.json"
        self.db_path = self.base_dir / "raven_memory.db"
        self._init_sqlite()

    def _init_sqlite(self) -> None:
        """Initializes SQLite database with FTS5 for fast full-text memory retrieval."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.cursor()
                # Create episodic table and FTS index
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS episodic (
                        id TEXT PRIMARY KEY,
                        query TEXT,
                        summary TEXT,
                        status TEXT,
                        timestamp REAL
                    )
                """)
                cur.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS episodic_fts USING fts5(
                        id UNINDEXED,
                        query,
                        summary,
                        content=episodic,
                        content_rowid=rowid
                    )
                """)
                # Evidence cache table and FTS
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS evidence_cache (
                        url TEXT PRIMARY KEY,
                        title TEXT,
                        content TEXT,
                        source_type TEXT,
                        cached_at REAL,
                        ttl REAL
                    )
                """)
                cur.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS cache_fts USING fts5(
                        url UNINDEXED,
                        title,
                        content,
                        content=evidence_cache,
                        content_rowid=rowid
                    )
                """)
                # Improvement proposals and failures
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS improvement_proposals (
                        proposal_id TEXT PRIMARY KEY,
                        version TEXT,
                        description TEXT,
                        status TEXT,
                        evaluation_score REAL,
                        created_at REAL
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS failure_patterns (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        failure_type TEXT,
                        details TEXT,
                        run_id TEXT,
                        timestamp REAL
                    )
                """)
                conn.commit()
        except Exception as e:
            logger.warning("memory.sqlite_init_failed", error=str(e))

    def _load_json(self, path: Path) -> List[dict]:
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("memory.load_error", path=str(path), error=str(e))
            return []

    def _save_json(self, path: Path, data: Any) -> None:
        try:
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error("memory.save_error", path=str(path), error=str(e))

    def _sanitize(self, text: str) -> str:
        return redact_secrets(text)

    # ── 1. Episodic Memory (compact run summaries) ─────────────────────
    def record_run(self, run_id: str, query: str, summary: str, status: str) -> None:
        runs = self._load_json(self.episodic_file)
        sanitized_query = self._sanitize(query)
        sanitized_summary = self._sanitize(summary)
        entry = {
            "id": run_id,
            "query": sanitized_query,
            "summary": sanitized_summary,
            "status": status,
            "timestamp": time.time(),
        }
        # Deduplicate by run_id
        runs = [r for r in runs if r.get("id") != run_id]
        runs.insert(0, entry)
        self._save_json(self.episodic_file, runs[:50])

        # Write to SQLite
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    "INSERT OR REPLACE INTO episodic (id, query, summary, status, timestamp) VALUES (?, ?, ?, ?, ?)",
                    (run_id, sanitized_query, sanitized_summary, status, time.time()),
                )
                cur.execute(
                    "INSERT INTO episodic_fts(episodic_fts, rank) VALUES('rebuild', 1)"
                )
                conn.commit()
        except Exception as e:
            logger.warning("memory.sqlite_record_run_failed", error=str(e))

    def search_episodic(self, query: str, limit: int = 3) -> List[dict]:
        """Search episodic memory with SQLite FTS5 fallback to keyword match."""
        sanitized_q = self._sanitize(query)
        # Try FTS5 first
        try:
            clean_term = "".join(c for c in sanitized_q if c.isalnum() or c.isspace()).strip()
            if clean_term:
                fts_query = " OR ".join(clean_term.split()[:5])
                with sqlite3.connect(self.db_path) as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT id, query, summary, status, timestamp FROM episodic WHERE id IN (SELECT id FROM episodic_fts WHERE episodic_fts MATCH ? LIMIT ?)",
                        (fts_query, limit),
                    )
                    rows = cur.fetchall()
                    if rows:
                        return [
                            {"id": r[0], "query": r[1], "summary": r[2], "status": r[3], "timestamp": r[4]}
                            for r in rows
                        ]
        except Exception:
            pass

        # JSON keyword fallback
        runs = self._load_json(self.episodic_file)
        words = set(sanitized_q.lower().split())
        matched = []
        for r in runs:
            score = sum(1 for w in words if w in r.get("query", "").lower() or w in r.get("summary", "").lower())
            if score > 0:
                matched.append((score, r))
        matched.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in matched[:limit]]

    # ── 2. Project Memory ──────────────────────────────────────────────
    def save_project_profile(self, project_path: str, profile_data: dict) -> None:
        profiles = self._load_json(self.project_file)
        cleaned = {k: self._sanitize(str(v)) if isinstance(v, str) else v for k, v in profile_data.items()}
        cleaned["path"] = project_path
        cleaned["updated_at"] = time.time()
        profiles = [p for p in profiles if p.get("path") != project_path]
        profiles.append(cleaned)
        self._save_json(self.project_file, profiles)

    def get_project_profile(self, project_path: str) -> Optional[dict]:
        profiles = self._load_json(self.project_file)
        for p in profiles:
            if p.get("path") == project_path:
                return p
        return None

    # ── 3. Decision Memory ─────────────────────────────────────────────
    def record_decision(self, decision_id: str, title: str, rationale: str, approved: bool) -> None:
        decisions = self._load_json(self.decision_file)
        entry = {
            "id": decision_id,
            "title": self._sanitize(title),
            "rationale": self._sanitize(rationale),
            "approved": approved,
            "timestamp": time.time(),
        }
        decisions = [d for d in decisions if d.get("id") != decision_id]
        decisions.append(entry)
        self._save_json(self.decision_file, decisions)

    def get_approved_decisions(self) -> List[dict]:
        decisions = self._load_json(self.decision_file)
        return [d for d in decisions if d.get("approved")]

    # ── 4. Evidence Cache with TTL ─────────────────────────────────────
    def cache_evidence(self, url: str, content: str, title: str = "", source_type: str = "web", ttl: float = 86400.0) -> None:
        cache = self._load_json(self.cache_file)
        entry = {
            "url_or_key": url,
            "title": self._sanitize(title),
            "content": self._sanitize(content)[:3000],
            "source_type": source_type,
            "cached_at": time.time(),
            "ttl_seconds": ttl,
        }
        cache = [c for c in cache if c.get("url_or_key") != url]
        cache.append(entry)
        self._save_json(self.cache_file, cache[:200])

    def get_cached_evidence(self, url: str) -> Optional[dict]:
        cache = self._load_json(self.cache_file)
        now = time.time()
        for c in cache:
            if c.get("url_or_key") == url:
                cached_at = c.get("cached_at", 0)
                ttl = c.get("ttl_seconds", 86400.0)
                if (now - cached_at) <= ttl:
                    return c
        return None

    # ── 5. Preference Memory ───────────────────────────────────────────
    def set_preferences(self, preferences: dict) -> None:
        self._save_json(self.preference_file, preferences)

    def get_preferences(self) -> dict:
        return self._load_json(self.preference_file) or {
            "depth": "standard",
            "citation_format": "inline",
            "require_freshness": False,
        }

    # ── 6. Failures & Continuous Improvement Proposals ─────────────────
    def record_failure(self, failure_type: str, details: str, run_id: str = "") -> None:
        """Logs recurring failure patterns (e.g. low_github_coverage, extraction_timeout)."""
        failures = self._load_json(self.failures_file)
        entry = {
            "failure_type": failure_type,
            "details": self._sanitize(details),
            "run_id": run_id,
            "timestamp": time.time(),
        }
        failures.append(entry)
        self._save_json(self.failures_file, failures[-100:])

        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO failure_patterns (failure_type, details, run_id, timestamp) VALUES (?, ?, ?, ?)",
                    (failure_type, self._sanitize(details), run_id, time.time()),
                )
                conn.commit()
        except Exception:
            pass

    def get_recurring_failures(self, limit: int = 10) -> List[dict]:
        return self._load_json(self.failures_file)[-limit:]

    def save_improvement_proposal(self, proposal: dict) -> None:
        """Stores a versioned proposal to adjust queries, source priorities, or prompts."""
        proposals = self._load_json(self.proposals_file)
        cleaned = {k: self._sanitize(str(v)) if isinstance(v, str) else v for k, v in proposal.items()}
        cleaned["updated_at"] = time.time()
        pid = cleaned.get("proposal_id", f"prop_{int(time.time())}")
        cleaned["proposal_id"] = pid
        proposals = [p for p in proposals if p.get("proposal_id") != pid]
        proposals.append(cleaned)
        self._save_json(self.proposals_file, proposals)

    def get_improvement_proposals(self) -> List[dict]:
        return self._load_json(self.proposals_file)

    def delete_memory(self, category: str, item_id: str) -> bool:
        """Correction & deletion control for saved memory entries."""
        file_map = {
            "episodic": self.episodic_file,
            "project": self.project_file,
            "decisions": self.decision_file,
            "cache": self.cache_file,
            "proposals": self.proposals_file,
        }
        target_file = file_map.get(category)
        if not target_file:
            return False

        items = self._load_json(target_file)
        initial_len = len(items)
        items = [it for it in items if it.get("id") != item_id and it.get("path") != item_id and it.get("url_or_key") != item_id and it.get("proposal_id") != item_id]
        if len(items) < initial_len:
            self._save_json(target_file, items)
            return True
        return False

    def retrieve_context_for_run(self, query: str, project_name: Optional[str] = None) -> str:
        """Retrieves and formats safe context from episodic runs, project memory, and approved decisions."""
        sections = []

        # 1. Matching episodic memory
        matched_runs = self.search_episodic(query, limit=2)
        if matched_runs:
            run_summaries = [f"Past run '{r.get('query')}': {r.get('summary')}" for r in matched_runs]
            sections.append("Past Research Context:\n" + "\n".join(run_summaries))

        # 2. Project profile if project_name provided
        if project_name:
            profile = self.get_project_profile(project_name)
            if profile:
                stack = profile.get("detected_stack", [])
                sections.append(f"Project Profile ({project_name}):\n- Detected stack: {', '.join(stack)}")

        # 3. Approved decisions
        decisions = self.get_approved_decisions()
        if decisions:
            dec_list = [f"- {d.get('title')}: {d.get('rationale')}" for d in decisions[-3:]]
            sections.append("Approved Architectural Decisions:\n" + "\n".join(dec_list))

        return "\n\n".join(sections)
