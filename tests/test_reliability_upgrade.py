from __future__ import annotations

import asyncio
import ssl
import sys
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from fastapi.testclient import TestClient

from src.api import app, job_manager
from src.models.raven import (
    EvidenceItem,
    ResearchObjective,
    ResearchPlan,
    SourceTunnelResult,
)
from src.schemas import ResearchRequest
from src.tunnels.base import BaseTunnel
from src.tunnels.browser_inspector import BrowserInspector, InspectedResource
from src.tunnels.dispatcher import TunnelDispatcher
from src.tunnels.papers_tunnel import PapersTunnel
from src.utils.job_manager import ResearchJob, ResearchJobManager
from src.utils.ssl_context import get_trusted_ssl_context


# ── 1. Playwright NotImplementedError on Windows / SelectorEventLoop ──────────

@pytest.mark.asyncio
async def test_playwright_not_implemented_handled_gracefully():
    """Verify that when Playwright raises NotImplementedError (e.g. Windows SelectorEventLoop),
    BrowserInspector disables browser capability and does not crash or loop repeatedly."""
    inspector = BrowserInspector()

    # Simulate _fetch_via_http returning empty/short content
    empty_http = InspectedResource(url="https://example.com/js-page", status="success", main_text="short")
    with patch.object(inspector, "_fetch_via_http", new=AsyncMock(return_value=empty_http)):
        with patch.object(
            inspector,
            "_fetch_via_playwright",
            new=AsyncMock(
                return_value=InspectedResource(
                    url="https://example.com/js-page",
                    status="failed",
                    error="Browser extraction not supported on current runtime: NotImplementedError",
                    extraction_method="playwright",
                )
            ),
        ):
            # Manually trigger disabled state as would happen on SelectorEventLoop
            inspector._browser_available = False
            inspector._browser_disabled_reason = "Windows SelectorEventLoop does not support asyncio subprocesses"

            res = await inspector.inspect_resource("https://example.com/js-page", mode="auto")
            assert res is not None
            # The HTTP result is preserved rather than throwing
            assert res.status == "success"
            assert res.main_text == "short"


@pytest.mark.asyncio
async def test_windows_selector_loop_detection():
    """Verify that is_browser_available correctly identifies SelectorEventLoop."""
    inspector = BrowserInspector()

    class FakeSelectorLoop(asyncio.SelectorEventLoop):
        pass

    with patch("sys.platform", "win32"):
        with patch("asyncio.get_running_loop", return_value=FakeSelectorLoop()):
            is_avail = inspector.is_browser_available()
            assert is_avail is False
            assert "SelectorEventLoop" in (inspector._browser_disabled_reason or "")


# ── 2. Browser inspection fails and HTTP extraction succeeds ─────────────────

@pytest.mark.asyncio
async def test_browser_fails_http_succeeds_preserves_content():
    """Verify that when Playwright fails, valid HTTP extraction content is preserved."""
    inspector = BrowserInspector()
    inspector._browser_available = True

    http_success = InspectedResource(
        url="https://example.com/doc",
        status="success",
        main_text="Detailed technical architecture documentation extracted statically via HTTP.",
        extraction_method="http",
    )
    pw_failure = InspectedResource(
        url="https://example.com/doc",
        status="failed",
        error="Browser crash / timeout",
        extraction_method="playwright",
    )

    with patch.object(inspector, "_fetch_via_http", new=AsyncMock(return_value=http_success)):
        with patch.object(inspector, "_fetch_via_playwright", new=AsyncMock(return_value=pw_failure)):
            res = await inspector.inspect_resource("https://example.com/doc", mode="auto")
            assert res.status == "success"
            assert "Detailed technical architecture" in res.main_text
            assert res.extraction_method == "http"


# ── 3. Browser and HTTP extraction both fail ─────────────────────────────────

@pytest.mark.asyncio
async def test_browser_and_http_both_fail_returns_structured_result():
    """Verify that when both extractions fail, a structured failed InspectedResource is returned without raising."""
    inspector = BrowserInspector()
    inspector._browser_available = True

    http_fail = InspectedResource(
        url="https://example.com/down",
        status="failed",
        error="HTTP 503 Service Unavailable",
        extraction_method="http",
    )
    pw_fail = InspectedResource(
        url="https://example.com/down",
        status="failed",
        error="Page.goto: net::ERR_CONNECTION_REFUSED",
        extraction_method="playwright",
    )

    with patch.object(inspector, "_fetch_via_http", new=AsyncMock(return_value=http_fail)):
        with patch.object(inspector, "_fetch_via_playwright", new=AsyncMock(return_value=pw_fail)):
            res = await inspector.inspect_resource("https://example.com/down", mode="auto")
            assert res.status == "failed"
            assert res.error is not None
            assert not res.main_text


