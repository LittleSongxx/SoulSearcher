from __future__ import annotations

import json
from typing import Any, Optional

from eval.deep_research_benchmark.judges.base import clamp_score, env_int, get_judge_llm, invoke_text
from eval.deep_research_benchmark.judges.rubric import (
    REPORT_JUDGE_PROMPT_VERSION,
    REPORT_RUBRIC_PROMPT,
    extract_json_object,
)
from eval.deep_research_benchmark.schemas import BenchmarkTask, JudgeScore


def score_report(
    task: BenchmarkTask,
    report: str,
    *,
    llm: Any = None,
    judge_model: str = "",
) -> JudgeScore:
    if not report.strip():
        return JudgeScore(
            case_id=task.id,
            judge_type="report",
            status="failed",
            error="empty report",
            details={"prompt_version": REPORT_JUDGE_PROMPT_VERSION},
        )

    llm = llm or get_judge_llm(judge_model, temperature=0.0)
    report_limit = env_int("DEEP_RESEARCH_BENCHMARK_JUDGE_REPORT_CHARS", 24000)
    prompt = REPORT_RUBRIC_PROMPT.format(
        task_json=json.dumps(task.to_dict(), ensure_ascii=False, indent=2),
        report=report[:report_limit],
    )
    raw = invoke_text(llm, prompt)
    try:
        payload = extract_json_object(raw)
    except Exception as exc:
        return JudgeScore(
            case_id=task.id,
            judge_type="report",
            status="failed",
            raw_response=raw,
            error=str(exc),
            details={"prompt_version": REPORT_JUDGE_PROMPT_VERSION},
        )

    dimensions = {
        key: clamp_score(payload.get(key))
        for key in ("coverage", "depth", "structure", "evidence_quality", "freshness", "overall")
    }
    overall = dimensions["overall"]
    passed = bool(payload.get("rubric_pass"))
    return JudgeScore(
        case_id=task.id,
        judge_type="report",
        status="scored",
        score=overall,
        passed=passed,
        raw_response=raw,
        details={
            "prompt_version": REPORT_JUDGE_PROMPT_VERSION,
            "dimensions": dimensions,
            "missing_dimensions": payload.get("missing_dimensions") or [],
            "rationale": str(payload.get("rationale") or ""),
        },
    )


def score_report_from_paths(
    task: BenchmarkTask,
    report: str,
    *,
    llm: Optional[Any] = None,
    judge_model: str = "",
) -> JudgeScore:
    return score_report(task, report, llm=llm, judge_model=judge_model)
