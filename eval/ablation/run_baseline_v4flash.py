"""
Re-run baseline with deepseek-v4-flash for fair comparison with v6 optimized_full.
All 6 new optimizations OFF, same model, same 5 cases.
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

CASE_INDICES = [0, 3, 4, 6, 9]
ALL_TASKS = []
with open(BENCH_FILE) as f:
    for line in f:
        if line.strip():
            ALL_TASKS.append(json.loads(line))
TASKS = [ALL_TASKS[i] for i in CASE_INDICES]

FAST_ENV = {
    "DEEPSEARCH_MAX_EPOCHS": "2",
    "TREE_MAX_DEPTH": "1",
    "TREE_MAX_BRANCHES": "3",
    "TREE_QUERIES_PER_BRANCH": "2",
    "DEEPSEARCH_RESULTS_PER_QUERY": "3",
    "DEEPSEARCH_MAX_SECONDS": "300",
    "DEEPSEARCH_TREE_MAX_SEARCHES": "15",
}

BASELINE_ENV = {
    # Force same model as v6
    "PRIMARY_MODEL": "deepseek-v4-flash",
    # All 6 new optimizations OFF
    "OBSERVATION_MASKING": "false",
    "STRIP_TOOL_MESSAGES": "false",
    "CONTEXT_OFFLOADING": "false",
    "AGENT_REFLEXION_ENABLED": "false",
    "TREE_BACKTRACK_ENABLED": "false",
    "DYNAMIC_TOOL_PRUNING": "false",
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


def build_runner_code() -> str:
    env_lines = []
    merged = {**FAST_ENV, **BASELINE_ENV}
    for k, v in merged.items():
        env_lines.append(f'os.environ["{k}"] = "{v}"')
    return RUNNER_TEMPLATE.format(root=ROOT, env_overrides="\n".join(env_lines))


def run_single_case(task: dict, runner_code: str) -> dict:
    cid = task["id"]
    query = task["query"]
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "-u", "-c", runner_code, query],
            capture_output=True, text=True, timeout=900, cwd=str(ROOT),
        )
        elapsed = time.monotonic() - t0
        out_lines = [l for l in proc.stdout.strip().split("\n") if l.strip()]
        if out_lines:
            result = json.loads(out_lines[-1])
        else:
            stderr_tail = (proc.stderr or "")[-500:]
            result = {"status": "error", "error": f"no output; stderr: {stderr_tail}", "final_report_chars": 0}
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        result = {"status": "timeout", "error": f"timeout after {elapsed:.0f}s", "final_report_chars": 0}
    except Exception as e:
        elapsed = time.monotonic() - t0
        result = {"status": "error", "error": str(e), "final_report_chars": 0}

    result["case_id"] = cid
    result["query"] = query
    result["variant"] = "baseline_v4flash"
    result["wall_time_s"] = round(elapsed, 1)
    result["domain"] = task.get("metadata", {}).get("domain", "")
    return result


def main():
    out_file = RESULTS_DIR / "baseline_v4flash.json"
    print("=" * 60)
    print("  BASELINE re-run with deepseek-v4-flash")
    print("  All 6 new optimizations: OFF")
    print(f"  Cases: {len(TASKS)}")
    print("=" * 60)

    runner_code = build_runner_code()
    results = []
    for i, task in enumerate(TASKS, 1):
        print(f"  [{i}/{len(TASKS)}] {task['id']}: {task['query'][:55]}...", flush=True)
        result = run_single_case(task, runner_code)
        results.append(result)
        s = result.get("status", "?")
        c = result.get("final_report_chars", 0)
        w = result.get("wall_time_s", 0)
        print(f"    -> {s} in {w:.0f}s, report={c} chars", flush=True)
        out_file.write_text(json.dumps(results, ensure_ascii=False, indent=2))

    print(f"\nDone: {len(results)} cases saved to {out_file}")

    # Quick summary
    completed = [r for r in results if r.get("status") == "completed"]
    if completed:
        avg_t = sum(r["wall_time_s"] for r in completed) / len(completed)
        avg_c = sum(r.get("final_report_chars", 0) for r in completed) / len(completed)
        print(f"\nCompleted: {len(completed)}/{len(results)}")
        print(f"Avg time: {avg_t:.0f}s")
        print(f"Avg chars: {avg_c:.0f}")


if __name__ == "__main__":
    main()