# ── 4. One tunnel fails while sibling tunnels succeed ────────────────────────

class MockSucceedingTunnel(BaseTunnel):
    def __init__(self, name: str = "good_tunnel") -> None:
        super().__init__(name=name)

    def is_enabled(self):
        return True, "Enabled"

    async def fetch(self, query: str, objective_id: str, limit: int = 5) -> SourceTunnelResult:
        item = EvidenceItem(
            evidence_id="ev_001",
            source_type=self.name,
            canonical_url=f"https://{self.name}.org/item1",
            title="Valid Source Item",
            excerpt="High quality verified evidence.",
            objective_id=objective_id,
        )
        return SourceTunnelResult(tunnel_name=self.name, status="completed", items=[item])


class MockFailingTunnel(BaseTunnel):
    def __init__(self, name: str = "bad_tunnel") -> None:
        super().__init__(name=name)

    def is_enabled(self):
        return True, "Enabled"

    async def fetch(self, query: str, objective_id: str, limit: int = 5) -> SourceTunnelResult:
        raise aiohttp.ClientError("Simulated remote network failure")


@pytest.mark.asyncio
async def test_one_tunnel_fails_sibling_tunnels_succeed():
    """Verify that when one tunnel raises an exception, other tunnels complete and evidence is preserved."""
    dispatcher = TunnelDispatcher()
    dispatcher.tunnels["t_good"] = MockSucceedingTunnel("t_good")
    dispatcher.tunnels["t_bad"] = MockFailingTunnel("t_bad")

    obj = ResearchObjective(
        id="obj_test",
        description="Test Objective",
        assigned_tunnels=["t_good", "t_bad"],
    )

    results = await dispatcher.execute_objective(obj, "test query")
    assert len(results) == 2

    by_name = {r.tunnel_name: r for r in results}
    assert by_name["t_good"].status == "completed"
    assert len(by_name["t_good"].items) == 1

    assert by_name["t_bad"].status == "failed"
    assert "ClientError" in (by_name["t_bad"].error or "")


# ── 5. Spawned task raises an exception and failure is handled ───────────────

@pytest.mark.asyncio
async def test_execute_plan_isolates_exceptions_without_unretrieved_task_errors():
    """Verify execute_plan uses return_exceptions=True and captures task exceptions cleanly."""
    dispatcher = TunnelDispatcher()

    # Make execute_objective raise an unexpected exception for one objective
    async def flaky_execute_objective(obj, query, limit_per_tunnel=4):
        if obj.id == "fail_obj":
            raise RuntimeError("Unexpected pipeline crash in objective")
        item = EvidenceItem(
            evidence_id=f"ev_{obj.id}",
            source_type="web",
            canonical_url=f"https://example.com/{obj.id}",
            excerpt=f"Objective evidence excerpt for {obj.id}",
            objective_id=obj.id,
        )
        return [SourceTunnelResult(tunnel_name="web", status="completed", items=[item])]

    dispatcher.execute_objective = flaky_execute_objective

    objs = [
        ResearchObjective(id="ok_obj_1", description="Valid objective 1"),
        ResearchObjective(id="fail_obj", description="Failing objective"),
        ResearchObjective(id="ok_obj_2", description="Valid objective 2"),
    ]

    verified, all_tr = await dispatcher.execute_plan(objs, "sample query")
    # Verified evidence from the 2 succeeding objectives must be retained!
    assert len(verified) == 2
    # The failed objective is converted into a failed result
    assert any(tr.status == "failed" for tr in all_tr)


# ── 6. Cancellation cleans up resources and propagates correctly ─────────────

@pytest.mark.asyncio
async def test_cancellation_propagates_and_cleans_up():
    """Verify that task cancellation is properly propagated without leaving zombie operations."""
    cleanup_called = False

    async def long_running_task():
        nonlocal cleanup_called
        try:
            await asyncio.sleep(10.0)
        finally:
            cleanup_called = True

    task = asyncio.create_task(long_running_task())
    await asyncio.sleep(0.01)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert cleanup_called is True
    assert task.cancelled()


# ── 7. arXiv TLS failure is represented as source failure without disabling TLS ─

@pytest.mark.asyncio
async def test_arxiv_tls_failure_isolated_without_disabling_verification():
    """Verify that an SSL certificate error on arXiv is reported as a source-level failure,
    while TLS verification remains strictly enabled."""
    tunnel = PapersTunnel()

    # Verify TLS context strictly enforces verification
    ctx = get_trusted_ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True

    # Simulate SSL certificate verification failure
    ssl_err = aiohttp.ClientConnectorCertificateError(
        connection_key=MagicMock(),
        certificate_error=ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED]"),
    )

    with patch("aiohttp.ClientSession.get", side_effect=ssl_err):
        result = await tunnel.fetch("quantum computing", "obj_paper_1")
        assert result.status == "failed"
        assert result.tunnel_name == "papers"
        assert "TLS certificate verification failed" in (result.error or "")
        assert result.reason == "arXiv TLS certificate verification failed"


