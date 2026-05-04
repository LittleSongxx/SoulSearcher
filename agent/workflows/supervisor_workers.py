from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from agent.workflows.query_strategy import backfill_diverse_queries
from agent.workflows.research_brief import ResearchBrief


@dataclass
class WorkerTask:
    worker_id: str
    topic: str
    focus: str
    queries: List[str]
    round_index: int
    context_id: str
    status: str = "pending"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WorkerRun:
    worker_id: str
    context_id: str
    topic: str
    focus: str
    queries: List[str]
    round_index: int
    result_count: int = 0
    evidence_count: int = 0
    summary: str = ""
    provider_breakdown: Dict[str, int] = field(default_factory=dict)
    status: str = "completed"
    started_at: str = ""
    completed_at: str = ""
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value not in (None, "", [], {})}


@dataclass
class SupervisorDecision:
    round_index: int
    action: str
    reason: str
    missing_topics: List[str] = field(default_factory=list)
    next_worker_topics: List[str] = field(default_factory=list)
    failed_gates: List[str] = field(default_factory=list)
    quality_snapshot: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value not in (None, "", [], {})}


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:10]}"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _unique(items: Iterable[str]) -> List[str]:
    output: List[str] = []
    seen = set()
    for item in items or []:
        text = _text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def _provider_breakdown(results: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for result in results or []:
        if not isinstance(result, dict):
            continue
        provider = _text(result.get("provider") or result.get("source_type") or "unknown")
        counts[provider] = counts.get(provider, 0) + 1
    return counts


def _role_candidates(brief: ResearchBrief) -> List[str]:
    roles: List[str] = []
    constraints = brief.constraints if isinstance(brief.constraints, dict) else {}
    source_constraints = constraints.get("source_constraints") if isinstance(constraints.get("source_constraints"), dict) else {}
    judge_rubric = constraints.get("judge_rubric") if isinstance(constraints.get("judge_rubric"), dict) else {}
    if source_constraints or brief.preferred_sources:
        roles.append("source_triage")
    if bool(judge_rubric.get("requires_citations_for_claims")) or bool(brief.expected_fields):
        roles.append("claim_verification")
    if brief.freshness_requirement and brief.freshness_requirement not in {"", "not_required"}:
        roles.append("freshness")
    if any("timeline" in _text(field).lower() or "时间线" in _text(field) for field in brief.expected_fields or []):
        roles.append("timeline")
    if any("comparison" in _text(field).lower() or "比较" in _text(field) or "对比" in _text(field) for field in brief.expected_fields or []):
        roles.append("comparison_table")
    return roles


def _focus_query_suffix(focus: str) -> str:
    key = _text(focus).lower()
    if key == "source_triage":
        return "official primary sources independent benchmarks reliable evidence"
    if key == "claim_verification":
        return "key claims evidence citations source verification"
    if key == "comparison_table":
        return "comparison dimensions table tradeoffs metrics"
    if key == "timeline":
        return "timeline dates official updates"
    return focus


def build_worker_tasks(
    *,
    brief: ResearchBrief,
    round_index: int,
    max_workers: int,
    queries_per_worker: int,
    historical_queries: Optional[List[str]] = None,
    missing_topics: Optional[List[str]] = None,
) -> List[WorkerTask]:
    topic = brief.clarified_goal or brief.original_query
    focus_candidates = _unique(
        list(missing_topics or [])
        + _role_candidates(brief)
        + list(brief.expected_fields or [])
        + ["evidence", "risks", "implementation", "freshness"]
    )
    if not focus_candidates:
        focus_candidates = [topic]

    tasks: List[WorkerTask] = []
    used_queries = list(historical_queries or [])
    worker_count = max(1, int(max_workers or 1))
    query_count = max(1, int(queries_per_worker or 1))
    for idx, focus in enumerate(focus_candidates[:worker_count], 1):
        suffix = _focus_query_suffix(focus)
        base = f"{topic} {suffix}" if suffix and suffix not in topic else topic
        queries = backfill_diverse_queries(base, [base], used_queries, query_count)
        queries = _unique(queries)[:query_count]
        used_queries.extend(queries)
        worker_id = _stable_id("worker", round_index, idx, focus)
        tasks.append(
            WorkerTask(
                worker_id=worker_id,
                topic=base,
                focus=focus,
                queries=queries or [base],
                round_index=round_index,
                context_id=f"ctx_{worker_id}",
            )
        )
    return tasks


def build_worker_run(
    *,
    task: WorkerTask,
    results: List[Dict[str, Any]],
    evidence_items: List[Dict[str, Any]],
    summary: str,
    errors: Optional[List[str]] = None,
    started_at: str = "",
) -> WorkerRun:
    completed_at = datetime.now(timezone.utc).isoformat()
    return WorkerRun(
        worker_id=task.worker_id,
        context_id=task.context_id,
        topic=task.topic,
        focus=task.focus,
        queries=task.queries,
        round_index=task.round_index,
        result_count=len(results or []),
        evidence_count=len(evidence_items or []),
        summary=summary or "",
        provider_breakdown=_provider_breakdown(results or []),
        status="failed" if errors else "completed",
        started_at=started_at,
        completed_at=completed_at,
        errors=list(errors or []),
    )


def decide_supervisor_next_step(
    *,
    round_index: int,
    max_rounds: int,
    worker_runs: List[Dict[str, Any]],
    gate_payload: List[Dict[str, Any]],
    diagnostics: Dict[str, Any],
) -> SupervisorDecision:
    failed_gates = [
        _text(gate.get("name"))
        for gate in gate_payload or []
        if isinstance(gate, dict) and gate.get("status") == "fail"
    ]
    missing_topics: List[str] = []
    for gate in gate_payload or []:
        if not isinstance(gate, dict):
            continue
        details = gate.get("details") if isinstance(gate.get("details"), dict) else {}
        for item in details.get("missing_dimensions") or []:
            text = _text(item)
            if text:
                missing_topics.append(text)
    missing_topics = _unique(missing_topics)
    total_results = sum(int(run.get("result_count") or 0) for run in worker_runs or [] if isinstance(run, dict))
    quality_snapshot = {
        "query_coverage_score": diagnostics.get("query_coverage_score"),
        "freshness_warning": diagnostics.get("freshness_warning"),
        "failed_gate_count": len(failed_gates),
        "worker_count": len(worker_runs or []),
        "result_count": total_results,
    }

    if round_index >= max(1, int(max_rounds or 1)):
        return SupervisorDecision(
            round_index=round_index,
            action="synthesize",
            reason="maximum supervisor rounds reached",
            missing_topics=missing_topics,
            failed_gates=failed_gates,
            quality_snapshot=quality_snapshot,
        )
    if total_results == 0:
        return SupervisorDecision(
            round_index=round_index,
            action="continue",
            reason="workers returned no evidence",
            missing_topics=missing_topics or ["evidence"],
            next_worker_topics=missing_topics or ["evidence"],
            failed_gates=failed_gates,
            quality_snapshot=quality_snapshot,
        )
    if missing_topics and failed_gates:
        return SupervisorDecision(
            round_index=round_index,
            action="continue",
            reason="quality gates identified missing dimensions",
            missing_topics=missing_topics,
            next_worker_topics=missing_topics,
            failed_gates=failed_gates,
            quality_snapshot=quality_snapshot,
        )
    if failed_gates:
        next_topics = []
        if "citation_coverage" in failed_gates:
            next_topics.append("citation_evidence")
        if "claim_verifier" in failed_gates:
            next_topics.append("claim_verification")
        if "freshness" in failed_gates:
            next_topics.append("freshness")
        if next_topics:
            next_topics = _unique(next_topics)
            return SupervisorDecision(
                round_index=round_index,
                action="continue",
                reason="quality gates require additional verification",
                missing_topics=next_topics,
                next_worker_topics=next_topics,
                failed_gates=failed_gates,
                quality_snapshot=quality_snapshot,
            )
    return SupervisorDecision(
        round_index=round_index,
        action="synthesize",
        reason="worker evidence is sufficient for synthesis",
        failed_gates=failed_gates,
        quality_snapshot=quality_snapshot,
    )


def build_intermediate_steps(
    *,
    worker_runs: List[Dict[str, Any]],
    supervisor_decisions: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = []
    for run in worker_runs or []:
        if not isinstance(run, dict):
            continue
        steps.append(
            {
                "id": _stable_id("step", run.get("worker_id"), run.get("completed_at")),
                "type": "worker_run",
                "worker_id": run.get("worker_id"),
                "context_id": run.get("context_id"),
                "round_index": run.get("round_index"),
                "title": run.get("topic") or run.get("focus"),
                "status": run.get("status", "completed"),
                "result_count": run.get("result_count", 0),
                "evidence_count": run.get("evidence_count", 0),
                "timestamp": run.get("completed_at"),
            }
        )
    for decision in supervisor_decisions or []:
        if not isinstance(decision, dict):
            continue
        steps.append(
            {
                "id": _stable_id("step", "supervisor", decision.get("round_index"), decision.get("action")),
                "type": "supervisor_decision",
                "round_index": decision.get("round_index"),
                "title": decision.get("action"),
                "status": decision.get("action"),
                "reason": decision.get("reason"),
                "missing_topics": decision.get("missing_topics", []),
            }
        )
    for idx, step in enumerate(steps, 1):
        step["order"] = idx
    return steps
