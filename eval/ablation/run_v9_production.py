"""
v9 Production Benchmark: Baseline vs Optimized — current .env tuning.

Same 10 cases as v7/v8. This runner preserves the user's current .env
parameters and only applies per-variant optimization toggles.

Results saved incrementally to eval/ablation/results/v9_*.json.

Usage:
    conda run -n langchain python eval/ablation/run_v9_production.py
    conda run -n langchain python eval/ablation/run_v9_production.py --variant baseline
    conda run -n langchain python eval/ablation/run_v9_production.py --variant optimized
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[2]
BENCH_FILE = ROOT / "eval" / "benchmarks" / "v7_tasks.jsonl"
RESULTS_DIR = ROOT / "eval" / "ablation" / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

TASKS = []
with open(BENCH_FILE, encoding="utf-8") as f:
    for line in f:
        if line.strip():
            TASKS.append(json.loads(line))

RUNNER_ENV = {
    "BENCHMARK_SKIP_CLIENT_CLOSE": "true",
    "DEEPSEARCH_VISUALIZE_BROWSER": "false",
}

VARIANTS = {
    "baseline": {
        "OBSERVATION_MASKING": "false",
        "STRIP_TOOL_MESSAGES": "false",
        "CONTEXT_OFFLOADING": "false",
        "AGENT_REFLEXION_ENABLED": "false",
        "TREE_BACKTRACK_ENABLED": "false",
        "DYNAMIC_TOOL_PRUNING": "false",
    },
    "optimized": {
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
print(json.dumps(result, ensure_ascii=False), flush=True)
os._exit(0)
""".strip()

DEFAULT_CASE_TIMEOUT_S = 900
DEFAULT_MONITOR_INTERVAL_S = 60
CASE_TIMEOUT_RETRY_COUNT = 1
ENV_SNAPSHOT_KEYS = (
    "PRIMARY_MODEL",
    "REASONING_MODEL",
    "SEARCH_ENGINES",
    "SEARCH_STRATEGY",
    "TOOL_RETRY",
    "TOOL_CALL_LIMIT",
    "DEEPSEARCH_MAX_EPOCHS",
    "DEEPSEARCH_QUERY_NUM",
    "DEEPSEARCH_RESULTS_PER_QUERY",
    "DEEPSEARCH_REPORT_SOURCES_LIMIT",
    "DEEPSEARCH_VISUALIZE_BROWSER",
    "DEEPSEARCH_ENABLE_CRAWLER",
    "DEEPSEARCH_ENABLE_RESEARCH_FETCHER",
    "RESEARCH_FETCH_TIMEOUT_S",
    "RESEARCH_FETCH_CONCURRENCY",
    "RESEARCH_FETCH_CACHE_TTL_S",
    "RESEARCH_FETCH_RENDER_MODE",
    "CRAWLER_HEADLESS",
    "CLAIM_VERIFIER_GATE_MAX_CONTRADICTED",
    "CLAIM_VERIFIER_GATE_MAX_UNSUPPORTED",
)


def read_dotenv_values() -> Dict[str, str]:
    env_file = ROOT / ".env"
    values: Dict[str, str] = {}
    if not env_file.exists():
        return values
    for raw in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip().strip('"').strip("'")
    return values


def env_snapshot() -> Dict[str, str]:
    dotenv = read_dotenv_values()
    snapshot: Dict[str, str] = {}
    for key in ENV_SNAPSHOT_KEYS:
        value = RUNNER_ENV.get(key, os.environ.get(key, dotenv.get(key, "")))
        snapshot[key] = value
    return snapshot


def build_runner_code(variant: str) -> str:
    env_lines = []
    merged = {**RUNNER_ENV, **VARIANTS[variant]}
    for k, v in merged.items():
        env_lines.append(f'os.environ["{k}"] = {json.dumps(v)}')
    env_block = "\n".join(env_lines)
    return RUNNER_TEMPLATE.format(root=ROOT, env_overrides=env_block)


def _parse_child_result(stdout: str, stderr: str, returncode: int) -> dict:
    out_lines = [line for line in stdout.strip().split("\n") if line.strip()]
    if out_lines:
        try:
            return json.loads(out_lines[-1])
        except json.JSONDecodeError:
            pass
    return {
        "status": "error",
        "error": f"no parseable output; returncode={returncode}; stderr: {(stderr or '')[-1200:]}",
        "final_report_chars": 0,
        "final_report": "",
    }


def _terminate_process_group(proc: subprocess.Popen) -> Tuple[str, str]:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except Exception:
        proc.terminate()
    try:
        return proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception:
            proc.kill()
        return proc.communicate()