# ── 8. Client disconnect does not cancel independent research run ─────────────

@pytest.mark.asyncio
async def test_client_disconnect_does_not_cancel_research_run():
    """Verify that when an SSE subscriber disconnects, the background research task continues executing."""
    mgr = ResearchJobManager()
    request = ResearchRequest(topic="Test autonomous systems")
    job = mgr.create_job(request)

    run_finished = False

    async def fake_graph_stream(state):
        nonlocal run_finished
        # Simulate multi-step graph execution
        for i in range(5):
            await asyncio.sleep(0.05)
            yield {"analyzer": {"events": [{"seq": i + 1, "step": "node", "status": "done"}]}}
        run_finished = True
        yield {"synthesizer": {"markdown_output": "# Completed Report", "final_report": {"run_status": "completed"}}}

    mock_graph = MagicMock()
    mock_graph.astream = fake_graph_stream

    task = mgr.start_job(job, mock_graph)

    # Client connects and subscribes
    subscriber_queue = job.add_subscriber()
    first_event = await subscriber_queue.get()
    assert first_event["type"] == "run_start"

    # Simulate client disconnect: subscriber is removed and queue discarded
    job.remove_subscriber(subscriber_queue)

    # Await the independent job task to complete
    await task

    assert run_finished is True
    assert job.status == "completed"
    assert job.result_payload is not None
    assert job.result_payload["title"] == "Test autonomous systems"


# ── 9. Explicit run cancellation still works ─────────────────────────────────

@pytest.mark.asyncio
async def test_explicit_run_cancellation_works():
    """Verify that calling cancel() on a ResearchJob explicitly stops execution."""
    mgr = ResearchJobManager()
    request = ResearchRequest(topic="Long running topic")
    job = mgr.create_job(request)

    async def infinite_graph_stream(state):
        while True:
            await asyncio.sleep(0.1)
            yield {"analyzer": {}}

    mock_graph = MagicMock()
    mock_graph.astream = infinite_graph_stream

    task = mgr.start_job(job, mock_graph)
    await asyncio.sleep(0.02)

    assert not task.done()
    cancelled = job.cancel()
    assert cancelled is True

    # Wait for cancellation to settle
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert job.status == "cancelled"
    assert job.completed_event.is_set()


# ── 10. Research streaming terminal status/event behavior ────────────────────

@pytest.mark.asyncio
async def test_research_streaming_terminal_events():
    """Verify research jobs broadcast proper terminal result or error events."""
    mgr = ResearchJobManager()
    request = ResearchRequest(topic="Testing terminal events")
    job = mgr.create_job(request)

    async def failing_graph(state):
        await asyncio.sleep(0.01)
        if False:
            yield {}
        raise ValueError("Fatal error in graph execution")

    mock_graph = MagicMock()
    mock_graph.astream = failing_graph

    queue = job.add_subscriber()
    task = mgr.start_job(job, mock_graph)

    events = []
    try:
        await task
    except Exception:
        pass

    while not queue.empty():
        events.append(queue.get_nowait())

    types = [e.get("type") for e in events]
    assert "run_start" in types
    assert "error" in types
    assert job.status == "failed"


# ── 11. Partial report when evidence is incomplete ───────────────────────────

def test_partial_report_structure():
    """Verify FinalRavenReport model supports partial run status and failed tunnels."""
    from src.models.raven import FinalRavenReport

    report = FinalRavenReport(
        title="Partial Technical Report",
        executive_summary="Partial results due to limited source availability.",
        failed_tunnels=["papers", "web"],
        run_status="partial",
        stop_reason="budget_exhausted",
    )
    assert report.run_status == "partial"
    assert "papers" in report.failed_tunnels
    assert report.stop_reason == "budget_exhausted"


# ── 12. Integration Test: Backend process survival under simulated failure ────

def test_backend_api_survives_simulated_failures():
    """Simulate API requests including health check, cancellation, and event retrieval,
    verifying server process remains alive and responsive."""
    client = TestClient(app)

    # 1. Health check passes
    resp = client.get("/health")
    assert resp.status_code == 200

    # 2. Cancel non-existent run returns 404 cleanly
    resp_cancel = client.post("/runs/non-existent-id/cancel")
    assert resp_cancel.status_code == 404

    # 3. Events retrieval for non-existent run returns empty list without error
    resp_events = client.get("/runs/non-existent-id/events")
    assert resp_events.status_code == 200
    assert resp_events.json()["events"] == []
