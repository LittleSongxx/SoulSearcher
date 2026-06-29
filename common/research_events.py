from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class ResearchRunEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    source_event_type: str = ""
    sequence: int = 0
    event_id: str = ""
    schema_version: int = 1
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if not payload.get("event_id"):
            payload["event_id"] = f"rre_{self.sequence}"
        return payload


_STATUS_STEP_TO_EVENT = {
    "init": "run.started",
    "clarifying": "brief.clarifying",
    "planning": "plan.delta",
    "researching": "search.query",
    "deep_research": "research.running",
    "writing": "report.delta",
    "agent": "research.running",
}

_STREAM_TO_EVENT = {
    "brief_created": "brief.created",
    "strategy_selected": "plan.created",
    "plan_graph_update": "plan.graph.updated",
    "replan_requested": "plan.replan.requested",
    "replan_applied": "plan.replan.applied",
    "research_node_start": "research.node.started",
    "research_node_complete": "research.node.completed",
    "research_tree_update": "research.tree.updated",
    "search": "search.query",
    "sources": "source.fetched",
    "evidence_selected": "evidence.updated",
    "quality_update": "quality.updated",
    "quality_gate_evaluated": "quality.updated",
    "gap_detected": "quality.gap.detected",
    "text": "report.delta",
    "message": "report.delta",
    "completion": "report.completed",
    "artifact": "report.artifact.created",
    "report_written": "report.completed",
    "interrupt": "interrupt.required",
    "cancelled": "run.cancelled",
    "done": "run.completed",
    "error": "run.failed",
    "tool_error": "tool.failed",
    "tool_start": "tool.started",
    "tool_result": "tool.completed",
    "tool": "tool.updated",
    "thinking": "plan.delta",
}


def canonical_research_event_type(source_event_type: str, data: Any = None) -> str:
    source_type = str(source_event_type or "").strip()
    if source_type == "status" and isinstance(data, dict):
        step = str(data.get("step") or "").strip().lower()
        return _STATUS_STEP_TO_EVENT.get(step, "status.updated")
    return _STREAM_TO_EVENT.get(
        source_type,
        source_type.replace("_", ".") if source_type else "event",
    )


def build_research_run_event(
    source_event_type: str,
    data: Any = None,
    *,
    seq: int = 0,
    event_id: str | None = None,
) -> dict[str, Any]:
    event_data = data if isinstance(data, dict) else {"value": data}
    return ResearchRunEvent(
        type=canonical_research_event_type(source_event_type, event_data),
        data=event_data,
        source_event_type=str(source_event_type or ""),
        sequence=int(seq or 0),
        event_id=event_id or "",
    ).to_dict()
