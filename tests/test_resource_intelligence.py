from __future__ import annotations

import os
import shutil
import tempfile
import pytest
from src.evaluation.evaluator import EVALUATION_DATASET, RavenEvaluator
from src.memory.content_store import BoundedContentStore
from src.memory.layered_memory import LayeredMemorySystem
from src.memory.learning import CrossRunLearningEngine, ImprovementProposal
from src.models.raven import EvidenceItem, FinalRavenReport, FreshnessState, ResearchObjective, ResearchPlan, VerificationState
from src.tunnels.browser_inspector import BrowserInspector, InspectedResource, safe_validate_url
from src.tunnels.github_tunnel import GitHubTunnel
from src.tunnels.verifier import EvidenceVerifier


# ── 1. SSRF & URL Validation Tests ────────────────────────────────────

def test_safe_validate_url_blocks_private_and_bad_schemes():
    with pytest.raises(ValueError, match="Blocked invalid URL scheme"):
        safe_validate_url("file:///etc/passwd")

    with pytest.raises(ValueError, match="Blocked invalid URL scheme"):
        safe_validate_url("gopher://example.com")

    with pytest.raises(ValueError, match="Blocked restricted host"):
        safe_validate_url("http://127.0.0.1/admin")

    with pytest.raises(ValueError, match="Blocked restricted host"):
        safe_validate_url("http://169.254.169.254/latest/meta-data")

    # Valid public URL should pass
    from unittest.mock import patch
    with patch("socket.getaddrinfo", return_value=[(2, 1, 0, "", ("93.184.216.34", 0))]):
        validated = safe_validate_url("https://www.python.org")
        assert "https://www.python.org" in validated


# ── 2. Bounded Content Store Tests ────────────────────────────────────

def test_bounded_content_store_put_and_get():
    tmp_dir = tempfile.mkdtemp()
    try:
        store = BoundedContentStore(base_dir=tmp_dir, max_bytes=1000)
        ref = store.put("https://example.com", "Hello, world! Detailed text content.")
        assert ref != ""
        fetched = store.get(ref)
        assert fetched == "Hello, world! Detailed text content."
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── 3. GitHub Deep Extraction Tests ───────────────────────────────────

def test_github_readme_section_extraction():
    tunnel = GitHubTunnel()
    readme = """# Mock Project
    
    ## Overview
    This project is an embedded analytics engine.
    
    ## Architecture
    The system uses columnar storage with vectorized execution.
    
    ## Features
    - High-throughput scans
    - Memory efficient
    """
    sections = tunnel._extract_readme_sections(readme)
    assert "Architecture" in sections
    assert "columnar storage" in sections
    assert "Features" in sections


# ── 4. Browser Inspector HTML Parsing ─────────────────────────────────

