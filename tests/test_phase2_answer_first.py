from __future__ import annotations

import pytest
from src.agents.answer_drafter import AnswerDrafter
from src.agents.raven_graph import build_raven_graph
from src.agents.self_check import SelfChecker
from src.config import get_settings
from src.evaluation.answer_eval import evaluate_answer_quality
from src.llm.provider import MockProviderAdapter
from src.models.raven import (
    AnalysisResult,
    ClaimItem,
    DraftAnswer,
    EvidenceItem,
    IntentType,
    SelfCheckVerdict,
    VerificationState,
)


class TestPhase2AnswerFirst:
    @pytest.mark.asyncio
    async def test_answer_drafter_generates_2_to_4_sentences_with_citations(self):
        """Answer drafter produces exactly 2-4 sentences, citing verified evidence IDs."""
        mock_canned_json = (
            '{"text": "A linked list stores items in nodes connected via pointer chains [ev_1]. '
            'Access by index is O(n) due to sequential traversal, whereas dynamic arrays provide O(1) random indexing [ev_2]. '
            'In Python, collections.deque implements a doubly-linked list for fast head/tail appends [ev_3].", '
            '"sentences": ['
            '"A linked list stores items in nodes connected via pointer chains [ev_1].", '
            '"Access by index is O(n) due to sequential traversal, whereas dynamic arrays provide O(1) random indexing [ev_2].", '
            '"In Python, collections.deque implements a doubly-linked list for fast head/tail appends [ev_3]."'
            '], '
            '"claims": ['
            '{"claim_text": "Linked lists store items in nodes with pointer chains", "cited_evidence_ids": ["ev_1"]}, '
            '{"claim_text": "Index access is O(n)", "cited_evidence_ids": ["ev_2"]}, '
            '{"claim_text": "collections.deque implements doubly-linked list", "cited_evidence_ids": ["ev_3"]}'
            ']}'
        )

        llm = MockProviderAdapter(canned_response=mock_canned_json)
        drafter = AnswerDrafter(llm_adapter=llm)

        evidence = [
            EvidenceItem(
                evidence_id="ev_1",
                source_type="documentation",
                canonical_url="https://docs.example.com/ll",
                title="Linked List Basics",
                excerpt="Linked list nodes store value and next pointer.",
                objective_id="obj_1",
            ),
            EvidenceItem(
                evidence_id="ev_2",
                source_type="documentation",
                canonical_url="https://docs.example.com/complexity",
                title="Time Complexities",
                excerpt="Index lookup takes O(n) time.",
                objective_id="obj_1",
            ),
            EvidenceItem(
                evidence_id="ev_3",
                source_type="documentation",
                canonical_url="https://docs.python.org/3/library/collections.html",
                title="Collections Module",
                excerpt="deque provides O(1) appends and pops at both ends.",
                objective_id="obj_1",
            ),
        ]

        draft = await drafter.draft("linked list implementation and time complexity", evidence=evidence)

        assert 2 <= len(draft.sentences) <= 4
        assert len(draft.cited_evidence_ids) >= 2
        assert "ev_1" in draft.cited_evidence_ids
        assert "ev_2" in draft.cited_evidence_ids
        assert "ev_3" in draft.cited_evidence_ids
        assert draft.is_hedged_fallback is False

    @pytest.mark.asyncio
    async def test_answer_drafter_deterministic_fallback_when_llm_fails(self):
        """When LLM encounters an exception, drafter safely falls back without crashing."""
        llm = MockProviderAdapter(canned_response="INVALID JSON NOT PARSABLE")
        drafter = AnswerDrafter(llm_adapter=llm)

        evidence = [
            EvidenceItem(
                evidence_id="ev_1",
                source_type="documentation",
                canonical_url="https://docs.example.com/ll",
                title="Linked List Documentation",
                excerpt="A linked list consists of nodes each pointing to the next node.",
                objective_id="obj_1",
            )
        ]

        draft = await drafter.draft("what is a linked list", evidence=evidence)
        assert draft.is_hedged_fallback is True
        assert len(draft.sentences) >= 1
        assert "[ev_1]" in draft.text

    @pytest.mark.asyncio
    async def test_self_check_flags_hallucinated_citations_and_contradictions(self):
        """Self-checker deterministically catches hallucinated evidence IDs and semantic contradictions."""
        checker = SelfChecker(llm_adapter=MockProviderAdapter())

        valid_evidence = [
            EvidenceItem(
                evidence_id="ev_valid_1",
                source_type="documentation",
                canonical_url="https://docs.python.org/3/tutorial/datastructures.html",
                title="Python Data Structures",
                excerpt="Python lists are dynamic arrays, not linked lists.",
                objective_id="obj_1",
            )
        ]

        # Case 1: Hallucinated citation
        draft_hallucinated = DraftAnswer(
            text="Nodes connect to subsequent nodes [ev_fake_999]. Second sentence explains lookup [ev_valid_1].",
            sentences=["Nodes connect to subsequent nodes [ev_fake_999].", "Second sentence explains lookup [ev_valid_1]."],
            claims=[],
            cited_evidence_ids=["ev_fake_999", "ev_valid_1"],
        )
        verdict_hallucinated = await checker.verify(draft_hallucinated, valid_evidence)
        assert verdict_hallucinated.passed is False
        assert any("ev_fake_999" in u for u in verdict_hallucinated.unsupported_claims)

        # Case 2: Semantic contradiction
        draft_contradiction = DraftAnswer(
            text="In Python, Python list is a linked list structure [ev_valid_1]. It allows fast random indexing.",
            sentences=["In Python, Python list is a linked list structure [ev_valid_1].", "It allows fast random indexing."],
            claims=[],
            cited_evidence_ids=["ev_valid_1"],
        )
        verdict_contradiction = await checker.verify(draft_contradiction, valid_evidence)
        assert verdict_contradiction.passed is False
        assert len(verdict_contradiction.contradicted_claims) > 0

    @pytest.mark.asyncio
    async def test_self_check_bounded_retry_and_hedged_fallback(self):
        """Verifies that hedged fallback is constructed when self-check reaches max retry bounds."""
        draft = DraftAnswer(
            text="Uncertain claim that failed repeated verification [ev_1].",
            sentences=["Uncertain claim that failed repeated verification [ev_1]."],
            claims=[ClaimItem(claim_text="Uncertain claim", cited_evidence_ids=["ev_1"])],
            cited_evidence_ids=["ev_1"],
        )

        evidence = [
            EvidenceItem(
                evidence_id="ev_1",
                source_type="documentation",
                canonical_url="https://docs.example.com",
                title="Official Language Guide",
                excerpt="Python standard arrays use contiguous heap-allocated buffers.",
                objective_id="obj_1",
            )
        ]

        hedged = SelfChecker.make_hedged_fallback(draft, evidence)
        assert hedged.is_hedged_fallback is True
        assert "[ev_1]" in hedged.text
        assert "Official Language Guide" in hedged.text

    def test_answer_quality_evaluation_metrics(self):
        """Test deterministic answer evaluation metrics (length, citation density, hallucinations)."""
        valid_evidence = [
            EvidenceItem(
                evidence_id="ev_1",
                source_type="web",
                canonical_url="https://example.com/1",
                excerpt="First excerpt.",
                objective_id="obj_1",
            ),
            EvidenceItem(
                evidence_id="ev_2",
                source_type="web",
                canonical_url="https://example.com/2",
                excerpt="Second excerpt.",
                objective_id="obj_1",
            ),
        ]

        # Valid answer
        good_draft = DraftAnswer(
            text="Linked lists store values in node chains [ev_1]. Indexing requires traversing pointers [ev_2].",
            sentences=[
                "Linked lists store values in node chains [ev_1].",
                "Indexing requires traversing pointers [ev_2].",
            ],
            claims=[],
            cited_evidence_ids=["ev_1", "ev_2"],
            is_hedged_fallback=False,
        )
        passed_verdict = SelfCheckVerdict(passed=True, reason="All claims supported")

        metrics_good = evaluate_answer_quality(good_draft, valid_evidence, passed_verdict)
        assert metrics_good.length_ok is True
        assert metrics_good.sentence_count == 2
        assert metrics_good.citation_density == 1.0
        assert metrics_good.citation_density_ok is True
        assert metrics_good.zero_hallucinated_citations is True
        assert metrics_good.self_check_passed is True
        assert metrics_good.overall_quality_score == 1.0

        # Bad answer: 1 sentence, hallucinated citation, failed self-check
        bad_draft = DraftAnswer(
            text="A single sentence with invented citation [ev_fake_999].",
            sentences=["A single sentence with invented citation [ev_fake_999]."],
            claims=[],
            cited_evidence_ids=["ev_fake_999"],
            is_hedged_fallback=True,
        )
        failed_verdict = SelfCheckVerdict(passed=False, unsupported_claims=["ev_fake_999"])

        metrics_bad = evaluate_answer_quality(bad_draft, valid_evidence, failed_verdict)
        assert metrics_bad.length_ok is False
        assert metrics_bad.zero_hallucinated_citations is False
        assert metrics_bad.self_check_passed is False
        assert metrics_bad.overall_quality_score < 0.5

    def test_raven_graph_includes_answer_drafter_and_self_check_nodes(self):
        """Test that build_raven_graph includes answer_drafter and self_check when enabled."""
        settings = get_settings()
        assert settings.answer_first is True
        assert settings.self_check is True

        graph = build_raven_graph()
        # Verify compiled graph nodes exist
        nodes = graph.nodes
        assert "answer_drafter" in nodes
        assert "self_check" in nodes
        assert "synthesizer" in nodes
        assert "relevance_filter" in nodes
