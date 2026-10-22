from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Dict, List, Optional, Set
import structlog

from src.schemas import ImageResult, ResearchRequest
from src.tools.graph import build_citation_graph
from src.tools.images import search_related_images

logger = structlog.get_logger(__name__)


class ResearchJob:
    """Encapsulates the state, tasks, and subscriber queues of an in-flight or completed research run."""

    def __init__(self, job_id: str, request: ResearchRequest) -> None:
        self.job_id = job_id
        self.request = request
        self.topic = request.topic
        self.events: List[Dict[str, Any]] = []
        self.subscribers: Set[asyncio.Queue] = set()
        self.task: Optional[asyncio.Task] = None
        self.status: str = "running"  # "running", "completed", "failed", "cancelled"
        self.error: Optional[str] = None
        self.result_payload: Optional[Dict[str, Any]] = None
        self.created_at: float = time.time()
        self.finished_at: Optional[float] = None
        self.completed_event: asyncio.Event = asyncio.Event()
        self.seq: int = 0

    def add_subscriber(self) -> asyncio.Queue:
        """Register a new SSE stream subscriber and replay all previous events."""
        q: asyncio.Queue = asyncio.Queue()
        for ev in self.events:
            q.put_nowait(ev)
        self.subscribers.add(q)
        return q

    def remove_subscriber(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    def broadcast_event(self, event_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Record an event to the journal with sequence number and broadcast to all active listeners."""
        self.seq += 1
        safe_ev = dict(event_dict)
        safe_ev.setdefault("job_id", self.job_id)
        safe_ev.setdefault("seq", self.seq)
        safe_ev.setdefault("timestamp", time.time())
        self.events.append(safe_ev)
        for q in list(self.subscribers):
            try:
                q.put_nowait(safe_ev)
            except Exception:
                pass
        return safe_ev

    def cancel(self) -> bool:
        """Request cancellation of the underlying task."""
        if self.task and not self.task.done():
            self.status = "cancelled"
            self.task.cancel()
            logger.info("job.cancellation_requested", job_id=self.job_id)
            return True
        return False


class ResearchJobManager:
    """Supervises active research tasks independently from SSE client lifecycles."""

    def __init__(self) -> None:
        self.jobs: Dict[str, ResearchJob] = {}

    def get_job(self, job_id: str) -> Optional[ResearchJob]:
        return self.jobs.get(job_id)

    def create_job(self, request: ResearchRequest, job_id: Optional[str] = None) -> ResearchJob:
        jid = job_id or str(uuid.uuid4())
        job = ResearchJob(jid, request)
        self.jobs[jid] = job
        return job

    def get_or_create_job(
        self, request: ResearchRequest, job_id: Optional[str] = None
    ) -> tuple[ResearchJob, bool]:
        """Retrieve existing job or create a new one. Returns (job, created)."""
        jid = request.job_id or job_id
        if jid and jid in self.jobs:
            return self.jobs[jid], False
        new_job = self.create_job(request, job_id=jid)
        return new_job, True

    def start_job(self, job: ResearchJob, raven_graph: Any) -> asyncio.Task:
        task = asyncio.create_task(
            self._run_job_coro(job, raven_graph),
            name=f"raven-job-{job.job_id}",
        )
        job.task = task
        return task

    async def _run_job_coro(self, job: ResearchJob, raven_graph: Any) -> None:
        job_id = job.job_id
        request = job.request
        logger.info("job.started", job_id=job_id, topic=request.topic)

        initial_state = {
            "job_id": job_id,
            "query": request.topic,
            "project_path": request.project_path,
            "profile": getattr(request, "profile", "standard"),
            "max_rounds": 3,
            "round_count": 1,
            "events": [],
            "evidence": [],
            "tunnel_results": [],
        }

        job.broadcast_event({"type": "run_start", "job_id": job_id, "topic": request.topic})

        emitted_event_count = 0
        final_state: Dict[str, Any] = {}

        try:
            async for step_chunk in raven_graph.astream(initial_state):
                for node_name, node_state in step_chunk.items():
                    final_state = node_state
                    job.broadcast_event({"type": "progress", "step": node_name, "status": "completed"})

                    all_events = node_state.get("events", [])
                    if len(all_events) > emitted_event_count:
                        for ev in all_events[emitted_event_count:]:
                            job.broadcast_event({"type": "workflow_event", "event": ev})
                        emitted_event_count = len(all_events)

                    if node_name in ("answer_drafter", "self_check") and node_state.get("draft_answer"):
                        job.broadcast_event({
                            "type": "answer",
                            "data": {
                                "answer": node_state["draft_answer"],
                                "self_check": node_state.get("self_check_verdict"),
                            },
                        })

                    if node_name == "synthesizer" and node_state.get("markdown_output"):
                        job.broadcast_event({"type": "token", "content": node_state["markdown_output"]})

            markdown_report = final_state.get("markdown_output", "")
            final_report_dict = final_state.get("final_report", {})
            evidence_items = final_state.get("evidence", [])
            sources = list(dict.fromkeys(e["canonical_url"] for e in evidence_items if e.get("canonical_url")))

            graph_data = build_citation_graph([{"source": s, "content": ""} for s in sources])

            image_results = []
            try:
                images = await asyncio.to_thread(search_related_images, request.topic, 6)
                image_results = [
                    ImageResult(
                        title=img.get("title", ""),
                        image_url=img.get("image", ""),
                        thumbnail_url=img.get("thumbnail"),
                        source_url=img.get("url", ""),
                        source_domain=img.get("source", ""),
                    )
                    for img in images
                    if img.get("image")
                ]
            except Exception:
                pass

            structured_sources = []
            for e in evidence_items:
                c_url = e.get("canonical_url", "")
                if not c_url:
                    continue
                exp = e.get("resource_explanation") or {}
                structured_sources.append({
                    "title": e.get("title") or c_url,
                    "url": c_url,
                    "summary": exp.get("what_it_provides") or exp.get("what_it_is") or "Technical resource",
                    "relevant_excerpts": [e.get("excerpt")[:300]] if e.get("excerpt") else [],
                    "objective_ids": e.get("objective_ids", []),
                    "retrieval_status": (
                        "extracted"
                        if e.get("extraction_method") in ("http", "playwright", "github_api", "local_ast")
                        else "discovered"
                    ),
                    "verification_status": e.get("verification_state", "unverified"),
                    "checked_at": e.get("checked_at"),
                })

            run_status = final_report_dict.get("run_status") or ("completed" if markdown_report else "failed")

            # Detach workflow events to completely eliminate circular references with job.events
            workflow_events = [dict(ev) for ev in final_state.get("events", [])]

            result_payload = {
                "job_id": job_id,
                "title": final_report_dict.get("title") or request.topic,
                "final_report": markdown_report or final_report_dict.get("executive_summary", ""),
                "answer_markdown": markdown_report or final_report_dict.get("direct_answer", ""),
                "direct_answer": final_report_dict.get("direct_answer", ""),
                "detailed_explanation": final_report_dict.get("detailed_explanation", ""),
                "key_findings": list(final_report_dict.get("key_findings", [])),
                "limitations": list(final_report_dict.get("limitations_and_uncertainty", [])),
                "sources": sources,
                "structured_sources": structured_sources,
                "topic": request.topic,
                "graph_data": graph_data,
                "images": [img.model_dump() for img in image_results],
                "graphs": [],
                "events": workflow_events,
                "evidence": [dict(e) for e in evidence_items],
                "plan": final_state.get("plan"),
                "analysis": final_state.get("analysis"),
                "critique": final_state.get("critique"),
                "draft_answer": final_state.get("draft_answer"),
                "self_check_verdict": final_state.get("self_check_verdict"),
                "answer_eval": final_state.get("answer_eval"),
                "run_status": run_status,
                "stop_reason": final_state.get("stop_reason", "Completed"),
            }

            job.result_payload = result_payload
            job.status = run_status
            job.broadcast_event({"type": "result", "data": result_payload})
            logger.info("job.completed", job_id=job_id, status=run_status)

        except asyncio.CancelledError:
            job.status = "cancelled"
            job.broadcast_event({"type": "cancelled", "message": "Research run was cancelled"})
            logger.warning("job.cancelled", job_id=job_id)
            raise
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            job.broadcast_event({"type": "error", "message": str(exc)})
            logger.error("job.failed", job_id=job_id, error=str(exc))
        finally:
            job.finished_at = time.time()
            job.completed_event.set()

    def active_jobs(self) -> List[ResearchJob]:
        return [j for j in self.jobs.values() if j.task and not j.task.done()]

    async def shutdown(self, timeout_sec: float = 5.0) -> None:
        """Cancel and await all in-flight tasks cleanly upon server shutdown."""
        running = self.active_jobs()
        if not running:
            return

        logger.info("job_manager.shutdown_cancelling_tasks", count=len(running))
        for j in running:
            j.cancel()

        tasks = [j.task for j in running if j.task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("job_manager.shutdown_complete")
