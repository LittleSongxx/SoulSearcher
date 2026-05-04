from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ResearchPipelineStage:
    name: str
    status: str
    item_count: int = 0
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value not in (None, "", [], {})}


def build_supervisor_workers_pipeline_artifact(
    *,
    research_brief: Dict[str, Any],
    task_runtime: Dict[str, Any],
    worker_runs: List[Dict[str, Any]],
    supervisor_decisions: List[Dict[str, Any]],
    decision_log: List[Dict[str, Any]],
    summary_notes: List[str],
    evidence_items: List[Dict[str, Any]],
    claim_ledger: Optional[List[Dict[str, Any]]] = None,
    quality_summary: Optional[Dict[str, Any]] = None,
    final_report: str = "",
) -> Dict[str, Any]:
    claim_ledger = list(claim_ledger or [])
    quality_summary = dict(quality_summary or {})
    runtime_status_counts = dict((task_runtime or {}).get("status_counts") or {})
    subtask_count = int((task_runtime or {}).get("subtask_count") or 0)
    completed_subtasks = int(runtime_status_counts.get("completed") or 0)
    failed_subtasks = int(runtime_status_counts.get("failed") or 0)
    compressed_research = _compressed_research_from_worker_runs(worker_runs)

    stages = [
        ResearchPipelineStage(
            name="research_brief",
            status="completed" if research_brief else "skipped",
            item_count=1 if research_brief else 0,
            outputs={
                "original_query": research_brief.get("original_query"),
                "clarified_goal": research_brief.get("clarified_goal"),
                "expected_field_count": len(research_brief.get("expected_fields") or []),
            },
        ),
        ResearchPipelineStage(
            name="supervisor",
            status="completed" if supervisor_decisions else "skipped",
            item_count=len(supervisor_decisions or []),
            outputs={
                "round_count": len(supervisor_decisions or []),
                "last_action": (supervisor_decisions or [{}])[-1].get("action"),
                "decision_log_count": len(decision_log or []),
            },
        ),
        ResearchPipelineStage(
            name="researcher",
            status=_researcher_status(subtask_count, completed_subtasks, failed_subtasks),
            item_count=subtask_count,
            outputs={
                "status_counts": runtime_status_counts,
                "worker_run_count": len(worker_runs or []),
                "evidence_item_count": len(evidence_items or []),
            },
        ),
        ResearchPipelineStage(
            name="compression",
            status="completed" if compressed_research else "skipped",
            item_count=len(compressed_research),
            outputs={
                "compressed_research_count": len(compressed_research),
                "summary_note_count": len(summary_notes or []),
                "compressed_research": compressed_research,
            },
        ),
        ResearchPipelineStage(
            name="writer",
            status="completed" if final_report else "skipped",
            item_count=1 if final_report else 0,
            inputs={"summary_note_count": len(summary_notes or [])},
            outputs={"report_length": len(final_report or "")},
        ),
        ResearchPipelineStage(
            name="verifier",
            status="completed" if quality_summary or claim_ledger else "skipped",
            item_count=len(claim_ledger),
            outputs={
                "claim_ledger_count": len(claim_ledger),
                "citation_coverage_score": quality_summary.get("citation_coverage_score"),
                "unsupported_claim_count": quality_summary.get("claim_verifier_unsupported"),
                "failed_gate_count": _failed_gate_count(quality_summary),
            },
        ),
    ]

    return {
        "schema_version": 1,
        "mode": "supervisor_workers",
        "stage_count": len(stages),
        "stages": [stage.to_dict() for stage in stages],
    }


def _researcher_status(subtask_count: int, completed_subtasks: int, failed_subtasks: int) -> str:
    if subtask_count <= 0:
        return "skipped"
    if failed_subtasks > 0 and completed_subtasks <= 0:
        return "failed"
    if failed_subtasks > 0:
        return "partial"
    if completed_subtasks >= subtask_count:
        return "completed"
    return "running"


def _compressed_research_from_worker_runs(worker_runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    compressed: List[Dict[str, Any]] = []
    for run in worker_runs or []:
        summary = str(run.get("summary") or "").strip()
        if not summary:
            continue
        compressed.append(
            {
                "worker_id": run.get("worker_id"),
                "context_id": run.get("context_id"),
                "round_index": run.get("round_index"),
                "focus": run.get("focus"),
                "summary": summary,
            }
        )
    return compressed


def _failed_gate_count(quality_summary: Dict[str, Any]) -> int:
    value = quality_summary.get("failed_gate_count")
    if isinstance(value, int):
        return value
    failed_gates = quality_summary.get("failed_gates")
    if isinstance(failed_gates, list):
        return len(failed_gates)
    return 0