def test_browser_inspector_html_parsing():
    tmp_dir = tempfile.mkdtemp()
    try:
        inspector = BrowserInspector()
        inspector.content_store = BoundedContentStore(base_dir=tmp_dir)
        html = """
        <html>
            <head>
                <title>Test Page Title</title>
                <meta name="published_time" content="2026-05-10T12:00:00Z" />
            </head>
            <body>
                <h1>Main Heading</h1>
                <h2>Sub Heading</h2>
                <p>This is a substantive paragraph with detailed technical explanations of database internals and query engines.</p>
                <a href="https://primary.example.com/spec">Spec Link</a>
            </body>
        </html>
        """
        inspected = inspector._parse_html(html, "https://example.com/test", "http")
        assert inspected.title == "Test Page Title"
        assert "Main Heading" in inspected.headings
        assert inspected.date_metadata == "2026-05-10T12:00:00Z"
        assert len(inspected.relevant_links) >= 1

        evidence = inspector.extract_evidence(inspected, "obj_1")
        assert len(evidence) == 1
        assert evidence[0].extraction_method == "http"
        assert evidence[0].resource_explanation is not None
        assert "Main Heading" in evidence[0].resource_explanation["what_it_provides"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── 5. Evidence Verifier Date & Freshness Tests ────────────────────────

def test_evidence_verifier_iso_dates_and_relevance():
    # Fresh within 90 days
    item_fresh = EvidenceItem(
        evidence_id="ev_1",
        source_type="web",
        canonical_url="https://example.com",
        published_date="2026-10-01T00:00:00Z",
        excerpt="This is a substantial excerpt detailing query execution engines.",
        objective_id="obj_query",
    )
    verified = EvidenceVerifier.verify_item(item_fresh, objective_text="Evaluate query execution engine speed")
    assert verified.freshness_state == FreshnessState.FRESH
    assert verified.verification_state == VerificationState.VERIFIED
    assert "Relevance confirmed" in verified.verification_notes

    # Stale older than 1 year
    item_stale = EvidenceItem(
        evidence_id="ev_2",
        source_type="web",
        canonical_url="https://example.com",
        published_date="2020-01-01T00:00:00Z",
        excerpt="An old excerpt about legacy system performance.",
        objective_id="obj_query",
    )
    verified_stale = EvidenceVerifier.verify_item(item_stale)
    assert verified_stale.freshness_state == FreshnessState.STALE


# ── 6. Layered Memory SQLite FTS5 & Deletion Tests ────────────────────

def test_layered_memory_sqlite_fts_and_deletion():
    tmp_dir = tempfile.mkdtemp()
    try:
        mem = LayeredMemorySystem(base_storage_dir=tmp_dir)
        mem.record_run("run_101", "Vector search benchmarks", "Analyzed HNSW vs IVF indexing trade-offs", "completed")

        results = mem.search_episodic("Vector search")
        assert len(results) >= 1
        assert results[0]["id"] == "run_101"

        # Record and retrieve failure
        mem.record_failure("low_github_coverage", "Found no repositories with >100 stars", run_id="run_101")
        failures = mem.get_recurring_failures()
        assert len(failures) == 1
        assert failures[0]["failure_type"] == "low_github_coverage"

        # Test deletion
        deleted = mem.delete_memory("episodic", "run_101")
        assert deleted is True
        assert len(mem._load_json(mem.episodic_file)) == 0
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── 7. Evaluator Quality Metrics Tests ────────────────────────────────

def test_evaluator_metrics_calculation():
    evaluator = RavenEvaluator()
    plan = ResearchPlan(
        objectives=[
            ResearchObjective(id="obj_1", description="Evaluate DuckDB performance"),
            ResearchObjective(id="obj_2", description="Evaluate SQLite concurrency"),
        ]
    )
    evidence = [
        EvidenceItem(
            evidence_id="ev_1",
            source_type="github",
            canonical_url="https://github.com/duckdb/duckdb",
            title="DuckDB",
            excerpt="DuckDB is an analytical database engine written in C++.",
            objective_id="obj_1",
            published_date="2026-09-01T00:00:00Z",
            verification_state=VerificationState.VERIFIED,
            verification_notes="meets substance and length criteria",
        ),
        EvidenceItem(
            evidence_id="ev_2",
            source_type="documentation",
            canonical_url="https://sqlite.org/wal.html",
            title="SQLite WAL",
            excerpt="WAL mode provides concurrency with simultaneous readers and writers.",
            objective_id="obj_2",
            published_date="2026-08-01T00:00:00Z",
            verification_state=VerificationState.VERIFIED,
            verification_notes="meets substance and length criteria",
        ),
    ]
    report = FinalRavenReport(
        title="DuckDB vs SQLite",
        executive_summary="DuckDB excels at analytical scans while SQLite is best for transactional storage.",
        stop_reason="Target coverage reached",
        formatted_markdown="Report referencing [ev_1] and [ev_2].",
    )

    metrics = evaluator.evaluate(
        run_id="run_test",
        query="DuckDB vs SQLite",
        plan=plan,
        evidence=evidence,
        report=report,
        round_count=2,
        initial_coverage=0.5,
        final_coverage=1.0,
        llm_calls=5,
        duration=12.5,
    )

    assert metrics.objective_coverage == 1.0
    assert metrics.citation_validity == 1.0
    assert metrics.correction_gain == 0.5
    assert metrics.overall_score >= 0.8
    assert len(EVALUATION_DATASET) == 8


# ── 8. Cross-Run Learning Engine Tests ────────────────────────────────

def test_cross_run_learning_proposal_and_adoption():
    tmp_dir = tempfile.mkdtemp()
    try:
        mem = LayeredMemorySystem(base_storage_dir=tmp_dir)
        # Log 2 recurring failures to trigger proposal
        mem.record_failure("low_github_coverage", "Repo stars too low", "run_1")
        mem.record_failure("low_github_coverage", "Missing releases", "run_2")

        engine = CrossRunLearningEngine(memory_system=mem)
        proposal = engine.analyze_patterns_and_propose()
        assert proposal is not None
        assert proposal.category == "source_priority"

        evaluated = engine.evaluate_proposal(proposal)
        assert evaluated.status == "evaluated"
        assert evaluated.evaluated_score > evaluated.baseline_score

        adopted = engine.adopt_proposal(evaluated)
        assert adopted is True
        assert evaluated.status == "adopted"
        assert evaluated.rollback_data is not None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
