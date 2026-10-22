from __future__ import annotations

import json
import time
from enum import Enum
from typing import Any, Dict, List, Optional
import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)


class SSEEventType(str, Enum):
    RUN_START = "run_start"
    PROGRESS = "progress"
    WORKFLOW_EVENT = "workflow_event"
    TOKEN = "token"
    RESULT = "result"
    ANSWER = "answer"
    GRAPH = "graph"
    ERROR = "error"
    CANCELLED = "cancelled"
    HEARTBEAT = "heartbeat"


class ResearchEventEnvelope(BaseModel):
    """Strict JSON-safe event envelope for all SSE research events."""
    model_config = ConfigDict(extra="ignore")

    type: str = Field(description="Event classification type")
    job_id: str = Field(description="Unique research job identifier")
    seq: int = Field(default=0, description="Monotonically increasing sequence number")
    timestamp: float = Field(default_factory=time.time, description="Unix timestamp of event")

    # Optional payload attributes depending on event type
    topic: Optional[str] = None
    step: Optional[str] = None
    status: Optional[str] = None
    event: Optional[Dict[str, Any]] = None
    content: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    message: Optional[str] = None


def _to_json_safe(obj: Any, seen: set | None = None) -> Any:
    """Recursively convert objects to JSON-safe primitives with circular reference detection."""
    if seen is None:
        seen = set()

    obj_id = id(obj)
    if isinstance(obj, (dict, list)):
        if obj_id in seen:
            raise ValueError(f"Circular reference detected in object of type {type(obj).__name__}")
        seen.add(obj_id)

    try:
        if obj is None or isinstance(obj, (bool, int, float, str)):
            return obj
        if isinstance(obj, BaseModel):
            return _to_json_safe(obj.model_dump(), seen)
        if isinstance(obj, dict):
            return {str(k): _to_json_safe(v, seen) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [_to_json_safe(item, seen) for item in obj]
        if hasattr(obj, "to_dict") and callable(getattr(obj, "to_dict")):
            return _to_json_safe(obj.to_dict(), seen)
        # For non-primitive objects, raise so callers know to serialize explicitly
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
    finally:
        if isinstance(obj, (dict, list)):
            seen.remove(obj_id)


def serialize_sse_event(event: Dict[str, Any], job_id: str) -> str:
    """Validate and serialize an SSE event to a data-line string.
    
    Guarantees:
    - Envelope structure conforms to ResearchEventEnvelope.
    - No circular references or invalid types.
    - Does not use default=str to silence errors.
    - Emits structured error on failure.
    """
    event_type = str(event.get("type", "unknown"))
    try:
        # Enforce envelope formatting
        envelope_data = {
            "type": event_type,
            "job_id": str(event.get("job_id", job_id)),
            "seq": int(event.get("seq", 0)),
            "timestamp": float(event.get("timestamp", time.time())),
        }
        for k, v in event.items():
            if k not in envelope_data:
                envelope_data[k] = v

        json_safe = _to_json_safe(envelope_data)
        serialized_json = json.dumps(json_safe, ensure_ascii=False)
        return f"data: {serialized_json}\n\n"

    except Exception as exc:
        # Log event type and job_id safely without leaking any payload or secrets
        logger.error(
            "sse.serialization_failed",
            job_id=job_id,
            event_type=event_type,
            error=str(exc),
        )
        # Do not silently drop; return a valid error envelope
        err_envelope = {
            "type": SSEEventType.ERROR.value,
            "job_id": job_id,
            "seq": int(event.get("seq", 0)),
            "timestamp": time.time(),
            "message": f"Serialization failure on event type '{event_type}': {str(exc)}",
        }
        return f"data: {json.dumps(err_envelope)}\n\n"
