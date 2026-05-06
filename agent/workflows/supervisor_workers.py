from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Optional

from agent.workflows.query_strategy import backfill_diverse_queries
from agent.workflows.research_brief import ResearchBrief


@dataclass
class WorkerTask:
    worker_id: str
    topic: str
    focus: str
    queries: list[str]
    round_index: int
    context_id: str
    status: str = "pending"
    depth: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorkerRun:
    worker_id: str
    context_id: str
    topic: str
    focus: str
    queries: list[str]
    round_index: int
    result_count: int = 0
    evidence_count: int = 0
    summary: str = ""
    raw_notes: list[str] = field(default_factory=list)
    compressed_research: str = ""
    learnings: list[str] = field(default_factory=list)
    follow_up_questions: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    budget_snapshot: dict[str, Any] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)
    compression_stats: dict[str, Any] = field(default_factory=dict)
    depth: int = 0
    provider_breakdown: dict[str, int] = field(default_factory=dict)
    status: str = "completed"
    started_at: str = ""
    completed_at: str = ""
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value not in (None, "", [], {})
        }


@dataclass
class SupervisorDecision:
    round_index: int
    action: str
    reason: str
    missing_topics: list[str] = field(default_factory=list)
    next_worker_topics: list[str] = field(default_factory=list)
    failed_gates: list[str] = field(default_factory=list)
    quality_snapshot: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value not in (None, "", [], {})
        }


@dataclass
class SupervisorThinkStep:
    round_index: int
    reflection: str = ""
    knowledge_gaps: list[str] = field(default_factory=list)
    confidence: float = 0.0
    next_focus: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value not in (None, "", [], {})
        }


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:10]}"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _unique(items: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen = set()
    for item in items or []:
        text = _text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def _provider_breakdown(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results or []:
        if not isinstance(result, dict):
            continue
        provider = _text(
            result.get("provider") or result.get("source_type") or "unknown"
        )
        counts[provider] = counts.get(provider, 0) + 1
    return counts


def _role_candidates(brief: ResearchBrief) -> list[str]:
    roles: list[str] = []
    constraints = brief.constraints if isinstance(brief.constraints, dict) else {}
    source_constraints = (
        constraints.get("source_constraints")
        if isinstance(constraints.get("source_constraints"), dict)
        else {}
    )
    judge_rubric = (
        constraints.get("judge_rubric")
        if isinstance(constraints.get("judge_rubric"), dict)
        else {}
    )
    if source_constraints or brief.preferred_sources:
        roles.append("source_triage")
    if bool(judge_rubric.get("requires_citations_for_claims")) or bool(
        brief.expected_fields
    ):
        roles.append("claim_verification")
    if brief.freshness_requirement and brief.freshness_requirement not in {
        "",
        "not_required",
    }:
        roles.append("freshness")
    if any(
        "timeline" in _text(field).lower() or "时间线" in _text(field)
        for field in brief.expected_fields or []
    ):
        roles.append("timeline")
    if any(
        "comparison" in _text(field).lower()
        or "比较" in _text(field)
        or "对比" in _text(field)
        for field in brief.expected_fields or []
    ):
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
    historical_queries: Optional[list[str]] = None,
    missing_topics: Optional[list[str]] = None,
) -> list[WorkerTask]:
    topic = brief.clarified_goal or brief.original_query
    focus_candidates = _unique(
        list(missing_topics or [])
        + _role_candidates(brief)
        + list(brief.expected_fields or [])
        + ["evidence", "risks", "implementation", "freshness"]
    )
    if not focus_candidates:
        focus_candidates = [topic]

    tasks: list[WorkerTask] = []
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
    results: list[dict[str, Any]],
    evidence_items: list[dict[str, Any]],
    summary: str,
    errors: Optional[list[str]] = None,
    started_at: str = "",
    raw_notes: Optional[list[str]] = None,
    tool_calls: Optional[list[dict[str, Any]]] = None,
    budget_snapshot: Optional[dict[str, Any]] = None,
    gaps: Optional[list[str]] = None,
    compression_stats: Optional[dict[str, Any]] = None,
    depth: int = 0,
) -> WorkerRun:
    completed_at = datetime.now(UTC).isoformat()
    citations = _citations_from_results(results or [], evidence_items or [])
    sources = _sources_from_results(results or [], evidence_items or [])
    compressed_research = summary or ""
    learnings = _learnings_from_summary(summary)
    follow_up_questions = _unique(task.queries or [])[:5]
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
        raw_notes=list(raw_notes or _raw_notes_from_results(results or [])),
        compressed_research=compressed_research,
        learnings=learnings,
        follow_up_questions=follow_up_questions,
        citations=citations,
        sources=sources,
        tool_calls=list(tool_calls or _tool_calls_from_queries(task.queries or [])),
        confidence=_worker_confidence(results or [], evidence_items or [], citations),
        budget_snapshot=dict(budget_snapshot or {}),
        gaps=list(gaps or []),
        compression_stats=dict(compression_stats or {}),
        depth=depth,
        provider_breakdown=_provider_breakdown(results or []),
        status="failed" if errors else "completed",
        started_at=started_at,
        completed_at=completed_at,
        errors=list(errors or []),
    )