def run_single_case(
    variant: str,
    task: dict,
    runner_code: str,
    case_timeout_s: int,
    monitor_interval_s: int,
) -> dict:
    cid = task["id"]
    query = task["query"]
    t0 = time.monotonic()
    stdout = ""
    stderr = ""
    timed_out = False
    try:
        proc = subprocess.Popen(
            [sys.executable, "-u", "-c", runner_code, query],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(ROOT),
            start_new_session=True,
        )
        interrupt_count = 0
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=monitor_interval_s)
                break
            except subprocess.TimeoutExpired:
                elapsed = time.monotonic() - t0
                print(
                    f"    ... still running {elapsed:.0f}s "
                    f"(hard cap {case_timeout_s}s)",
                    flush=True,
                )
                if elapsed >= case_timeout_s:
                    timed_out = True
                    stdout, stderr = _terminate_process_group(proc)
                    break
            except KeyboardInterrupt:
                interrupt_count += 1
                elapsed = time.monotonic() - t0
                if proc.poll() is None:
                    print(
                        f"    ! KeyboardInterrupt received at {elapsed:.0f}s; "
                        f"child still running, continuing monitor ({interrupt_count})",
                        flush=True,
                    )
                    continue
                stdout, stderr = proc.communicate()
                break
        elapsed = time.monotonic() - t0
        if timed_out:
            result = {
                "status": "timeout",
                "error": f"subprocess timeout at {case_timeout_s}s; stderr: {(stderr or '')[-1200:]}",
                "final_report_chars": 0,
                "final_report": "",
            }
        else:
            result = _parse_child_result(stdout, stderr, proc.returncode or 0)
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


def _is_timeout_retryable(result: dict) -> bool:
    status = str(result.get("status") or "").lower()
    error = str(result.get("error") or "").lower()
    return status == "timeout" or "subprocess timeout" in error


def _load_existing_completed(out_file: Path) -> Tuple[List[dict], set]:
    if not out_file.exists():
        return [], set()
    existing = json.loads(out_file.read_text(encoding="utf-8"))
    completed_or_terminal = [
        r for r in existing
        if r.get("status") == "completed"
    ]
    done_ids = {r["case_id"] for r in completed_or_terminal if r.get("case_id")}
    return completed_or_terminal, done_ids


def run_variant(variant: str, case_timeout_s: int, monitor_interval_s: int):
    out_file = RESULTS_DIR / f"v9_{variant}.json"
    existing_results, done_ids = _load_existing_completed(out_file)
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
    print("  Parameters: current .env + variant toggles")
    print(f"  Hard cap: {case_timeout_s}s/case, retry on timeout: {CASE_TIMEOUT_RETRY_COUNT}")
    print("  Benchmark-only override: DEEPSEARCH_VISUALIZE_BROWSER=false")
    print(f"{'=' * 60}")

    runner_code = build_runner_code(variant)
    results = list(existing_results)
    total_idx = len(done_ids)
    for task in remaining:
        total_idx += 1
        print(
            f"  [{total_idx}/{len(TASKS)}] {task['id']} ({task['metadata']['domain']}): "
            f"{task['query'][:50]}...",
            flush=True,
        )
        result = run_single_case(
            variant,
            task,
            runner_code,
            case_timeout_s=case_timeout_s,
            monitor_interval_s=monitor_interval_s,
        )

        retry_count = 0
        while retry_count < CASE_TIMEOUT_RETRY_COUNT and _is_timeout_retryable(result):
            retry_count += 1
            print(
                f"    -> timeout detected, retry {retry_count}/{CASE_TIMEOUT_RETRY_COUNT}...",
                flush=True,
            )
            result = run_single_case(
                variant,
                task,
                runner_code,
                case_timeout_s=case_timeout_s,
                monitor_interval_s=monitor_interval_s,
            )

        if retry_count:
            result["retry_count"] = retry_count

        results.append(result)
        status = result.get("status", "?")
        chars = result.get("final_report_chars", 0)
        wall = result.get("wall_time_s", 0)
        retry_suffix = f", retries={retry_count}" if retry_count else ""
        print(
            f"    -> {status} in {wall:.0f}s, report={chars} chars{retry_suffix}",
            flush=True,
        )
        out_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"  [{variant}] Done: {len(results)} cases saved to {out_file}")
    return results


def print_summary(all_data: dict):
    print(f"\n{'=' * 60}")
    print("  v9 PRODUCTION BENCHMARK SUMMARY")
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
            qu = r.get("last_quality_update") or {}
            if qu.get("query_coverage_score") is not None:
                qc_scores.append(qu["query_coverage_score"])
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
    parser = argparse.ArgumentParser(description="v9 Production Benchmark")
    parser.add_argument(
        "--variant",
        choices=["baseline", "optimized"],
        default=None,
        help="Run only a specific variant (default: run both)",
    )
    parser.add_argument(
        "--case-timeout-s",
        type=int,
        default=DEFAULT_CASE_TIMEOUT_S,
        help="Hard cap per case in seconds",
    )
    parser.add_argument(
        "--monitor-interval-s",
        type=int,
        default=DEFAULT_MONITOR_INTERVAL_S,
        help="Seconds between per-case heartbeat logs",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  v9 PRODUCTION BENCHMARK: Baseline vs Optimized")
    print(f"  Cases: {len(TASKS)} | Variants: {len(VARIANTS)}")
    print("  Parameters: current .env + per-variant optimization toggles")
    print("  Selected .env snapshot:")
    for key, value in env_snapshot().items():
        print(f"    {key}={value or '(unset)'}")
    print("=" * 60)

    variants_to_run = [args.variant] if args.variant else list(VARIANTS.keys())
    all_data = {}
    for variant in variants_to_run:
        results = run_variant(
            variant,
            case_timeout_s=args.case_timeout_s,
            monitor_interval_s=args.monitor_interval_s,
        )
        all_data[variant] = results

    for variant in VARIANTS:
        if variant not in all_data:
            f = RESULTS_DIR / f"v9_{variant}.json"
            if f.exists():
                all_data[variant] = json.loads(f.read_text(encoding="utf-8"))

    if all_data:
        print_summary(all_data)


if __name__ == "__main__":
    main()
