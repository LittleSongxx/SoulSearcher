"""
v7 Production Benchmark: Baseline vs Optimized — full production parameters.

Two variants × 10 new cases (5 domains × 2), no timeout.
Results saved incrementally to eval/ablation/results/v7_*.json.

Usage:
    conda run -n langchain python eval/ablation/run_v7_production.py
    conda run -n langchain python eval/ablation/run_v7_production.py --variant baseline
    conda run -n langchain python eval/ablation/run_v7_production.py --variant optimized
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BENCH_FILE = ROOT / "eval" / "benchmarks" / "v7_tasks.jsonl"
RESULTS_DIR = ROOT / "eval" / "ablation" / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Load all tasks from v7_tasks.jsonl
TASKS = []
with open(BENCH_FILE) as f:
    for line in f:
        if line.strip():
            TASKS.append(json.loads(line))

# Production env: use config.py defaults for search parameters.
# Only set DEEPSEARCH_MAX_SECONDS=0 (unlimited) explicitly to ensure no time cap.
PRODUCTION_ENV = {
    "DEEPSEARCH_MAX_SECONDS": "0",
}

# Per-variant optimization toggles
VARIANTS = {
    "baseline": {
        # All 6 optimizations OFF
        "OBSERVATION_MASKING": "false",
        "STRIP_TOOL_MESSAGES": "false",
        "CONTEXT_OFFLOADING": "false",
        "AGENT_REFLEXION_ENABLED": "false",
        "TREE_BACKTRACK_ENABLED": "false",
        "DYNAMIC_TOOL_PRUNING": "false",
    },
    "optimized": {
        # All 6 optimizations ON (dev2 defaults)
        "OBSERVATION_MASKING": "true",
        "OBSERVATION_MASKING_WINDOW": "5",
        "CONTEXT_OFFLOADING": "true",
        "AGENT_REFLEXION_ENABLED": "true",
        "TREE_BACKTRACK_ENABLED": "true",
        "DYNAMIC_TOOL_PRUNING": "true",
    },
}

RUNNER_TEMPLATE = """
import asyncio, json, os, sys
sys.path.insert(0, "{root}")
{env_overrides}
from scripts.benchmark_deep_research import _execute_research_case
result = asyncio.run(_execute_research_case(
    sys.argv[1], mode="auto", base_url="asgi", model="", timeout_s=7200,
))
print(json.dumps(result, ensure_ascii=False))
""".strip()


def build_runner_code(variant: str) -> str:
    env_lines = []
    merged = {**PRODUCTION_ENV, **VARIANTS[variant]}
    for k, v in merged.items():
        env_lines.append(f'os.environ["{k}"] = "{v}"')
    env_block = "\n".join(env_lines)
    return RUNNER_TEMPLATE.format(root=ROOT, env_overrides=env_block)


def run_single_case(variant: str, task: dict, runner_code: str) -> dict:
    cid = task["id"]
    query = task["query"]
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "-u", "-c", runner_code, query],
            capture_output=True,
            text=True,
            timeout=None,  # No subprocess timeout — let it finish
            cwd=str(ROOT),
        )
        elapsed = time.monotonic() - t0
        out_lines = [l for l in proc.stdout.strip().split("\n") if l.strip()]
        if out_lines:
            result = json.loads(out_lines[-1])
        else:
            stderr_tail = (proc.stderr or "")[-1000:]
            result = {
                "status": "error",
                "error": f"no output; stderr: {stderr_tail}",
                "final_report_chars": 0,
                "final_report": "",
            }
    except Exception as e:
        elapsed = time.monotonic() - t0
        result = {
            "status": "error",
            "error": str(e),
            "final_report_chars": 0,
            "final_report": "",
        }

    result["case_id"] = cid
    result["query"] = query
    result["variant"] = variant
    result["wall_time_s"] = round(elapsed, 1)
    result["domain"] = task.get("metadata", {}).get("domain", "")
    return result


def run_variant(variant: str):
    out_file = RESULTS_DIR / f"v7_{variant}.json"

    # Check for already-completed cases (resume support)
    existing_results = []
    done_ids = set()
    if out_file.exists():
        existing_results = json.loads(out_file.read_text())
        done_ids = {r["case_id"] for r in existing_results if r.get("status") == "completed"}

    remaining = [t for t in TASKS if t["id"] not in done_ids]
    if not remaining:
        print(f"\n[{variant}] All {len(TASKS)} cases already completed, skipping.")
        return existing_results

    print(f"\n{'=' * 60}")
    print(f"  VARIANT: {variant}")
    toggled = VARIANTS[variant]
    on_flags = [k for k, v in toggled.items() if v.lower() == "true"]
    off_flags = [k for k, v in toggled.items() if v.lower() == "false"]
    print(f"  ON:  {', '.join(on_flags) or '(none)'}")
    print(f"  OFF: {', '.join(off_flags) or '(none)'}")
    print(f"  Cases: {len(remaining)} remaining / {len(TASKS)} total")
    print(f"  Parameters: production defaults (epochs=3, depth=2, branches=4)")
    print(f"  Timeout: none (unlimited)")
    print(f"{'=' * 60}")

    runner_code = build_runner_code(variant)
    results = list(existing_results)  # start with any existing results

    total_idx = len(done_ids)
    for i, task in enumerate(remaining, 1):
        total_idx += 1
        print(
            f"  [{total_idx}/{len(TASKS)}] {task['id']} ({task['metadata']['domain']}): "
            f"{task['query'][:50]}...",
            flush=True,
        )
        result = run_single_case(variant, task, runner_code)
        results.append(result)

        status = result.get("status", "?")
        chars = result.get("final_report_chars", 0)
        wall = result.get("wall_time_s", 0)
        print(f"    -> {status} in {wall:.0f}s, report={chars} chars", flush=True)

        # Incremental save
        out_file.write_text(json.dumps(results, ensure_ascii=False, indent=2))

    print(f"  [{variant}] Done: {len(results)} cases saved to {out_file}")
    return results


def print_summary(all_data: dict):
    print(f"\n{'=' * 60}")
    print("  v7 PRODUCTION BENCHMARK SUMMARY")
    print(f"{'=' * 60}\n")

    def _avg(vals):
        return round(sum(vals) / len(vals), 3) if vals else 0

    header = f"{'Variant':<12} {'Done':>6} {'Time':>7} {'Chars':>8} {'QC':>6} {'Src':>5} {'UC':>4} {'Fresh':>6} {'CitCov':>7}"
    print(header)
    print("-" * len(header))

    for variant, results in all_data.items():
        completed = [r for r in results if r.get("status") == "completed"]
        times = [r["wall_time_s"] for r in completed]
        chars = [r.get("final_report_chars", 0) for r in completed]

        qc_scores, sources, uc_counts = [], [], []
        freshness, cit_cov = [], []

        for r in completed:
            # From SSE quality_update
            qu = r.get("last_quality_update") or {}
            if qu.get("query_coverage_score") is not None:
                qc_scores.append(qu["query_coverage_score"])

            # From run metrics API
            es = r.get("evidence_summary") or {}
            if es.get("sources_count") is not None:
                sources.append(es["sources_count"])
            if es.get("query_coverage_score") is not None and not qc_scores:
                qc_scores.append(es["query_coverage_score"])
            if es.get("unsupported_claims_count") is not None:
                uc_counts.append(es["unsupported_claims_count"])
            if es.get("freshness_ratio_30d") is not None:
                freshness.append(es["freshness_ratio_30d"])
            if es.get("citation_coverage") is not None:
                cit_cov.append(es["citation_coverage"])

        print(
            f"{variant:<12} "
            f"{len(completed):>3}/{len(results):<2} "
            f"{_avg(times):>6.0f}s "
            f"{_avg(chars):>7.0f} "
            f"{_avg(qc_scores):>6.2f} "
            f"{_avg(sources):>4.0f} "
            f"{_avg(uc_counts):>4.1f} "
            f"{_avg(freshness):>5.2f} "
            f"{_avg(cit_cov):>6.2f}"
        )


def main():
    parser = argparse.ArgumentParser(description="v7 Production Benchmark")
    parser.add_argument(
        "--variant",
        choices=["baseline", "optimized"],
        default=None,
        help="Run only a specific variant (default: run both)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  v7 PRODUCTION BENCHMARK: Baseline vs Optimized")
    print(f"  Cases: {len(TASKS)} | Variants: {len(VARIANTS)}")
    print("  Parameters: production defaults (no timeout)")
    print("=" * 60)

    variants_to_run = [args.variant] if args.variant else list(VARIANTS.keys())
    all_data = {}

    for variant in variants_to_run:
        results = run_variant(variant)
        all_data[variant] = results

    # Also load the other variant if only one was run (for summary)
    for variant in VARIANTS:
        if variant not in all_data:
            f = RESULTS_DIR / f"v7_{variant}.json"
            if f.exists():
                all_data[variant] = json.loads(f.read_text())

    if all_data:
        print_summary(all_data)


if __name__ == "__main__":
    main()
