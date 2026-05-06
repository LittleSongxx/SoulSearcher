from __future__ import annotations

import json
from typing import Any

from eval.deep_research_benchmark.judges.base import (
    env_int,
    get_judge_llm,
    invoke_text,
    maybe_number,
)
from eval.deep_research_benchmark.judges.rubric import (
    CITATION_JUDGE_PROMPT,
    CITATION_JUDGE_PROMPT_VERSION,
    extract_json_object,
)
from eval.deep_research_benchmark.schemas import BenchmarkTask, JudgeScore


def compact_evidence(evidence: dict[str, Any], *, max_items: int = 40) -> dict[str, Any]:
    sources = evidence.get("sources") if isinstance(evidence, dict) else []
    items = evidence.get("evidence_items") if isinstance(evidence, dict) else []
    passages = evidence.get("passages") if isinstance(evidence, dict) else []
    return {
        "sources": sources[:max_items] if isinstance(sources, list) else [],
        "evidence_items": items[:max_items] if isinstance(items, list) else [],
        "passages": passages[:max_items] if isinstance(passages, list) else [],
    }


def judge_citations(
    task: BenchmarkTask,
    report: str,
    evidence: dict[str, Any],
    *,
    llm: Any = None,
    judge_model: str = "",
) -> JudgeScore:
    llm = llm or get_judge_llm(judge_model, temperature=0.0)
    report_limit = env_int("DEEP_RESEARCH_BENCHMARK_JUDGE_CITATION_REPORT_CHARS", 6000)
    evidence_limit = env_int("DEEP_RESEARCH_BENCHMARK_JUDGE_CITATION_EVIDENCE_CHARS", 6000)
    evidence_items = env_int("DEEP_RESEARCH_BENCHMARK_JUDGE_CITATION_EVIDENCE_ITEMS", 6)
    prompt = CITATION_JUDGE_PROMPT.format(
        task_json=json.dumps(task.to_dict(), ensure_ascii=False, indent=2),
        report=report[:report_limit],
        evidence_json=json.dumps(
            compact_evidence(evidence, max_items=evidence_items), ensure_ascii=False, indent=2
        )[:evidence_limit],
    )
    raw = invoke_text(llm, prompt)
    try:
        payload = extract_json_object(raw)
    except Exception as exc:
        return JudgeScore(
            case_id=task.id,
            judge_type="citation",
            status="failed",
            raw_response=raw,
            error=str(exc),
            details={"prompt_version": CITATION_JUDGE_PROMPT_VERSION},
        )

    accuracy = maybe_number(payload.get("citation_accuracy"))
    total = int(maybe_number(payload.get("total_citation_checks")) or 0)
    supported = int(maybe_number(payload.get("supported_citation_checks")) or 0)
    if accuracy is None and total > 0:
        accuracy = supported / total

    return JudgeScore(
        case_id=task.id,
        judge_type="citation",
        status="scored",
        score=accuracy,
        passed=(accuracy is not None and accuracy >= 0.8),
        raw_response=raw,
        details={
            "prompt_version": CITATION_JUDGE_PROMPT_VERSION,
            "total_citation_checks": total,
            "supported_citation_checks": supported,
            "effective_unique_sources": int(
                maybe_number(payload.get("effective_unique_sources")) or 0
            ),
            "checks": payload.get("checks") or [],
        },
    )
