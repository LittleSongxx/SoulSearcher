from __future__ import annotations

import json
from typing import Any

from eval.deep_research_benchmark.judges.base import env_int, get_judge_llm, invoke_text, maybe_number
from eval.deep_research_benchmark.judges.citation_judge import compact_evidence
from eval.deep_research_benchmark.judges.rubric import (
    CLAIM_JUDGE_PROMPT,
    CLAIM_JUDGE_PROMPT_VERSION,
    extract_json_object,
)
from eval.deep_research_benchmark.schemas import BenchmarkTask, JudgeScore


def judge_claims(
    task: BenchmarkTask,
    report: str,
    evidence: dict[str, Any],
    *,
    llm: Any = None,
    judge_model: str = "",
) -> JudgeScore:
    llm = llm or get_judge_llm(judge_model, temperature=0.0)
    report_limit = env_int("DEEP_RESEARCH_BENCHMARK_JUDGE_CLAIM_REPORT_CHARS", 6000)
    evidence_limit = env_int("DEEP_RESEARCH_BENCHMARK_JUDGE_CLAIM_EVIDENCE_CHARS", 6000)
    evidence_items = env_int("DEEP_RESEARCH_BENCHMARK_JUDGE_CLAIM_EVIDENCE_ITEMS", 6)
    prompt = CLAIM_JUDGE_PROMPT.format(
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
            judge_type="claim",
            status="failed",
            raw_response=raw,
            error=str(exc),
            details={"prompt_version": CLAIM_JUDGE_PROMPT_VERSION},
        )

    total = int(maybe_number(payload.get("total_claims")) or 0)
    unsupported = int(maybe_number(payload.get("unsupported_claims")) or 0)
    contradicted = int(maybe_number(payload.get("contradicted_claims")) or 0)
    unsupported_rate = maybe_number(payload.get("unsupported_claim_rate"))
    if unsupported_rate is None and total > 0:
        unsupported_rate = (unsupported + contradicted) / total

    return JudgeScore(
        case_id=task.id,
        judge_type="claim",
        status="scored",
        score=unsupported_rate,
        passed=(unsupported_rate is not None and unsupported_rate <= 0.1),
        raw_response=raw,
        details={
            "prompt_version": CLAIM_JUDGE_PROMPT_VERSION,
            "total_claims": total,
            "supported_claims": int(maybe_number(payload.get("supported_claims")) or 0),
            "unsupported_claims": unsupported,
            "contradicted_claims": contradicted,
            "claims": payload.get("claims") or [],
        },
    )
