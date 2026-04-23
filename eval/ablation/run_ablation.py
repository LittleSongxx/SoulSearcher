"""
Ablation study: measure the contribution of each component to Deep Research quality.

Variants:
  full            – baseline (tree + IterDRAG + ClaimVerifier + multi-search + cache)
  no_iterdrag     – disable KnowledgeGapAnalyzer
  no_claimverify  – disable ClaimVerifier (set min_overlap to absurdly high value)
  linear          – use linear search instead of tree exploration
  single_provider – single search engine (Tavily only) instead of multi-provider

Each variant runs 5 representative cases via subprocess isolation.
Results are saved incrementally to eval/ablation/results/.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BENCH_FILE = ROOT / "eval" / "benchmarks" / "sample_tasks.jsonl"
RESULTS_DIR = ROOT / "eval" / "ablation" / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# 5 representative cases: financial, technical, legal, medical, scientific
CASE_INDICES = [0, 3, 4, 6, 9]  # case_001, 004, 005, 007, 010

# Load tasks
ALL_TASKS = []
with open(BENCH_FILE) as f:
    for line in f:
        if line.strip():
            ALL_TASKS.append(json.loads(line))
TASKS = [ALL_TASKS[i] for i in CASE_INDICES]

# Common env overrides to speed up ablation (reduce depth/epochs)
# Estimated ~3 min/case instead of ~8 min, preserving relative differences.
FAST_ENV = {
    "DEEPSEARCH_MAX_EPOCHS": "2",
    "TREE_MAX_DEPTH": "1",
    "TREE_MAX_BRANCHES": "3",
    "TREE_QUERIES_PER_BRANCH": "2",
    "DEEPSEARCH_RESULTS_PER_QUERY": "3",
    "DEEPSEARCH_MAX_SECONDS": "300",
    "DEEPSEARCH_TREE_MAX_SEARCHES": "15",
}

# Per-variant env overrides (on top of existing .env + FAST_ENV)
VARIANTS = {
    "full": {},
    "no_iterdrag": {
        "DEEPSEARCH_USE_GAP_ANALYSIS": "false",
    },
    "no_claimverify": {
        "DEEPSEARCH_CLAIM_VERIFIER_MIN_OVERLAP_TOKENS": "999999",
    },
    "linear": {
        "DEEPSEARCH_MODE": "linear",
        "DEEPSEARCH_MAX_EPOCHS": "1",
        "DEEPSEARCH_QUERY_NUM": "3",
    },
    "single_provider": {
        "SEARCH_ENGINES": "tavily",
        "SEARCH_STRATEGY": "fallback",
    },
    "hierarchical": {
        "USE_HIERARCHICAL_AGENTS": "true",
        "USE_HYBRID_SEARCH": "false",
    },
    "hybrid": {
        "USE_HIERARCHICAL_AGENTS": "true",
        "USE_HYBRID_SEARCH": "true",
    },
}

# Subprocess runner template
RUNNER_TEMPLATE = """
import asyncio, json, os, sys
sys.path.insert(0, "{root}")
{env_overrides}
from scripts.benchmark_deep_research import _execute_research_case
result = asyncio.run(_execute_research_case(
    sys.argv[1], mode="{mode}", base_url="asgi", model="", timeout_s=660,
))
print(json.dumps(result, ensure_ascii=False))
""".strip()


def build_runner_code(variant: str) -> str:
    env_lines = []
    mode = "auto"
    # Apply common fast settings first, then variant-specific overrides
    merged = {**FAST_ENV, **VARIANTS[variant]}
    for k, v in merged.items():
        env_lines.append(f'os.environ["{k}"] = "{v}"')
    if variant == "linear":
        mode = "linear"
    env_block = "\n".join(env_lines)
    return RUNNER_TEMPLATE.format(root=ROOT, env_overrides=env_block, mode=mode)


def run_single_case(variant: str, task: dict, runner_code: str) -> dict:
    cid = task["id"]
    query = task["query"]
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "-u", "-c", runner_code, query],
            capture_output=True,
            text=True,
            timeout=720,
            cwd=str(ROOT),
        )
        elapsed = time.monotonic() - t0
        out_lines = [l for l in proc.stdout.strip().split("\n") if l.strip()]
        result = (
            json.loads(out_lines[-1])
            if out_lines
            else {
                "status": "error",
                "error": "no output",
                "final_report_chars": 0,
            }
        )
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        result = {
            "status": "timeout",
            "error": f"timeout after {elapsed:.0f}s",
            "final_report_chars": 0,
        }
    except Exception as e:
        elapsed = time.monotonic() - t0
        result = {"status": "error", "error": str(e), "final_report_chars": 0}

    result["case_id"] = cid
    result["query"] = query
    result["variant"] = variant
    result["wall_time_s"] = round(elapsed, 1)
    result["domain"] = task.get("metadata", {}).get("domain", "")
    return result


def main():
    # Check which variants are already done
    for variant in VARIANTS:
        out_file = RESULTS_DIR / f"{variant}.json"
        if out_file.exists():
            existing = json.loads(out_file.read_text())
            done_ids = {
                r["case_id"] for r in existing if r.get("status") == "completed"
            }
            needed_ids = {t["id"] for t in TASKS}
            if needed_ids <= done_ids:
                print(
                    f"[{variant}] Already complete ({len(existing)} cases), skipping."
                )
                continue

        print(f"\n{'='*60}")
        print(f"  VARIANT: {variant}")
        print(f"  Overrides: {VARIANTS[variant] or '(none – baseline)'}")
        print(f"{'='*60}")

        runner_code = build_runner_code(variant)
        results = []
        for i, task in enumerate(TASKS, 1):
            print(
                f"  [{i}/{len(TASKS)}] {task['id']}: {task['query'][:55]}...",
                flush=True,
            )
            result = run_single_case(variant, task, runner_code)
            results.append(result)
            status = result.get("status", "?")
            chars = result.get("final_report_chars", 0)
            wall = result.get("wall_time_s", 0)
            print(f"    -> {status} in {wall:.0f}s, report={chars} chars", flush=True)

            # Incremental save
            out_file = RESULTS_DIR / f"{variant}.json"
            out_file.write_text(json.dumps(results, ensure_ascii=False, indent=2))

        print(f"  [{variant}] Done: {len(results)} cases saved to {out_file}")

    # Generate comparison summary
    print(f"\n{'='*60}")
    print("  COMPARISON SUMMARY")
    print(f"{'='*60}\n")

    all_data = {}
    for variant in VARIANTS:
        f = RESULTS_DIR / f"{variant}.json"
        if f.exists():
            all_data[variant] = json.loads(f.read_text())

    if not all_data:
        print("No results found.")
        return

    # Print per-variant aggregates
    header = f"{'Variant':<18} {'Completed':>9} {'Avg Time':>9} {'Avg Chars':>10} {'Avg QC':>7} {'Avg Src':>8}"
    print(header)
    print("-" * len(header))

    summary = {}
    for variant, results in all_data.items():
        completed = [r for r in results if r.get("status") == "completed"]
        times = [r["wall_time_s"] for r in completed]
        chars = [r.get("final_report_chars", 0) for r in completed]
        qc_scores = []
        sources = []
        for r in completed:
            qu = r.get("last_quality_update") or {}
            if qu.get("query_coverage_score") is not None:
                qc_scores.append(qu["query_coverage_score"])
            es = r.get("evidence_summary") or {}
            if es.get("sources_count") is not None:
                sources.append(es["sources_count"])

        avg_time = sum(times) / len(times) if times else 0
        avg_chars = sum(chars) / len(chars) if chars else 0
        avg_qc = sum(qc_scores) / len(qc_scores) if qc_scores else 0
        avg_src = sum(sources) / len(sources) if sources else 0

        print(
            f"{variant:<18} {len(completed):>5}/{len(results):<3} "
            f"{avg_time:>8.0f}s {avg_chars:>9.0f} {avg_qc:>7.2f} {avg_src:>7.0f}"
        )
        summary[variant] = {
            "completed": len(completed),
            "total": len(results),
            "avg_time_s": round(avg_time, 1),
            "avg_report_chars": round(avg_chars),
            "avg_query_coverage": round(avg_qc, 3),
            "avg_sources": round(avg_src),
            "cases": results,
        }

    # Save comparison
    comp_file = RESULTS_DIR / "comparison.json"
    comp_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nComparison saved to {comp_file}")


if __name__ == "__main__":
    main()