def _raw_notes_from_results(results: list[dict[str, Any]]) -> list[str]:
    notes: list[str] = []
    for result in results[:8]:
        if not isinstance(result, dict):
            continue
        title = _text(result.get("title") or result.get("name"))
        snippet = _text(
            result.get("snippet") or result.get("content") or result.get("body")
        )
        url = _text(result.get("url") or result.get("href"))
        note = " | ".join(part for part in (title, snippet[:500], url) if part)
        if note:
            notes.append(note)
    return notes


def _tool_calls_from_queries(queries: list[str]) -> list[dict[str, Any]]:
    return [
        {"tool": "evidence_provider_search", "query": query}
        for query in _unique(queries or [])
    ]


def _citations_from_results(
    results: list[dict[str, Any]], evidence_items: list[dict[str, Any]]
) -> list[str]:
    citations: list[str] = []
    for result in results or []:
        if not isinstance(result, dict):
            continue
        citation = _text(
            result.get("citation_id")
            or result.get("url")
            or result.get("href")
            or result.get("document_id")
        )
        if citation:
            citations.append(citation)
    for item in evidence_items or []:
        if not isinstance(item, dict):
            continue
        citation = _text(
            item.get("citation_id")
            or item.get("url")
            or item.get("source_url")
            or item.get("document_id")
            or item.get("id")
        )
        if citation:
            citations.append(citation)
    return _unique(citations)[:20]


