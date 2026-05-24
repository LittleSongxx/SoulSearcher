#!/usr/bin/env python3
"""GAIA Benchmark Runner for Weaver.

Evaluates Weaver against the GAIA benchmark or a curated internal dataset.
Runs each question through the deep-research pipeline in GAIA mode (short
answers), scores against ground truth, and produces a JSON report.

Usage:
    python scripts/gaia_benchmark.py                          # curated benchmark
    python scripts/gaia_benchmark.py --source gaia            # GAIA validation set
    python scripts/gaia_benchmark.py --source gaia --max 10   # first 10 GAIA qs
    python scripts/gaia_benchmark.py --source local --path data/gaia_val.jsonl
    python scripts/gaia_benchmark.py --mode remote --url http://localhost:8002
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("gaia_benchmark")


@dataclass
class BenchmarkResult:
    """Result for a single benchmark question."""
    task_id: str
    question: str
    level: int
    ground_truth: str = ""
    prediction: str = ""
    correct: bool = False
    score: float = 0.0
    scoring_method: str = ""
    duration_ms: float = 0
    error: str = ""
    prediction_chars: int = 0
    length_ok: bool = False


@dataclass
class BenchmarkReport:
    """Aggregate benchmark report."""
    total: int = 0
    correct: int = 0
    overall_accuracy: float = 0.0
    level_accuracy: dict[int, float] = field(default_factory=dict)
    avg_duration_ms: float = 0
    results: list[BenchmarkResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    run_id: str = ""
    timestamp: str = ""
    config: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Remote mode — calls the running Weaver server via its research SSE endpoint
# =============================================================================

async def run_remote(
    question: dict[str, Any], base_url: str, timeout: float = 300.0
) -> BenchmarkResult:
    """Run a benchmark question against a remote Weaver server."""
    import httpx

    task_id = question.get("id") or question.get("task_id", "unknown")
    query = question.get("query") or question.get("question", "")

    result = BenchmarkResult(
        task_id=task_id,
        question=query,
        level=question.get("level", 1),
        ground_truth=question.get("ground_truth", ""),
    )

    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            payload = {
                "messages": [{"role": "user", "content": query}],
                "deepsearch_config": {
                    "gaia_mode": True,
                    "allow_clarification": False,
                    "max_researcher_iterations": 3,
                },
            }
            response = await client.post(
                f"{base_url}/api/research/sse", json=payload
            )
            response.raise_for_status()

            # Collect SSE stream, extract final answer
            final_answer = _extract_answer_from_sse(response.text)
            result.prediction = final_answer

    except Exception as e:
        result.error = str(e)
        logger.error(f"[{task_id}] Remote error: {e}")

    result.duration_ms = (time.monotonic() - t0) * 1000
    return result


def _extract_answer_from_sse(text: str) -> str:
    """Extract the final answer from an SSE stream response."""
    lines = text.strip().split("\n")
    final_content = ""
    for line in lines:
        if line.startswith("data:"):
            try:
                data = json.loads(line[5:].strip())
                if data.get("type") == "content":
                    final_content += data.get("data", {}).get("text", "")
                elif data.get("type") == "done":
                    break
            except json.JSONDecodeError:
                continue
    return final_content.strip()


# =============================================================================
# ASGI mode — runs the graph in-process
# =============================================================================

async def run_asgi(
    question: dict[str, Any], graph, config: dict | None = None
) -> BenchmarkResult:
    """Run a benchmark question in-process against the compiled graph."""
    task_id = question.get("id") or question.get("task_id", "unknown")
    query = question.get("query") or question.get("question", "")

    result = BenchmarkResult(
        task_id=task_id,
        question=query,
        level=question.get("level", 1),
        ground_truth=question.get("ground_truth", ""),
    )

    t0 = time.monotonic()
    try:
        from agent.core.state import build_initial_state

        initial_state = build_initial_state(input_text=query)
        run_config = {
            "configurable": {
                "thread_id": f"gaia_{task_id}",
                "allow_clarification": False,
                "gaia_mode": True,
                "report_format": "markdown",
            },
            **(config or {}),
        }

        final_state = await graph.ainvoke(initial_state, run_config)
        result.prediction = (final_state.get("final_report") or "").strip()

    except Exception as e:
        result.error = str(e)
        logger.error(f"[{task_id}] ASGI error: {e}")

    result.duration_ms = (time.monotonic() - t0) * 1000
    return result


# =============================================================================
# Main Benchmark Runner
# =============================================================================

async def run_benchmark(
    questions: list[dict[str, Any]],
    mode: str = "asgi",
    base_url: str = "http://localhost:8002",
    max_concurrent: int = 3,
) -> BenchmarkReport:
    """Run a full benchmark across all questions."""
    from scripts.gaia_scorer import score_gaia_answer

    report = BenchmarkReport(
        total=len(questions),
        run_id=datetime.now().strftime("%Y%m%d_%H%M%S"),
        timestamp=datetime.now().isoformat(),
        config={"mode": mode, "max_concurrent": max_concurrent},
    )

    if mode == "asgi":
        from agent.core.graph import create_research_graph
        graph = create_research_graph()
        runner = lambda q: run_asgi(q, graph)
    else:
        runner = lambda q: run_remote(q, base_url)

    semaphore = asyncio.Semaphore(max_concurrent)

    async def run_one(q: dict) -> BenchmarkResult:
        async with semaphore:
            logger.info(f"[{q.get('id', q.get('task_id', '?'))}] Running...")
            r = await runner(q)

            r.prediction_chars = len(r.prediction)

            # Check expected length range if defined
            expected_len = q.get("expected_length")
            if expected_len and isinstance(expected_len, (list, tuple)) and len(expected_len) == 2:
                r.length_ok = expected_len[0] <= r.prediction_chars <= expected_len[1]

            # Score against ground truth if available
            if not r.error and r.ground_truth:
                scoring = score_gaia_answer(r.prediction, r.ground_truth)
                r.correct = scoring["correct"]
                r.score = scoring["score"]
                r.scoring_method = scoring["method"]
            elif not r.error and expected_len:
                # No ground truth — use length check as pass/fail
                r.correct = r.length_ok
                r.score = 1.0 if r.length_ok else 0.0
                r.scoring_method = "length_check"

            logger.info(
                f"[{r.task_id}] {'✓' if r.correct else '✗'} "
                f"({r.duration_ms:.0f}ms, {r.prediction_chars} chars) → {r.prediction[:100]}"
            )
            return r

    results = await asyncio.gather(*[run_one(q) for q in questions])
    report.results = list(results)

    # Aggregate statistics
    for r in report.results:
        if r.error:
            report.errors.append(f"{r.task_id}: {r.error}")
        if r.correct:
            report.correct += 1

    # Per-level accuracy
    for level in sorted(set(r.level for r in report.results)):
        level_results = [r for r in report.results if r.level == level and not r.error]
        if level_results:
            report.level_accuracy[level] = sum(1 for r in level_results if r.correct) / len(level_results)

    total_with_gt = [r for r in report.results if r.ground_truth and not r.error]
    report.overall_accuracy = report.correct / len(total_with_gt) if total_with_gt else 0
    report.avg_duration_ms = (
        sum(r.duration_ms for r in report.results) / len(report.results)
        if report.results else 0
    )

    return report


def print_report(report: BenchmarkReport) -> None:
    """Print a formatted benchmark report to stdout."""
    print(f"\n{'='*60}")
    print(f"GAIA BENCHMARK REPORT  [{report.run_id}]")
    print(f"{'='*60}")
    print(f"Total questions:  {report.total}")
    print(f"Correct:          {report.correct}")
    print(f"Overall accuracy: {report.overall_accuracy:.1%}")
    print(f"Avg duration:     {report.avg_duration_ms:.0f}ms")
    print()

    if report.level_accuracy:
        print("Accuracy by level:")
        for level, acc in sorted(report.level_accuracy.items()):
            n = sum(1 for r in report.results if r.level == level)
            print(f"  Level {level}: {acc:.1%} ({n} questions)")

    if report.errors:
        print(f"\nErrors: {len(report.errors)}")
        for err in report.errors[:5]:
            print(f"  - {err}")

    print(f"\n{'='*60}")

    # Detailed results table
    print(f"\n{'ID':<25} {'Lv':<3} {'Result':<8} {'Score':<7} {'Time':<8} Answer")
    print("-" * 100)
    for r in report.results:
        status = "✓" if r.correct else ("✗" if r.ground_truth else "?")
        print(
            f"{r.task_id:<25} {r.level:<3} {status:<8} {r.score:<7.1%} "
            f"{r.duration_ms:<8.0f}ms {r.prediction[:60]}"
        )


def save_report(report: BenchmarkReport, output_path: str) -> None:
    """Save the benchmark report as JSON."""
    data = {
        "run_id": report.run_id,
        "timestamp": report.timestamp,
        "config": report.config,
        "total": report.total,
        "correct": report.correct,
        "overall_accuracy": report.overall_accuracy,
        "level_accuracy": report.level_accuracy,
        "avg_duration_ms": report.avg_duration_ms,
        "errors": report.errors,
        "results": [
            {
                "task_id": r.task_id,
                "question": r.question[:200],
                "level": r.level,
                "ground_truth": r.ground_truth,
                "prediction": r.prediction,
                "correct": r.correct,
                "score": r.score,
                "scoring_method": r.scoring_method,
                "duration_ms": r.duration_ms,
                "prediction_chars": r.prediction_chars,
                "length_ok": r.length_ok,
                "error": r.error,
            }
            for r in report.results
        ],
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"Report saved to {output_path}")


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="GAIA Benchmark Runner for Weaver")
    parser.add_argument("--source", default="curated",
                        choices=["curated", "gaia", "local"],
                        help="Question source (default: curated)")
    parser.add_argument("--path", help="Path to local JSONL file (for --source local)")
    parser.add_argument("--max", type=int, dest="max_questions",
                        help="Maximum number of questions")
    parser.add_argument("--levels", type=int, nargs="+", choices=[1, 2, 3],
                        help="Filter by GAIA difficulty level")
    parser.add_argument("--mode", default="asgi", choices=["asgi", "remote"],
                        help="Execution mode (default: asgi)")
    parser.add_argument("--url", default="http://localhost:8002",
                        help="Server URL for remote mode")
    parser.add_argument("--concurrent", type=int, default=3,
                        help="Max concurrent questions (default: 3)")
    parser.add_argument("--output", default="",
                        help="Save JSON report to this path")
    args = parser.parse_args()

    # Load questions
    if args.source == "gaia":
        from scripts.gaia_loader import load_gaia_from_huggingface
        questions = load_gaia_from_huggingface(
            max_questions=args.max_questions,
            levels=args.levels,
        )
        if not questions:
            logger.error(
                "Failed to load GAIA from HuggingFace. "
                "Make sure 'pip install datasets' is installed and you have internet access."
            )
            sys.exit(1)
    elif args.source == "local":
        from scripts.gaia_loader import load_gaia_from_local
        path = args.path or "data/gaia_val.jsonl"
        questions = load_gaia_from_local(path)
    else:
        from scripts.gaia_loader import get_curated_benchmark
        questions = get_curated_benchmark()
        if args.levels:
            questions = [q for q in questions if q["level"] in args.levels]
        if args.max_questions:
            questions = questions[:args.max_questions]

    if not questions:
        logger.error("No questions loaded.")
        sys.exit(1)

    # Run benchmark
    report = asyncio.run(run_benchmark(
        questions=questions,
        mode=args.mode,
        base_url=args.url,
        max_concurrent=args.concurrent,
    ))

    # Output
    print_report(report)

    output_path = args.output or f"benchmark_{report.run_id}.json"
    save_report(report, output_path)


if __name__ == "__main__":
    main()
