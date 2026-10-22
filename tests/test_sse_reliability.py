from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api import app, job_manager
from src.models.events import ResearchEventEnvelope, SSEEventType, _to_json_safe, serialize_sse_event
from src.schemas import ResearchRequest
from src.utils.job_manager import ResearchJob, ResearchJobManager


# ── 1. Every Supported SSE Event Type ──────────────────────────────────────────

def test_every_supported_sse_event_type():
    """Verify serialization and envelope attributes for every supported event type."""
    job_id = "test-job-101"

    event_samples = [
        ({"type": "run_start", "topic": "Quantum Computing", "seq": 1}, SSEEventType.RUN_START),
        ({"type": "progress", "step": "analyzer", "status": "completed", "seq": 2}, SSEEventType.PROGRESS),
        (
            {
                "type": "workflow_event",
                "event": {"seq": 1, "step": "planner", "status": "running", "details": {}, "timestamp": time.time()},
                "seq": 3,
            },
            SSEEventType.WORKFLOW_EVENT,
        ),
        ({"type": "token", "content": "## Analysis\nFound 5 papers.", "seq": 4}, SSEEventType.TOKEN),
        (
            {
                "type": "result",
                "data": {
                    "final_report": "# Final Report",
                    "sources": ["https://arxiv.org/abs/2401.00001"],
                    "events": [{"seq": 1, "step": "analyzer", "status": "completed"}],
                },
                "seq": 5,
            },
            SSEEventType.RESULT,
        ),
        ({"type": "error", "message": "Failed to connect to source tunnel", "seq": 6}, SSEEventType.ERROR),
        ({"type": "cancelled", "message": "Run was cancelled by user", "seq": 7}, SSEEventType.CANCELLED),
    ]

    for raw_event, expected_enum in event_samples:
        sse_line = serialize_sse_event(raw_event, job_id=job_id)
        assert sse_line.startswith("data: ")
        assert sse_line.endswith("\n\n")

        payload = json.loads(sse_line[6:].strip())
        assert payload["type"] == expected_enum.value
        assert payload["job_id"] == job_id
        assert payload["seq"] == raw_event["seq"]
        assert "timestamp" in payload


# ── 2. JSON-Safe Serialization & Circular Payload Rejection ─────────────────────

def test_circular_reference_detected_and_handled_without_default_str():
    """Verify that circular data structures are intercepted safely and generate a valid error envelope
    instead of crashing the server with unhandled ValueError or using default=str."""
    job_id = "test-job-circular"

    # Create a circular dictionary
    cyclic_dict: Dict[str, Any] = {"name": "test"}
    cyclic_dict["self_ref"] = cyclic_dict

    # Check internal detection helper
    with pytest.raises(ValueError, match="Circular reference detected"):
        _to_json_safe(cyclic_dict)

    # Check top-level SSE serializer returns an explicit error envelope instead of throwing
    with patch("structlog.get_logger") as mock_logger:
        log_instance = MagicMock()
        mock_logger.return_value = log_instance

        sse_output = serialize_sse_event({"type": "result", "data": cyclic_dict, "seq": 10}, job_id=job_id)
        assert sse_output.startswith("data: ")
        parsed = json.loads(sse_output[6:].strip())

        assert parsed["type"] == "error"
        assert parsed["job_id"] == job_id
        assert "Serialization failure" in parsed["message"]
        assert "Circular reference" in parsed["message"]


def test_clean_detachment_of_events_in_job_manager():
    """Verify that ResearchJob result_payload detaches workflow events from job.events,
    completely preventing circular references."""
    req = ResearchRequest(topic="Test Architecture")
    job = ResearchJob("job-no-cycle", req)

    # Add several journal events
    job.broadcast_event({"type": "run_start", "topic": req.topic})
    job.broadcast_event({"type": "progress", "step": "analyzer", "status": "completed"})

    # Synthetic workflow events from node_state
    workflow_events = [
        {"seq": 1, "step": "analyzer", "status": "completed", "details": {}, "timestamp": time.time()}
    ]

    # result_payload must use detached workflow_events
    result_payload = {
        "job_id": job.job_id,
        "title": req.topic,
        "final_report": "Everything passed.",
        "events": workflow_events,
    }

    job.result_payload = result_payload
    job.status = "completed"
    result_ev = job.broadcast_event({"type": "result", "data": result_payload})

    # Serialization must succeed without circular reference error
    line = serialize_sse_event(result_ev, job_id=job.job_id)
    assert line.startswith("data: ")
    data = json.loads(line[6:].strip())
    assert data["type"] == "result"
    assert data["data"]["final_report"] == "Everything passed."
    assert len(data["data"]["events"]) == 1


# ── 3. Successful Final-Report Delivery Over SSE ──────────────────────────────

@pytest.mark.asyncio
async def test_successful_final_report_delivery():
    """Verify that a research workflow completes and delivers the final report over the SSE queue."""
    req = ResearchRequest(topic="State of Agentic Systems")
    mgr = ResearchJobManager()
    job = mgr.create_job(req, job_id="job-report-deliver")

    sub_queue = job.add_subscriber()

    # Mock graph that yields state and finishes
    async def mock_astream(state):
        yield {"analyzer": {"events": [{"seq": 1, "step": "analyzer", "status": "completed"}]}}
        yield {
            "synthesizer": {
                "markdown_output": "# Synthesized Report\nDetailed evaluation.",
                "final_report": {"executive_summary": "Detailed evaluation.", "key_findings": ["F1", "F2"]},
                "events": [
                    {"seq": 1, "step": "analyzer", "status": "completed"},
                    {"seq": 2, "step": "synthesizer", "status": "completed"},
                ],
                "evidence": [
                    {
                        "canonical_url": "https://example.com/paper1",
                        "title": "Paper 1",
                        "excerpt": "Excerpt from paper 1",
                    }
                ],
            }
        }

    mock_graph = MagicMock()
    mock_graph.astream = mock_astream

    with patch("src.utils.job_manager.search_related_images", return_value=[]):
        mgr.start_job(job, mock_graph)
        await job.completed_event.wait()

    assert job.status == "completed"
    assert job.result_payload is not None
    assert "# Synthesized Report" in job.result_payload["final_report"]

    # Read events from subscriber queue
    events = []
    while not sub_queue.empty():
        events.append(await sub_queue.get())

    types = [e["type"] for e in events]
    assert "run_start" in types
    assert "progress" in types
    assert "result" in types

    # Serialize each event to ensure full SSE validity
    for ev in events:
        line = serialize_sse_event(ev, job_id=job.job_id)
        assert line.startswith("data: ")
        parsed = json.loads(line[6:].strip())
        assert parsed["job_id"] == job.job_id


