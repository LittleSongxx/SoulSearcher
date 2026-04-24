"""
Ablation study v6: measure the contribution of each new optimization to Deep Research quality.

Optimizations under test:
  optimized_full    – all 6 optimizations enabled (dev2 defaults)
  no_obs_masking    – disable observation masking (revert to strip)
  no_offloading     – disable context offloading
  no_reflexion      – disable agent reflexion
  no_backtrack      – disable LATS tree backtracking
  no_tool_pruning   – disable dynamic tool pruning
  no_optimizations  – all 6 disabled (baseline equivalent)

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

# Same 5 representative cases as the original ablation
CASE_INDICES = [0, 3, 4, 6, 9]  # case_001, 004, 005, 007, 010

ALL_TASKS = []
with open(BENCH_FILE) as f:
    for line in f:
        if line.strip():
            ALL_TASKS.append(json.loads(line))
TASKS = [ALL_TASKS[i] for i in CASE_INDICES]

# Common fast env (same as original ablation for fair comparison)
FAST_ENV = {
    "DEEPSEARCH_MAX_EPOCHS": "2",
    "TREE_MAX_DEPTH": "1",
    "TREE_MAX_BRANCHES": "3",
    "TREE_QUERIES_PER_BRANCH": "2",
    "DEEPSEARCH_RESULTS_PER_QUERY": "3",
    "DEEPSEARCH_MAX_SECONDS": "300",
    "DEEPSEARCH_TREE_MAX_SEARCHES": "15",
}

# Per-variant env overrides for each optimization toggle
VARIANTS = {
    "optimized_full": {
        # All 6 optimizations ON (dev2 defaults)
        "OBSERVATION_MASKING": "true",
        "OBSERVATION_MASKING_WINDOW": "5",
        "CONTEXT_OFFLOADING": "true",
        "AGENT_REFLEXION_ENABLED": "true",
        "TREE_BACKTRACK_ENABLED": "true",
        "DYNAMIC_TOOL_PRUNING": "true",
    },
    "no_obs_masking": {
        "OBSERVATION_MASKING": "false",
        "STRIP_TOOL_MESSAGES": "false",
        "CONTEXT_OFFLOADING": "true",
        "AGENT_REFLEXION_ENABLED": "true",
        "TREE_BACKTRACK_ENABLED": "true",
        "DYNAMIC_TOOL_PRUNING": "true",
    },
    "no_offloading": {
        "OBSERVATION_MASKING": "true",
        "CONTEXT_OFFLOADING": "false",
        "AGENT_REFLEXION_ENABLED": "true",
        "TREE_BACKTRACK_ENABLED": "true",
        "DYNAMIC_TOOL_PRUNING": "true",
    },
    "no_reflexion": {
        "OBSERVATION_MASKING": "true",
        "CONTEXT_OFFLOADING": "true",
        "AGENT_REFLEXION_ENABLED": "false",
        "TREE_BACKTRACK_ENABLED": "true",
        "DYNAMIC_TOOL_PRUNING": "true",
    },
    "no_backtrack": {
        "OBSERVATION_MASKING": "true",
        "CONTEXT_OFFLOADING": "true",
        "AGENT_REFLEXION_ENABLED": "true",
        "TREE_BACKTRACK_ENABLED": "false",
        "DYNAMIC_TOOL_PRUNING": "true",
    },
    "no_tool_pruning": {
        "OBSERVATION_MASKING": "true",
        "CONTEXT_OFFLOADING": "true",
        "AGENT_REFLEXION_ENABLED": "true",
        "TREE_BACKTRACK_ENABLED": "true",
        "DYNAMIC_TOOL_PRUNING": "false",
    },
    "no_optimizations": {
        # All 6 OFF — equivalent to pre-optimization baseline
        "OBSERVATION_MASKING": "false",
        "STRIP_TOOL_MESSAGES": "false",
        "CONTEXT_OFFLOADING": "false",
        "AGENT_REFLEXION_ENABLED": "false",
        "TREE_BACKTRACK_ENABLED": "false",
        "DYNAMIC_TOOL_PRUNING": "false",
    },
}

RUNNER_TEMPLATE = """
import asyncio, json, os, sys
sys.path.insert(0, "{root}")
{env_overrides}
from scripts.benchmark_deep_research import _execute_research_case
result = asyncio.run(_execute_research_case(
    sys.argv[1], mode="auto", base_url="asgi", model="", timeout_s=840,
))
print(json.dumps(result, ensure_ascii=False))
""".strip()


def build_runner_code(variant: str) -> str:
    env_lines = []
    merged = {**FAST_ENV, **VARIANTS[variant]}
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
            timeout=900,
            cwd=str(ROOT),
        )
        elapsed = time.monotonic() - t0
        out_lines = [l for l in proc.stdout.strip().split("\n") if l.strip()]
        if out_lines:
            result = json.loads(out_lines[-1])
        else:
            stderr_tail = (proc.stderr or "")[-500:]
            result = {
                "status": "error",
                "error": f"no output; stderr: {stderr_tail}",
                "final_report_chars": 0,
            }
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
    print("=" * 60)
    print("  ABLATION v6: Optimization Contributions")
    print("  Branch: dev2")
    print(f"  Cases: {len(TASKS)} | Variants: {len(VARIANTS)}")
    print("=" * 60)

    for variant in VARIANTS:
        out_file = RESULTS_DIR / f"v6_{variant}.json"

        # Skip if already complete
        if out_file.exists():
            existing = json.loads(out_file.read_text())
            done_ids = {
                r["case_id"] for r in existing if r.get("status") == "completed"
            }
            needed_ids = {t["id"] for t in TASKS}
            if needed_ids <= done_ids:
                print(f"\n[{variant}] Already complete ({len(existing)} cases), skipping.")
                continue

        print(f"\n{'=' * 60}")
        print(f"  VARIANT: {variant}")
        toggled = VARIANTS[variant]
        on_flags = [k for k, v in toggled.items() if v.lower() == "true"]
        off_flags = [k for k, v in toggled.items() if v.lower() == "false"]
        print(f"  ON:  {', '.join(on_flags) or '(none)'}")
        print(f"  OFF: {', '.join(off_flags) or '(none)'}")
        print(f"{'=' * 60}")

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
            out_file.write_text(json.dumps(results, ensure_ascii=False, indent=2))

        print(f"  [{variant}] Done: {len(results)} cases saved to {out_file}")

    # Generate comparison summary
    print(f"\n{'=' * 60}")
    print("  v6 COMPARISON SUMMARY")
    print(f"{'=' * 60}\n")

    all_data = {}
    for variant in VARIANTS:
        f = RESULTS_DIR / f"v6_{variant}.json"
        if f.exists():
            all_data[variant] = json.loads(f.read_text())

    if not all_data:
        print("No results found.")
        return

    def _avg(vals):
        return round(sum(vals) / len(vals), 3) if vals else 0

    header = f"{'Variant':<20} {'Done':>5} {'Time':>6} {'Chars':>7} {'QC':>5} {'Src':>5}"
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
            if es.get("query_coverage_score") is not None and not qc_scores:
                qc_scores.append(es["query_coverage_score"])

        m = {
            "completed": len(completed),
            "total": len(results),
            "avg_time_s": _avg(times),
            "avg_report_chars": round(_avg(chars)),
            "avg_query_coverage": _avg(qc_scores),
            "avg_sources": round(_avg(sources)),
        }

        print(
            f"{variant:<20} {m['completed']:>3}/{m['total']:<1} "
            f"{m['avg_time_s']:>5.0f}s {m['avg_report_chars']:>6d} "
            f"{m['avg_query_coverage']:>5.2f} {m['avg_sources']:>4d}"
        )
        summary[variant] = {**m, "cases": results}

    comp_file = RESULTS_DIR / "v6_comparison.json"
    comp_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nComparison saved to {comp_file}")


if __name__ == "__main__":
    main()