def _sources_from_results(
    results: list[dict[str, Any]], evidence_items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    seen = set()
    for result in results or []:
        if not isinstance(result, dict):
            continue
        key = _text(
            result.get("url")
            or result.get("href")
            or result.get("document_id")
            or result.get("title")
        )
        if not key or key in seen:
            continue
        seen.add(key)
        sources.append(
            _compact_source(
                title=result.get("title") or result.get("name"),
                url=result.get("url") or result.get("href"),
                document_id=result.get("document_id") or result.get("id"),
                provider=result.get("provider") or result.get("source_type"),
                citation_id=result.get("citation_id"),
            )
        )
    for item in evidence_items or []:
        if not isinstance(item, dict):
            continue
        key = _text(
            item.get("url")
            or item.get("source_url")
            or item.get("document_id")
            or item.get("id")
        )
        if not key or key in seen:
            continue
        seen.add(key)
        sources.append(
            _compact_source(
                title=item.get("title"),
                url=item.get("url") or item.get("source_url"),
                document_id=item.get("document_id") or item.get("id"),
                provider=item.get("provider") or item.get("source_type"),
                citation_id=item.get("citation_id"),
            )
        )
    return sources[:20]


def _compact_source(**values: Any) -> dict[str, Any]:
    return {
        key: value for key, value in values.items() if value not in (None, "", [], {})
    }


def _learnings_from_summary(summary: str) -> list[str]:
    text = _text(summary)
    if not text:
        return []
    candidates = []
    for line in text.splitlines():
        stripped = line.strip(" -•\t")
        if len(stripped) >= 20:
            candidates.append(stripped)
    if not candidates:
        candidates = [text[:500]]
    return _unique(candidates)[:6]


def _worker_confidence(
    results: list[dict[str, Any]],
    evidence_items: list[dict[str, Any]],
    citations: list[str],
) -> float:
    score = 0.25
    score += min(0.3, len(results or []) * 0.03)
    score += min(0.25, len(evidence_items or []) * 0.05)
    score += min(0.2, len(citations or []) * 0.03)
    return round(max(0.0, min(1.0, score)), 3)


def decide_supervisor_next_step(
    *,
    round_index: int,
    max_rounds: int,
    worker_runs: list[dict[str, Any]],
    gate_payload: list[dict[str, Any]],
    diagnostics: dict[str, Any],
) -> SupervisorDecision:
    failed_gates = [
        _text(gate.get("name"))
        for gate in gate_payload or []
        if isinstance(gate, dict) and gate.get("status") == "fail"
    ]
    missing_topics: list[str] = []
    for gate in gate_payload or []:
        if not isinstance(gate, dict):
            continue
        details = gate.get("details") if isinstance(gate.get("details"), dict) else {}
        for item in details.get("missing_dimensions") or []:
            text = _text(item)
            if text:
                missing_topics.append(text)
    missing_topics = _unique(missing_topics)
    total_results = sum(
        int(run.get("result_count") or 0)
        for run in worker_runs or []
        if isinstance(run, dict)
    )
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
    worker_runs: list[dict[str, Any]],
    supervisor_decisions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
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
                "id": _stable_id(
                    "step",
                    "supervisor",
                    decision.get("round_index"),
                    decision.get("action"),
                ),
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


def build_worker_orchestration_artifact(
    *,
    worker_runs: list[dict[str, Any]],
    supervisor_decisions: list[dict[str, Any]],
    parallel_workers: int,
) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    round_counts: dict[str, int] = {}
    partial_results: list[dict[str, Any]] = []
    failed_workers: list[dict[str, Any]] = []
    for run in worker_runs or []:
        if not isinstance(run, dict):
            continue
        status = _text(run.get("status") or "completed") or "completed"
        status_counts[status] = status_counts.get(status, 0) + 1
        round_key = str(run.get("round_index") or 0)
        round_counts[round_key] = round_counts.get(round_key, 0) + 1
        partial_results.append(
            {
                key: value
                for key, value in {
                    "worker_id": run.get("worker_id"),
                    "context_id": run.get("context_id"),
                    "round_index": run.get("round_index"),
                    "focus": run.get("focus"),
                    "status": status,
                    "result_count": run.get("result_count", 0),
                    "evidence_count": run.get("evidence_count", 0),
                    "completed_at": run.get("completed_at"),
                }.items()
                if value not in (None, "", [], {})
            }
        )
        if status == "failed" or run.get("errors"):
            failed_workers.append(
                {
                    "worker_id": run.get("worker_id"),
                    "round_index": run.get("round_index"),
                    "errors": run.get("errors") or [],
                }
            )
    return {
        "schema_version": 1,
        "dispatch_model": "parallel_batch" if parallel_workers > 1 else "sequential",
        "parallel_workers": max(1, int(parallel_workers or 1)),
        "worker_count": len(worker_runs or []),
        "status_counts": status_counts,
        "round_counts": round_counts,
        "partial_result_count": len(partial_results),
        "partial_results": partial_results,
        "failed_workers": failed_workers,
        "supervisor_decision_count": len(supervisor_decisions or []),
        "supports_partial_results": True,
        "supports_dynamic_spawn": False,
        "supports_midflight_cancel": False,
    }


def build_branch_diagnostics_artifact(
    *,
    worker_runs: list[dict[str, Any]],
    evidence_items: list[dict[str, Any]],
) -> dict[str, Any]:
    focus_counts: dict[str, int] = {}
    branch_evidence_counts: dict[str, int] = {}
    branch_gap_counts: dict[str, int] = {}
    conflict_hints: list[dict[str, Any]] = []
    for run in worker_runs or []:
        if not isinstance(run, dict):
            continue
        focus = _text(run.get("focus") or run.get("topic") or run.get("worker_id") or "branch")
        focus_key = focus.lower()
        focus_counts[focus_key] = focus_counts.get(focus_key, 0) + 1
        worker_id = _text(run.get("worker_id") or focus_key)
        branch_evidence_counts[worker_id] = int(run.get("evidence_count") or 0)
        branch_gap_counts[worker_id] = len(run.get("gaps") or [])
        errors = run.get("errors") or []
        if errors:
            conflict_hints.append(
                {
                    "worker_id": worker_id,
                    "kind": "worker_error",
                    "details": errors[:3] if isinstance(errors, list) else [str(errors)],
                }
            )
    duplicate_focus_count = sum(max(0, count - 1) for count in focus_counts.values())
    evidence_by_url: dict[str, int] = {}
    for item in evidence_items or []:
        if not isinstance(item, dict):
            continue
        url = _text(item.get("url") or item.get("source_url") or item.get("document_id"))
        if url:
            evidence_by_url[url] = evidence_by_url.get(url, 0) + 1
    duplicate_evidence_urls = [url for url, count in evidence_by_url.items() if count > 1][:10]
    if duplicate_evidence_urls:
        conflict_hints.append(
            {
                "kind": "duplicate_evidence",
                "details": duplicate_evidence_urls,
            }
        )
    return {
        "schema_version": 1,
        "branch_count": len(worker_runs or []),
        "duplicate_focus_count": duplicate_focus_count,
        "branch_evidence_counts": branch_evidence_counts,
        "branch_gap_counts": branch_gap_counts,
        "conflict_hint_count": len(conflict_hints),
        "conflict_hints": conflict_hints[:12],
        "merge_strategy": "deduplicate_by_source_and_focus",
    }


def build_intermediate_report(
    *,
    worker_runs: list[dict[str, Any]],
    summary_notes: list[str],
    evidence_items: list[dict[str, Any]],
    round_index: int,
    topic: str = "",
) -> dict[str, Any]:
    total_results = sum(
        int(run.get("result_count") or 0)
        for run in worker_runs or []
        if isinstance(run, dict)
    )
    total_evidence = sum(
        int(run.get("evidence_count") or 0)
        for run in worker_runs or []
        if isinstance(run, dict)
    )
    source_urls = _unique(
        _text(item.get("url") or item.get("source_url") or item.get("href") or "")
        for item in evidence_items or []
        if isinstance(item, dict)
    )[:20]
    findings = "\n\n".join(summary_notes or [])
    key_learnings: list[str] = []
    for run in worker_runs or []:
        if not isinstance(run, dict):
            continue
        for learning in run.get("learnings") or []:
            text = _text(learning)
            if text and text not in key_learnings:
                key_learnings.append(text)
    key_learnings = key_learnings[:15]

    report_text = f"# Intermediate Research Report (Round {round_index})\n\n"
    if topic:
        report_text += f"**Topic:** {topic}\n\n"
    report_text += f"**Evidence collected:** {total_results} results, {total_evidence} evidence items\n\n"
    if key_learnings:
        report_text += "## Key Findings\n\n"
        for learning in key_learnings:
            report_text += f"- {learning}\n"
        report_text += "\n"
    if findings:
        report_text += "## Detailed Notes\n\n"
        report_text += findings[:4000]
        report_text += "\n\n"
    if source_urls:
        report_text += "## Sources\n\n"
        for idx, url in enumerate(source_urls[:10], 1):
            report_text += f"{idx}. {url}\n"

    return {
        "round_index": round_index,
        "topic": topic,
        "report_text": report_text,
        "total_results": total_results,
        "total_evidence": total_evidence,
        "key_learnings": key_learnings,
        "source_count": len(source_urls),
        "completion_estimate": min(1.0, round(total_results / max(1, 20) * 0.5 + total_evidence / max(1, 40) * 0.5, 3)),
        "timestamp": datetime.now(UTC).isoformat(),
    }