# ── 4. Stream Exceptions & Client Disconnects ─────────────────────────────────

@pytest.mark.asyncio
async def test_client_disconnect_preserves_running_background_job():
    """Verify that when an SSE client disconnects (raising CancelledError in the generator),
    the background research job continues running to completion without being killed."""
    req = ResearchRequest(topic="Decoupled SSE Run")
    mgr = ResearchJobManager()
    job = mgr.create_job(req, job_id="job-survives-disconnect")

    job_completed = asyncio.Event()

    async def slow_mock_astream(state):
        await asyncio.sleep(0.05)
        yield {
            "synthesizer": {
                "markdown_output": "Report finished after client left.",
                "final_report": {"executive_summary": "Done"},
                "events": [],
            }
        }
        job_completed.set()

    mock_graph = MagicMock()
    mock_graph.astream = slow_mock_astream

    with patch("src.utils.job_manager.search_related_images", return_value=[]):
        mgr.start_job(job, mock_graph)

        # Simulate subscriber joining then leaving early
        sub_queue = job.add_subscriber()
        first_ev = await sub_queue.get()  # gets run_start
        assert first_ev["type"] == "run_start"

        # Client leaves
        job.remove_subscriber(sub_queue)

        # Wait for the background task to complete independently
        await asyncio.wait_for(job.completed_event.wait(), timeout=3.0)
        assert job.status == "completed"
        assert "Report finished" in job.result_payload["final_report"]


# ── 5. Reconnection Without Creating Duplicate Run ───────────────────────────

@pytest.mark.asyncio
async def test_reconnection_without_duplicate_run():
    """Verify that requesting a stream or creating a job with an existing job_id
    reconnects to the existing job without creating duplicate tasks or re-running."""
    mgr = ResearchJobManager()
    req = ResearchRequest(topic="Duplicate Prevention")

    job1, created1 = mgr.get_or_create_job(req, job_id="shared-job-id")
    assert created1 is True
    assert job1.job_id == "shared-job-id"

    # Second call with the same job_id
    job2, created2 = mgr.get_or_create_job(req, job_id="shared-job-id")
    assert created2 is False
    assert job2 is job1

    # Request with job_id in body
    req_with_id = ResearchRequest(topic="Duplicate Prevention", job_id="shared-job-id")
    job3, created3 = mgr.get_or_create_job(req_with_id)
    assert created3 is False
    assert job3 is job1


# ── 6. Events Journal & State Preservation on Disconnect / Replay ─────────────

def test_events_journal_endpoint_and_state_preservation():
    """Verify GET /runs/{job_id}/events returns correct state and result for client replay."""
    client = TestClient(app)

    job_id = "test-journal-replay"
    req = ResearchRequest(topic="Journal Verification")
    job = job_manager.create_job(req, job_id=job_id)

    job.broadcast_event({"type": "run_start", "topic": req.topic})
    job.broadcast_event({"type": "progress", "step": "analyzer", "status": "completed"})

    result_payload = {
        "job_id": job_id,
        "title": req.topic,
        "final_report": "Replayed report content.",
        "sources": ["https://arxiv.org/abs/1234.5678"],
        "events": [{"seq": 1, "step": "analyzer", "status": "completed"}],
    }
    job.result_payload = result_payload
    job.status = "completed"
    job.broadcast_event({"type": "result", "data": result_payload})

    res = client.get(f"/runs/{job_id}/events")
    assert res.status_code == 200
    data = res.json()

    assert data["job_id"] == job_id
    assert data["status"] == "completed"
    assert len(data["events"]) == 3
    assert data["result"]["final_report"] == "Replayed report content."


# ── 7. API Route and Response-Contract Consistency ─────────────────────────────

def test_stream_and_run_routes_consistency():
    """Verify that all streaming and management routes exist and return correct contracts."""
    client = TestClient(app)

    # 1. Non-existent run stream returns 404
    missing_stream = client.get("/runs/non-existent-id/stream")
    assert missing_stream.status_code == 404

    # 2. Existing run stream returns text/event-stream
    test_jid = "test-route-stream"
    req = ResearchRequest(topic="Route Consistency")
    job = job_manager.create_job(req, job_id=test_jid)
    job.broadcast_event({"type": "run_start", "topic": req.topic})
    job.status = "completed"
    job.completed_event.set()

    stream_res = client.get(f"/runs/{test_jid}/stream")
    assert stream_res.status_code == 200
    assert "text/event-stream" in stream_res.headers.get("content-type", "")

    # 3. Cancel endpoint contract
    cancel_res = client.post(f"/runs/{test_jid}/cancel")
    assert cancel_res.status_code == 200
    cancel_data = cancel_res.json()
    assert cancel_data["job_id"] == test_jid
    assert "cancelled" in cancel_data
