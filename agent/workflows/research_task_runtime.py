from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Optional

_TERMINAL_STATUSES = {"completed", "failed", "timed_out", "cancelled"}


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _stable_text(value: Any) -> str:
    return str(value or "").strip()


def _parse_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


@dataclass
class ResearchSubtaskRun:
    subtask_id: str
    worker_id: str
    context_id: str
    parent_id: str
    mode: str
    round_index: int
    topic: str
    focus: str
    queries: list[str]
    status: str = "pending"
    started_at: str = ""
    updated_at: str = ""
    completed_at: str = ""
    duration_seconds: float = 0.0
    result_count: int = 0
    evidence_count: int = 0
    raw_notes: list[str] = field(default_factory=list)
    compressed_summary: str = ""
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value not in (None, "", [], {})}


class ResearchTaskRuntime:
    def __init__(self, *, mode: str, parent_id: str = "deepsearch_supervisor") -> None:
        self.mode = mode
        self.parent_id = parent_id
        self._subtasks: dict[str, ResearchSubtaskRun] = {}
        self._events: list[dict[str, Any]] = []

    def register_tasks(self, tasks: Iterable[Any], *, metadata: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        return [self.register_task(task, metadata=metadata) for task in tasks]

    def register_task(self, task: Any, *, metadata: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        worker_id = _stable_text(getattr(task, "worker_id", ""))
        run = ResearchSubtaskRun(
            subtask_id=worker_id,
            worker_id=worker_id,
            context_id=_stable_text(getattr(task, "context_id", "")),
            parent_id=self.parent_id,
            mode=self.mode,
            round_index=int(getattr(task, "round_index", 0) or 0),
            topic=_stable_text(getattr(task, "topic", "")),
            focus=_stable_text(getattr(task, "focus", "")),
            queries=list(getattr(task, "queries", []) or []),
            updated_at=_utc_now_iso(),
            metadata=dict(metadata or {}),
        )
        self._subtasks[worker_id] = run
        self._record_event(run, "registered")
        return run.to_dict()

    def start_task(self, worker_id: str, *, started_at: Optional[str] = None, metadata: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        run = self._subtasks[worker_id]
        run.status = "running"
        run.started_at = started_at or _utc_now_iso()
        run.updated_at = run.started_at
        if metadata:
            run.metadata.update(metadata)
        self._record_event(run, "started")
        return run.to_dict()

    def complete_task(
        self,
        worker_id: str,
        *,
        result_count: int = 0,
        evidence_count: int = 0,
        compressed_summary: str = "",
        raw_notes: Optional[list[str]] = None,
        errors: Optional[list[str]] = None,
        completed_at: Optional[str] = None,
    ) -> dict[str, Any]:
        status = "failed" if errors else "completed"
        return self.finish_task(
            worker_id,
            status=status,
            result_count=result_count,
            evidence_count=evidence_count,
            compressed_summary=compressed_summary,
            raw_notes=raw_notes,
            errors=errors,
            completed_at=completed_at,
        )

    def finish_task(
        self,
        worker_id: str,
        *,
        status: str,
        result_count: int = 0,
        evidence_count: int = 0,
        compressed_summary: str = "",
        raw_notes: Optional[list[str]] = None,
        errors: Optional[list[str]] = None,
        completed_at: Optional[str] = None,
    ) -> dict[str, Any]:
        if status not in _TERMINAL_STATUSES:
            raise ValueError(f"Unsupported terminal status: {status}")
        run = self._subtasks[worker_id]
        run.status = status
        run.result_count = max(0, int(result_count or 0))
        run.evidence_count = max(0, int(evidence_count or 0))
        run.compressed_summary = compressed_summary or ""
        run.raw_notes = list(raw_notes or [])
        run.errors = list(errors or [])
        run.completed_at = completed_at or _utc_now_iso()
        run.updated_at = run.completed_at
        run.duration_seconds = _duration_seconds(run.started_at, run.completed_at)
        self._record_event(run, status)
        return run.to_dict()

    def to_artifact(self) -> dict[str, Any]:
        subtasks = [run.to_dict() for run in self._subtasks.values()]
        status_counts: dict[str, int] = {}
        for run in self._subtasks.values():
            status_counts[run.status] = status_counts.get(run.status, 0) + 1
        return {
            "schema_version": 1,
            "mode": self.mode,
            "parent_id": self.parent_id,
            "status_counts": status_counts,
            "subtask_count": len(subtasks),
            "subtasks": subtasks,
            "events": list(self._events),
        }

    def finish_open_tasks(self, *, status: str, error: str = "") -> list[dict[str, Any]]:
        finished: list[dict[str, Any]] = []
        for worker_id, run in list(self._subtasks.items()):
            if run.status in _TERMINAL_STATUSES:
                continue
            finished.append(
                self.finish_task(
                    worker_id,
                    status=status,
                    errors=[error] if error else [],
                )
            )
        return finished

    def _record_event(self, run: ResearchSubtaskRun, event_type: str) -> None:
        self._events.append(
            {
                "event_id": f"subtask_event_{len(self._events) + 1}",
                "type": event_type,
                "subtask_id": run.subtask_id,
                "worker_id": run.worker_id,
                "context_id": run.context_id,
                "round_index": run.round_index,
                "status": run.status,
                "timestamp": run.updated_at or _utc_now_iso(),
            }
        )


def _duration_seconds(started_at: str, completed_at: str) -> float:
    started = _parse_datetime(started_at)
    completed = _parse_datetime(completed_at)
    if not started or not completed:
        return 0.0
    return max(0.0, round((completed - started).total_seconds(), 3))
