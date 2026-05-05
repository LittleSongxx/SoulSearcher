from __future__ import annotations

import json
import multiprocessing as mp
import queue
from pathlib import Path
from typing import Any, Callable, Dict, List

from eval.deep_research_benchmark.dataset import load_tasks
from eval.deep_research_benchmark.judges.base import env_int
from eval.deep_research_benchmark.judges.citation_judge import judge_citations
from eval.deep_research_benchmark.judges.claim_judge import judge_claims
from eval.deep_research_benchmark.judges.report_judge import score_report
from eval.deep_research_benchmark.schemas import BenchmarkTask, JudgeScore


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _task_by_id(tasks: list[BenchmarkTask]) -> dict[str, BenchmarkTask]:
    return {task.id: task for task in tasks}


def _load_scores(path: Path) -> list[JudgeScore]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return []
    scores: list[JudgeScore] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        scores.append(_score_from_dict(item))
    return scores


def _score_from_dict(item: dict[str, Any]) -> JudgeScore:
    return JudgeScore(
        case_id=str(item.get("case_id") or ""),
        judge_type=str(item.get("judge_type") or ""),
        status=str(item.get("status") or ""),
        score=item.get("score"),
        passed=item.get("passed"),
        details=item.get("details") if isinstance(item.get("details"), dict) else {},
        raw_response=str(item.get("raw_response") or ""),
        error=str(item.get("error") or ""),
        judged_at=str(item.get("judged_at") or ""),
    )


def _score_key(score: JudgeScore) -> tuple[str, str]:
    return score.case_id, score.judge_type


def _write_scores(path: Path, scores: list[JudgeScore]) -> None:
    output = [score.to_dict() for score in scores]
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")


def _failed_score(case_id: str, judge_type: str, exc: Exception) -> JudgeScore:
    return JudgeScore(
        case_id=case_id,
        judge_type=judge_type,
        status="failed",
        error=f"{type(exc).__name__}: {exc}",
    )


def _judge_worker(fn: Callable[[], JudgeScore], output_queue: Any) -> None:
    try:
        output_queue.put(("ok", fn().to_dict()))
    except Exception as exc:
        output_queue.put(("error", f"{type(exc).__name__}: {exc}"))


def _run_with_process_timeout(fn: Callable[[], JudgeScore], timeout_s: int) -> JudgeScore:
    if timeout_s <= 0:
        return fn()
    context = mp.get_context("fork")
    output_queue = context.Queue()
    process = context.Process(target=_judge_worker, args=(fn, output_queue))
    process.start()
    process.join(timeout_s)
    if process.is_alive():
        process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join()
        raise TimeoutError(f"judge subprocess exceeded {timeout_s}s")
    try:
        status, payload = output_queue.get_nowait()
    except queue.Empty as exc:
        raise RuntimeError(f"judge subprocess exited without result: exitcode={process.exitcode}") from exc
    if status == "ok" and isinstance(payload, dict):
        return _score_from_dict(payload)
    raise RuntimeError(str(payload))


def _run_one_judge(
    *,
    output_path: Path,
    scores: list[JudgeScore],
    existing: set[tuple[str, str]],
    case_id: str,
    judge_type: str,
    total_cases: int,
    case_index: int,
    fn: Callable[[], JudgeScore],
) -> None:
    key = (case_id, judge_type)
    if key in existing:
        print(f"[judge] skip existing {case_index}/{total_cases} {case_id} {judge_type}", flush=True)
        return
    print(f"[judge] start {case_index}/{total_cases} {case_id} {judge_type}", flush=True)
    timeout_s = env_int("DEEP_RESEARCH_BENCHMARK_JUDGE_TIMEOUT_S", 120)
    try:
        score = _run_with_process_timeout(fn, timeout_s)
    except Exception as exc:
        score = _failed_score(case_id, judge_type, exc)
        print(f"[judge] failed {case_index}/{total_cases} {case_id} {judge_type}: {score.error}", flush=True)
    else:
        print(
            f"[judge] done {case_index}/{total_cases} {case_id} {judge_type} status={score.status}",
            flush=True,
        )
    scores.append(score)
    existing.add(key)
    _write_scores(output_path, scores)


def judge_run(run_dir: str | Path, *, judge_model: str = "") -> list[JudgeScore]:
    root = Path(run_dir)
    config = _read_json(root / "run_config.json")
    dataset_path = Path(str(config.get("dataset_path") or ""))
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset from run_config not found: {dataset_path}")

    tasks = _task_by_id(load_tasks(dataset_path, max_cases=config.get("max_cases")))
    results_path = root / "results.json"
    if not results_path.exists():
        raise FileNotFoundError(f"Run results not found: {results_path}")
    results = json.loads(results_path.read_text(encoding="utf-8"))
    if not isinstance(results, list):
        raise ValueError("results.json must contain a list")

    output_path = root / "judge_scores.json"
    scores: List[JudgeScore] = [score for score in _load_scores(output_path) if score.status == "scored"]
    existing = {_score_key(score) for score in scores}
    total_cases = len([item for item in results if isinstance(item, dict)])
    for case_index, result in enumerate(results, start=1):
        if not isinstance(result, dict):
            continue
        case_id = str(result.get("case_id") or "")
        task = tasks.get(case_id)
        if task is None:
            continue
        report = str(result.get("final_report") or "")
        evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
        _run_one_judge(
            output_path=output_path,
            scores=scores,
            existing=existing,
            case_id=case_id,
            judge_type="report",
            total_cases=total_cases,
            case_index=case_index,
            fn=lambda task=task, report=report: score_report(task, report, judge_model=judge_model),
        )
        _run_one_judge(
            output_path=output_path,
            scores=scores,
            existing=existing,
            case_id=case_id,
            judge_type="citation",
            total_cases=total_cases,
            case_index=case_index,
            fn=lambda task=task, report=report, evidence=evidence: judge_citations(
                task, report, evidence, judge_model=judge_model
            ),
        )
        _run_one_judge(
            output_path=output_path,
            scores=scores,
            existing=existing,
            case_id=case_id,
            judge_type="claim",
            total_cases=total_cases,
            case_index=case_index,
            fn=lambda task=task, report=report, evidence=evidence: judge_claims(
                task, report, evidence, judge_model=judge_model
            ),
        )
    _write_scores(output_path, scores)
    print(
        f"[judge] complete scores={len(scores)} output={root / 'judge_scores.json'}",
        flush=True,
    )
    return scores
