from __future__ import annotations

import json
from typing import Any

from eval.deep_research_benchmark.judges.base import (
    env_int,
    get_judge_llm,
    invoke_text,
    maybe_number,
)
from eval.deep_research_benchmark.judges.citation_judge import compact_evidence
from eval.deep_research_benchmark.judges.rubric import (
    HALLUCINATION_JUDGE_PROMPT,
    HALLUCINATION_JUDGE_PROMPT_VERSION,
    extract_json_object,
)
from eval.deep_research_benchmark.schemas import BenchmarkTask, JudgeScore


def judge_hallucinations(
    task: BenchmarkTask,
    report: str,
    evidence: dict[str, Any],
    *,
    llm: Any = None,
    judge_model: str = "",
) -> JudgeScore:
    llm = llm or get_judge_llm(judge_model, temperature=0.0)
    report_limit = env_int("DEEP_RESEARCH_BENCHMARK_JUDGE_HALLUCINATION_REPORT_CHARS", 6000)
    evidence_limit = env_int("DEEP_RESEARCH_BENCHMARK_JUDGE_HALLUCINATION_EVIDENCE_CHARS", 6000)
    evidence_items = env_int("DEEP_RESEARCH_BENCHMARK_JUDGE_HALLUCINATION_EVIDENCE_ITEMS", 8)
    prompt = HALLUCINATION_JUDGE_PROMPT.format(
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
            judge_type="hallucination",
            status="failed",
            raw_response=raw,
            error=str(exc),
            details={"prompt_version": HALLUCINATION_JUDGE_PROMPT_VERSION},
        )

    hallucination_count = int(maybe_number(payload.get("hallucination_count")) or 0)
    total_claims = int(maybe_number(payload.get("total_claims_checked")) or 0)
    hallucination_rate = maybe_number(payload.get("hallucination_rate"))
    if hallucination_rate is None and total_claims > 0:
        hallucination_rate = hallucination_count / total_claims

    hallucination_types: dict[str, int] = {}
    for item in payload.get("hallucinations") or []:
        if not isinstance(item, dict):
            continue
        h_type = str(item.get("type") or "unknown")
        hallucination_types[h_type] = hallucination_types.get(h_type, 0) + 1

    return JudgeScore(
        case_id=task.id,
        judge_type="hallucination",
        status="scored",
        score=hallucination_rate,
        passed=(hallucination_rate is not None and hallucination_rate <= 0.1),
        raw_response=raw,
        details={
            "prompt_version": HALLUCINATION_JUDGE_PROMPT_VERSION,
            "total_claims_checked": total_claims,
            "hallucination_count": hallucination_count,
            "hallucination_rate": hallucination_rate,
            "hallucination_types": hallucination_types,
            "hallucinations": payload.get("hallucinations") or [],
        },
    )
