from __future__ import annotations

import pytest
from src.agents.analyzer import Analyzer
from src.agents.synthesizer import Synthesizer, remap_citations_in_text
from src.llm.provider import MockProviderAdapter
from src.models.raven import (
    AnalysisResult,
    EvidenceItem,
    FinalRavenReport,
    IntentType,
    ResearchObjective,
    ResearchPlan,
    VerificationState,
)
from src.tunnels.normalizer import normalize_evidence_items, normalize_url
from src.tunnels.relevance import RelevanceFilter


class TestPhase1EvidenceQuality:
    @pytest.mark.asyncio
    async def test_dsa_disambiguation_and_crypto_rejection(self):
        """Test DSA acronym disambiguation and crypto signature evidence rejection."""
        mock_resp = (
            '{"intent": "idea", "goal": "Understand DSA linked list operations", '
            '"constraints": [], "assumptions": [], "unknowns": []}'
        )
        llm = MockProviderAdapter(canned_response=mock_resp)
        analyzer = Analyzer(llm_adapter=llm)

        analysis = await analyzer.analyze("DSA linked list implementation in python")
        terms_lower = {k.lower(): v.lower() for k, v in analysis.disambiguated_terms.items()}
        assert "dsa" in terms_lower
        assert "data structures and algorithms" in terms_lower["dsa"]
        assert "data structures and algorithms" in (analysis.domain_anchor or "").lower()
        assert analysis.is_concept_query is True

        # Candidate item talking about cryptographic Digital Signature Algorithm
        crypto_item = EvidenceItem(
            evidence_id="ev_crypto_1",
            source_type="web",
            canonical_url="https://en.wikipedia.org/wiki/Digital_Signature_Algorithm",
            title="Digital Signature Algorithm - Wikipedia",
            excerpt="The Digital Signature Algorithm (DSA) is a Federal Information Processing Standard for digital signatures based on modular exponentiation and discrete logarithms.",
            objective_id="obj_1",
        )

        filter_engine = RelevanceFilter()
        evaluation = filter_engine.evaluate_item(crypto_item, analysis=analysis)
        assert evaluation.is_relevant is False
        assert "domain mismatch" in evaluation.reason.lower()

    def test_binary_pdf_and_linguistics_paper_rejection(self):
        """Test that %PDF- binary headers and linguistics papers are rejected by RelevanceFilter."""
        analysis = AnalysisResult(
            intent=IntentType.IDEA,
            goal="Analyze linked list time complexity",
            domain_anchor="Computer Science / Data Structures & Algorithms",
            is_concept_query=True,
            disambiguated_terms={"concept": "linked list"},
        )
        filter_engine = RelevanceFilter()

        # 1. Raw PDF binary stream
        raw_pdf_item = EvidenceItem(
            evidence_id="ev_pdf_bad",
            source_type="web",
            canonical_url="https://example.com/paper.pdf",
            title="Corrupted PDF Stream",
            excerpt="%PDF-1.4\n%\xe2\xe3\xcf\xd3\nstream\nx\x9c\xed\x9d\x07...",
            objective_id="obj_1",
        )
        eval_pdf = filter_engine.evaluate_item(raw_pdf_item, analysis=analysis)
        assert eval_pdf.is_relevant is False
        assert "binary pdf" in eval_pdf.reason.lower()

        # 2. Linguistics paper with vowel harmony
        linguistics_item = EvidenceItem(
            evidence_id="ev_vowel_1",
            source_type="papers",
            canonical_url="https://arxiv.org/abs/2101.99999",
            title="Vowel Harmony in Altaic Languages",
            excerpt="We explore vowel harmony phonology and morphological constraints across Turkic and Mongolic languages.",
            objective_id="obj_1",
        )
        eval_ling = filter_engine.evaluate_item(linguistics_item, analysis=analysis)
        assert eval_ling.is_relevant is False
        assert "domain mismatch" in eval_ling.reason.lower()

    def test_url_canonicalization_and_deduplication(self):
        """Two identical URLs differing only in params/trailing slashes are deduplicated and objectives merged."""
        url_a = "https://pypi.org/project/llist/"
        url_b = "https://pypi.org/project/llist?utm_source=python_digest&utm_medium=feed#description"

        assert normalize_url(url_a) == normalize_url(url_b)

        item_a = EvidenceItem(
            evidence_id="ev_a",
            source_type="web",
            canonical_url=url_a,
            title="llist 0.7.1 - PyPI",
            excerpt="llist is an extension module providing linked list data structures for Python: dllist and sllist.",
            objective_id="obj_1",
            objective_ids=["obj_1"],
        )
        item_b = EvidenceItem(
            evidence_id="ev_b",
            source_type="web",
            canonical_url=url_b,
            title="llist 0.7.1 on PyPI",
            excerpt="llist extension module with high-performance C implementations of linked lists for Python.",
            objective_id="obj_2",
            objective_ids=["obj_2"],
        )

        deduped = normalize_evidence_items([item_a, item_b])
        assert len(deduped) == 1
        assert "obj_1" in deduped[0].objective_ids
        assert "obj_2" in deduped[0].objective_ids

    def test_naming_repo_rejected_and_comparison_table_omitted_for_concept_query(self):
        """Naming dictionary repositories are rejected and concept queries omit repo comparison tables."""
        analysis = AnalysisResult(
            intent=IntentType.IDEA,
            goal="Explain linked list structure",
            domain_anchor="Computer Science / Data Structures & Algorithms",
            is_concept_query=True,
            disambiguated_terms={"concept": "linked list"},
        )

        naming_repo = EvidenceItem(
            evidence_id="ev_repo_naming",
            source_type="github",
            canonical_url="https://github.com/wklken/naming",
            title="wklken/naming",
            publisher_or_repo="wklken/naming",
            excerpt="Curated naming dictionary for variables and functions in programming projects.",
            objective_id="obj_1",
        )

        filter_engine = RelevanceFilter()
        eval_res = filter_engine.evaluate_item(naming_repo, analysis=analysis)
        assert eval_res.is_relevant is False
        assert "naming dictionary repository is irrelevant" in eval_res.reason

        # Test synthesizer formatting omits repo comparison for pure concept queries
        valid_evidence = [
            EvidenceItem(
                evidence_id="ev_valid_1",
                source_type="documentation",
                canonical_url="https://docs.python.org/3/tutorial/datastructures.html",
                title="Python Data Structures",
                excerpt="Lists in Python can be used as queues and linked representations with collections.deque.",
                objective_id="obj_1",
            )
        ]
        plan = ResearchPlan(
            objectives=[
                ResearchObjective(
                    id="obj_1",
                    description="Analyze linked list fundamentals",
                    questions=["What are the memory trade-offs?"],
                    assigned_tunnels=["docs"],
                )
            ]
        )

        report = FinalRavenReport(
            title="Linked List Analysis",
            direct_answer="Linked lists are linear data structures where elements point to the next element.",
            executive_summary="Linked lists provide O(1) insertions at head.",
            architectural_findings="Sequential pointer chains require extra node overhead.",
            sourced_facts=["Nodes link to next elements [S1]."],
            inferences=["Arrays often outperform linked lists due to cache locality."],
            recommendations=["Use deque for FIFO queues in Python."],
            limitations_and_uncertainty=["Specific hardware memory latency."],
            stop_reason="Completed",
            failed_tunnels=[],
        )

        synth = Synthesizer(llm_adapter=MockProviderAdapter())
        markdown = synth._format_markdown(
            report,
            valid_evidence,
            analysis=analysis,
        )

        assert "## Repository Comparison" not in markdown
        assert "wklken/naming" not in markdown

    def test_deterministic_citation_remapping_and_ledger_parity(self):
        """Citations [ev_...] are deterministically remapped to appearance order [S1..Sn] and phantoms stripped."""
        item_1 = EvidenceItem(
            evidence_id="ev_doc_101",
            source_type="documentation",
            canonical_url="https://example.com/docs/linked-list",
            title="Linked List Documentation",
            excerpt="Linked list node definition and operations.",
            objective_id="obj_1",
        )
        item_2 = EvidenceItem(
            evidence_id="ev_doc_202",
            source_type="web",
            canonical_url="https://example.com/complexity",
            title="Time Complexity Guide",
            excerpt="Traversal is O(n) while insertion is O(1).",
            objective_id="obj_1",
        )

        valid_evidence = {
            "ev_doc_101": item_1,
            "ev_doc_202": item_2,
        }
        id_to_index: dict[str, int] = {}
        ordered_evidence: list[EvidenceItem] = []

        raw_text = (
            "First fact uses [ev_doc_202], then a second fact cites [ev_doc_101]. "
            "A hallucinated citation [ev_fake_999] must be cleanly stripped!"
        )

        remapped = remap_citations_in_text(raw_text, valid_evidence, id_to_index, ordered_evidence)

        # ev_doc_202 appeared first, so it becomes [S1]
        # ev_doc_101 appeared second, so it becomes [S2]
        # ev_fake_999 is stripped
        assert "[S1]" in remapped
        assert "[S2]" in remapped
        assert "ev_doc_202" not in remapped
        assert "ev_doc_101" not in remapped
        assert "ev_fake_999" not in remapped
        assert ordered_evidence[0].evidence_id == "ev_doc_202"
        assert ordered_evidence[1].evidence_id == "ev_doc_101"
        assert len(ordered_evidence) == 2
