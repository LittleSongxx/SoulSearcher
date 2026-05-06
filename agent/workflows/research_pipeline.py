from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class ResearchPipelineStage:
    name: str
    status: str
    item_count: int = 0
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value not in (None, "", [], {})
        }


@dataclass
class SubResearchFinding:
    subquestion: str
    summary: str
    worker_id: str = ""
    context_id: str = ""
    round_index: int = 0
    citations: list[str] = field(default_factory=list)
    confidence: float = 0.0
    gaps: list[str] = field(default_factory=list)
    follow_up_queries: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value not in (None, "", [], {})
        }


def build_supervisor_workers_pipeline_artifact(
    *,
    research_brief: dict[str, Any],
    task_runtime: dict[str, Any],
    worker_runs: list[dict[str, Any]],
    supervisor_decisions: list[dict[str, Any]],
    decision_log: list[dict[str, Any]],
    summary_notes: list[str],
    evidence_items: list[dict[str, Any]],
    claim_ledger: Optional[list[dict[str, Any]]] = None,
    quality_summary: Optional[dict[str, Any]] = None,
    final_report: str = "",
) -> dict[str, Any]:
    claim_ledger = list(claim_ledger or [])
    quality_summary = dict(quality_summary or {})
    runtime_status_counts = dict((task_runtime or {}).get("status_counts") or {})
    subtask_count = int((task_runtime or {}).get("subtask_count") or 0)
    completed_subtasks = int(runtime_status_counts.get("completed") or 0)
    failed_subtasks = int(runtime_status_counts.get("failed") or 0)
    compressed_research = _compressed_research_from_worker_runs(worker_runs)
    sub_research_findings = build_sub_research_findings(worker_runs)

    stages = [
        ResearchPipelineStage(
            name="research_brief",
            status="completed" if research_brief else "skipped",
            item_count=1 if research_brief else 0,
            outputs={
                "original_query": research_brief.get("original_query"),
                "clarified_goal": research_brief.get("clarified_goal"),
                "expected_field_count": len(
                    research_brief.get("expected_fields") or []
                ),
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
            status=_researcher_status(
                subtask_count, completed_subtasks, failed_subtasks
            ),
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
                "sub_research_findings": [
                    item.to_dict() for item in sub_research_findings
                ],
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
                "citation_coverage_score": quality_summary.get(
                    "citation_coverage_score"
                ),
                "unsupported_claim_count": quality_summary.get(
                    "claim_verifier_unsupported"
                ),
                "failed_gate_count": _failed_gate_count(quality_summary),
            },
        ),
    ]

    return {
        "schema_version": 1,
        "mode": "supervisor_workers",
        "stage_count": len(stages),
        "stages": [stage.to_dict() for stage in stages],
        "sub_research_findings": [item.to_dict() for item in sub_research_findings],
    }


def _researcher_status(
    subtask_count: int, completed_subtasks: int, failed_subtasks: int
) -> str:
    if subtask_count <= 0:
        return "skipped"
    if failed_subtasks > 0 and completed_subtasks <= 0:
        return "failed"
    if failed_subtasks > 0:
        return "partial"
    if completed_subtasks >= subtask_count:
        return "completed"
    return "running"


def _compressed_research_from_worker_runs(
    worker_runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    compressed: list[dict[str, Any]] = []
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


def build_sub_research_findings(
    worker_runs: list[dict[str, Any]],
) -> list[SubResearchFinding]:
    findings: list[SubResearchFinding] = []
    for run in worker_runs or []:
        summary = str(run.get("summary") or "").strip()
        topic = str(run.get("topic") or run.get("focus") or "").strip()
        if not summary and not topic:
            continue
        citations = _citations_from_run(run)
        findings.append(
            SubResearchFinding(
                subquestion=topic or str(run.get("worker_id") or "sub-research"),
                summary=summary,
                worker_id=str(run.get("worker_id") or ""),
                context_id=str(run.get("context_id") or ""),
                round_index=int(run.get("round_index") or 0),
                citations=citations,
                confidence=_finding_confidence(run, citations),
                gaps=(
                    [
                        str(item).strip()
                        for item in (run.get("gaps") or run.get("missing_topics") or [])
                        if str(item).strip()
                    ]
                    if isinstance(
                        run.get("gaps") or run.get("missing_topics") or [], list
                    )
                    else []
                ),
                follow_up_queries=(
                    [
                        str(item).strip()
                        for item in (
                            run.get("follow_up_queries") or run.get("queries") or []
                        )
                        if str(item).strip()
                    ][:5]
                    if isinstance(
                        run.get("follow_up_queries") or run.get("queries") or [], list
                    )
                    else []
                ),
            )
        )
    return findings


def _citations_from_run(run: dict[str, Any]) -> list[str]:
    citations: list[str] = []
    for key in ("citations", "citation_ids", "evidence_urls"):
        value = run.get(key)
        if isinstance(value, list):
            citations.extend(str(item).strip() for item in value if str(item).strip())
    for source in run.get("sources") or []:
        if not isinstance(source, dict):
            continue
        citation = str(
            source.get("citation_id")
            or source.get("url")
            or source.get("document_id")
            or ""
        ).strip()
        if citation:
            citations.append(citation)
    seen = set()
    output: list[str] = []
    for citation in citations:
        if citation in seen:
            continue
        seen.add(citation)
        output.append(citation)
    return output[:12]


def _finding_confidence(run: dict[str, Any], citations: list[str]) -> float:
    if isinstance(run.get("confidence"), (int, float)):
        return round(max(0.0, min(1.0, float(run.get("confidence")))), 3)
    result_count = int(run.get("result_count") or 0)
    evidence_count = int(run.get("evidence_count") or 0)
    score = 0.35
    if result_count > 0:
        score += 0.2
    if evidence_count > 0:
        score += 0.25
    if citations:
        score += 0.2
    return round(max(0.0, min(1.0, score)), 3)


def _failed_gate_count(quality_summary: dict[str, Any]) -> int:
    value = quality_summary.get("failed_gate_count")
    if isinstance(value, int):
        return value
    failed_gates = quality_summary.get("failed_gates")
    if isinstance(failed_gates, list):
        return len(failed_gates)
    return 0
