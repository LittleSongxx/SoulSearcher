from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from statistics import mean
from typing import Any, Optional

from eval.deep_research_benchmark.schemas import MetricSummary


def percentile(values: Iterable[float], pct: float) -> Optional[float]:
    cleaned = sorted(float(value) for value in values if value is not None)
    if not cleaned:
        return None
    if len(cleaned) == 1:
        return round(cleaned[0], 3)
    rank = (len(cleaned) - 1) * pct
    low = int(rank)
    high = min(low + 1, len(cleaned) - 1)
    weight = rank - low
    value = cleaned[low] * (1 - weight) + cleaned[high] * weight
    return round(value, 3)


def _load_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _avg(values: list[float]) -> Optional[float]:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _count_evidence(results: list[dict[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for item in results:
        evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
        value = evidence.get(key) if isinstance(evidence, dict) else None
        if isinstance(value, list):
            values.append(float(len(value)))
    return values


def _claim_counts(results: list[dict[str, Any]], status: str | None = None) -> list[float]:
    values: list[float] = []
    for item in results:
        evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
        claims = evidence.get("claims") if isinstance(evidence, dict) else None
        if not isinstance(claims, list):
            continue
        if status is None:
            values.append(float(len(claims)))
        else:
            values.append(float(sum(1 for claim in claims if isinstance(claim, dict) and claim.get("status") == status)))
    return values


def _task_language(task: dict[str, Any], result: dict[str, Any]) -> str:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    language = str(metadata.get("language") or "").strip()
    if language:
        return language
    query = str(task.get("query") or result.get("query") or "")
    return "zh" if any("\u4e00" <= char <= "\u9fff" for char in query) else "en"


def _group_breakdown(
    results: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    tasks_by_id: dict[str, dict[str, Any]],
    key: str,
) -> dict[str, Any]:
    report_by_case = {s.get("case_id"): s for s in scores if s.get("judge_type") == "report" and s.get("status") == "scored"}
    claim_by_case = {s.get("case_id"): s for s in scores if s.get("judge_type") == "claim" and s.get("status") == "scored"}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        case_id = str(result.get("case_id") or "")
        task = tasks_by_id.get(case_id, {})
        if key == "language":
            group = _task_language(task, result)
        else:
            group = str(task.get("domain") or "unknown")
        grouped.setdefault(group, []).append(result)

    output: dict[str, Any] = {}
    for group, group_results in sorted(grouped.items()):
        case_ids = {item.get("case_id") for item in group_results}
        report_scores = [report_by_case[case_id] for case_id in case_ids if case_id in report_by_case]
        claim_scores = [claim_by_case[case_id] for case_id in case_ids if case_id in claim_by_case]
        unsupported = [float(s["score"]) for s in claim_scores if isinstance(s.get("score"), (int, float))]
        output[group] = {
            "total_cases": len(group_results),
            "completed_cases": sum(1 for item in group_results if item.get("status") == "completed"),
            "rubric_pass_rate": (
                round(sum(1 for score in report_scores if bool(score.get("passed"))) / len(report_scores), 4)
                if report_scores
                else None
            ),
            "unsupported_claim_rate": _avg(unsupported),
        }
    return output


def summarize_results(
    results: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    tasks: Optional[list[dict[str, Any]]] = None,
) -> MetricSummary:
    total = len(results)
    completed = [item for item in results if item.get("status") == "completed"]
    failed = [item for item in results if item.get("status") == "failed"]
    timeout = [item for item in results if item.get("status") == "timeout"]

    stable = []
    for item in completed:
        evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
        sources = evidence.get("sources") if isinstance(evidence, dict) else []
        if int(item.get("final_report_chars") or 0) >= 800 and isinstance(sources, list) and sources:
            stable.append(item)

    report_scores = [s for s in scores if s.get("judge_type") == "report" and s.get("status") == "scored"]
    citation_scores = [
        s for s in scores if s.get("judge_type") == "citation" and s.get("status") == "scored"
    ]
    claim_scores = [s for s in scores if s.get("judge_type") == "claim" and s.get("status") == "scored"]
    hallucination_scores = [s for s in scores if s.get("judge_type") == "hallucination" and s.get("status") == "scored"]

    citation_accuracy_values = [
        float(s["score"]) for s in citation_scores if isinstance(s.get("score"), (int, float))
    ]
    unsupported_values = [
        float(s["score"]) for s in claim_scores if isinstance(s.get("score"), (int, float))
    ]
    effective_sources = []
    for score in citation_scores:
        details = score.get("details") if isinstance(score.get("details"), dict) else {}
        value = details.get("effective_unique_sources")
        if isinstance(value, (int, float)):
            effective_sources.append(float(value))

    report_evidence_quality = []
    report_depth = []
    for score in report_scores:
        details = score.get("details") if isinstance(score.get("details"), dict) else {}
        dimensions = details.get("dimensions") if isinstance(details.get("dimensions"), dict) else {}
        evidence_quality = dimensions.get("evidence_quality")
        depth = dimensions.get("depth")
        if isinstance(evidence_quality, (int, float)):
            report_evidence_quality.append(float(evidence_quality))
        if isinstance(depth, (int, float)):
            report_depth.append(float(depth))

    tasks_by_id = {
        str(task.get("id") or ""): task
        for task in (tasks or [])
        if isinstance(task, dict) and str(task.get("id") or "")
    }
    duration_s = [float(item.get("duration_ms") or 0) / 1000.0 for item in completed]
    return MetricSummary(
        total_cases=total,
        completed_cases=len(completed),
        failed_cases=len(failed),
        timeout_cases=len(timeout),
        stable_completion_rate=round(len(stable) / total, 4) if total else 0.0,
        rubric_pass_rate=(
            round(sum(1 for s in report_scores if bool(s.get("passed"))) / len(report_scores), 4)
            if report_scores
            else None
        ),
        citation_accuracy=_avg(citation_accuracy_values),
        unsupported_claim_rate=_avg(unsupported_values),
        avg_effective_citations=_avg(effective_sources),
        judge_scores_total=len([s for s in scores if s.get("status") == "scored"]),
        report_judge_scored=len(report_scores),
        citation_judge_scored=len(citation_scores),
        claim_judge_scored=len(claim_scores),
        avg_report_evidence_quality=_avg(report_evidence_quality),
        avg_report_depth=_avg(report_depth),
        avg_sources=_avg(_count_evidence(results, "sources")),
        avg_evidence_items=_avg(_count_evidence(results, "evidence_items")),
        avg_passages=_avg(_count_evidence(results, "passages")),
        avg_claims_checked=_avg(_claim_counts(results)),
        avg_claims_supported=_avg(_claim_counts(results, "verified")),
        avg_claims_unsupported=_avg(_claim_counts(results, "unsupported")),
        hallucination_rate=_avg([float(s["score"]) for s in hallucination_scores if isinstance(s.get("score"), (int, float))]),
        hallucination_count=_avg([
            float(s.get("details", {}).get("hallucination_count") or 0)
            for s in hallucination_scores
            if isinstance(s.get("details"), dict)
        ]) if hallucination_scores else None,
        hallucination_judge_scored=len(hallucination_scores),
        by_language=_group_breakdown(results, scores, tasks_by_id, "language") if tasks_by_id else {},
        by_domain=_group_breakdown(results, scores, tasks_by_id, "domain") if tasks_by_id else {},
        p50_duration_s=percentile(duration_s, 0.5),
        p90_duration_s=percentile(duration_s, 0.9),
        mean_duration_s=round(mean(duration_s), 3) if duration_s else None,
    )


def summarize_run(run_dir: str | Path) -> MetricSummary:
    root = Path(run_dir)
    tasks = []
    cases_dir = root / "cases"
    if cases_dir.exists():
        for path in sorted(cases_dir.glob("*/task.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                payload = None
            if isinstance(payload, dict):
                tasks.append(payload)
    summary = summarize_results(
        _load_list(root / "results.json"),
        _load_list(root / "judge_scores.json"),
        tasks,
    )
    (root / "summary.json").write_text(
        json.dumps(summary.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary
