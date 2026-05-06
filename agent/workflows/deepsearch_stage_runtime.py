from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Optional


@dataclass
class DeepSearchStageRecord:
    name: str
    status: str
    order: int
    started_at: str
    completed_at: str = ""
    duration_ms: float = 0.0
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    recovery_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value not in (None, "", [], {})
        }


class DeepSearchStageRuntime:
    def __init__(self, *, mode: str, run_id: str = "") -> None:
        self.mode = mode
        self.run_id = run_id
        self.started_at = datetime.now(UTC).isoformat()
        self._records: list[DeepSearchStageRecord] = []
        self._started_monotonic: dict[int, float] = {}

    def start(self, name: str, **inputs: Any) -> int:
        order = len(self._records) + 1
        record = DeepSearchStageRecord(
            name=name,
            status="running",
            order=order,
            started_at=datetime.now(UTC).isoformat(),
            inputs={key: value for key, value in inputs.items() if value not in (None, "", [], {})},
        )
        self._records.append(record)
        handle = order - 1
        self._started_monotonic[handle] = time.time()
        return handle

    def finish(self, handle: Optional[int], **outputs: Any) -> None:
        record = self._record(handle)
        if record is None:
            return
        record.status = "completed"
        record.completed_at = datetime.now(UTC).isoformat()
        record.duration_ms = self._duration_ms(handle)
        record.outputs.update(
            {key: value for key, value in outputs.items() if value not in (None, "", [], {})}
        )

    def fail(
        self,
        handle: Optional[int],
        error: Any,
        *,
        recovery_hint: str = "",
        **outputs: Any,
    ) -> None:
        record = self._record(handle)
        if record is None:
            return
        record.status = "failed"
        record.completed_at = datetime.now(UTC).isoformat()
        record.duration_ms = self._duration_ms(handle)
        text = str(error or "").strip() or "unknown error"
        record.errors.append(text)
        record.recovery_hint = recovery_hint or _default_recovery_hint(record.name)
        record.outputs.update(
            {key: value for key, value in outputs.items() if value not in (None, "", [], {})}
        )

    def skip(self, name: str, *, reason: str = "", **outputs: Any) -> None:
        order = len(self._records) + 1
        self._records.append(
            DeepSearchStageRecord(
                name=name,
                status="skipped",
                order=order,
                started_at=datetime.now(UTC).isoformat(),
                completed_at=datetime.now(UTC).isoformat(),
                outputs={
                    key: value
                    for key, value in {"reason": reason, **outputs}.items()
                    if value not in (None, "", [], {})
                },
            )
        )

    def event_payload(self, handle: Optional[int]) -> dict[str, Any]:
        record = self._record(handle)
        return record.to_dict() if record else {}

    def artifact(self) -> dict[str, Any]:
        records = [record.to_dict() for record in self._records]
        status_counts: dict[str, int] = {}
        for record in self._records:
            status_counts[record.status] = status_counts.get(record.status, 0) + 1
        failed = next((record for record in self._records if record.status == "failed"), None)
        running = [record.name for record in self._records if record.status == "running"]
        completed = [record.name for record in self._records if record.status == "completed"]
        return {
            "schema_version": 1,
            "mode": self.mode,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "stage_count": len(records),
            "status_counts": status_counts,
            "last_completed_stage": completed[-1] if completed else "",
            "failed_stage": failed.name if failed else "",
            "running_stages": running,
            "resumable": bool(failed or completed),
            "recovery_hint": failed.recovery_hint if failed else _next_recovery_hint(completed),
            "stages": records,
        }

    def _record(self, handle: Optional[int]) -> Optional[DeepSearchStageRecord]:
        if handle is None:
            return None
        if handle < 0 or handle >= len(self._records):
            return None
        return self._records[handle]

    def _duration_ms(self, handle: Optional[int]) -> float:
        started = self._started_monotonic.get(handle if handle is not None else -1)
        if started is None:
            return 0.0
        return round((time.time() - started) * 1000.0, 3)


def _default_recovery_hint(stage_name: str) -> str:
    if stage_name in {"worker_dispatch", "worker_search", "worker_compression"}:
        return "resume from completed worker runs and retry incomplete workers"
    if stage_name in {"evidence_build", "source_fetch", "prewrite_gap_followup"}:
        return "reuse collected search runs and rebuild evidence artifacts"
    if stage_name in {"writer", "sectioned_report"}:
        return "reuse evidence bundles and restart report writing"
    if stage_name in {"verifier", "citation_repair", "claim_grounding"}:
        return "reuse draft report and rerun verification/repair"
    return "resume from the last completed DeepSearch stage"


def _next_recovery_hint(completed: list[str]) -> str:
    if not completed:
        return "start from research brief"
    last = completed[-1]
    if last in {"research_brief", "brief_review", "supervisor_plan"}:
        return "continue with worker dispatch"
    if last in {"worker_dispatch", "worker_search", "worker_compression"}:
        return "continue with evidence build and synthesis"
    if last in {"evidence_build", "source_quality", "brief_coverage"}:
        return "continue with report writing"
    if last in {"writer", "sectioned_report"}:
        return "continue with verification and citation repair"
    return "session has a completed DeepSearch artifact snapshot"


def build_fallback_artifact(*, source_strategy: str, fallback_strategy: str, error: Any) -> dict[str, Any]:
    return {
        "source_strategy": source_strategy,
        "fallback_strategy": fallback_strategy,
        "reason": str(error or "").strip() or "unknown error",
        "timestamp": datetime.now(UTC).isoformat(),
        "recovery_hint": "inspect stage_runtime and retry the failed stage before falling back",
    }
