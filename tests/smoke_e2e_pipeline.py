import asyncio
import json
import structlog
from src.agents.raven_graph import build_raven_graph
from src.agents.raven_state import RavenState

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)

async def test_full_pipeline():
    print("=== Testing RAVEN Graph End-to-End ===")
    graph = build_raven_graph()
    initial_state: RavenState = {
        "job_id": "smoke-test-dsa",
        "query": "dsa linkedlist",
        "profile": "standard",
        "max_rounds": 1,
        "round_count": 1,
        "events": [],
        "evidence": [],
        "tunnel_results": [],
    }

    final_state = {}
    async for step_chunk in graph.astream(initial_state):
        for node_name, node_state in step_chunk.items():
            final_state = node_state
            events = node_state.get("events", [])
            last_event = events[-1] if events else {}
            print(f"Node completed: {node_name} | Events: {len(events)} | Last step: {last_event.get('step')} ({last_event.get('status')})")

    report = final_state.get("final_report", {})
    markdown = final_state.get("markdown_output", "")
    evidence = final_state.get("evidence", [])

    print("\n=== PIPELINE RESULTS ===")
    print(f"Evidence count: {len(evidence)}")
    print(f"Report title: {report.get('title')}")
    print(f"Run status: {report.get('run_status')}")
    print(f"Direct answer length: {len(report.get('direct_answer', ''))}")
    print(f"Detailed explanation length: {len(report.get('detailed_explanation', ''))}")
    print(f"Key findings count: {len(report.get('key_findings', []))}")
    print(f"Limitations count: {len(report.get('limitations_and_uncertainty', []))}")
    print(f"Markdown output length: {len(markdown)}")

    print("\n--- Direct Answer Preview ---")
    print(report.get("direct_answer", "")[:400].encode("ascii", "replace").decode("ascii"))

    print("\n--- Key Findings Preview ---")
    for f in report.get("key_findings", [])[:3]:
        print(f"- {str(f).encode('ascii', 'replace').decode('ascii')}")

    print("\n--- Markdown Report Preview (first 600 chars) ---")
    print(markdown[:600].encode("ascii", "replace").decode("ascii"))

    # Check for substantive content and proper non-URL response
    assert len(markdown) > 300, "Markdown report too short!"
    assert len(evidence) > 0, "No evidence was gathered!"
    assert report.get("run_status") == "completed", f"Run status should be completed, got {report.get('run_status')}"
    print("\nSUCCESS: All pipeline assertions passed!")

if __name__ == "__main__":
    asyncio.run(test_full_pipeline())
