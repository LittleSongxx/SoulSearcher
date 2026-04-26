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

CASE_IDS = (
    "v7_010",
    "v7_008",
    "v7_001",
    "v7_006",
    "v7_004",
)
CASE_ID_SET = set(CASE_IDS)
TASKS: List[dict] = []
with open(BENCH_FILE, encoding="utf-8") as f:
    for line in f:
        if line.strip():
            task = json.loads(line)
            if task.get("id") in CASE_ID_SET:
                TASKS.append(task)
TASKS.sort(key=lambda item: CASE_IDS.index(item["id"]))

RUNNER_ENV = {
    "BENCHMARK_SKIP_CLIENT_CLOSE": "true",
    "DEEPSEARCH_VISUALIZE_BROWSER": "false",
}

VARIANTS = {
    "optimized_full": {
        "OBSERVATION_MASKING": "true",
        "OBSERVATION_MASKING_WINDOW": "5",
        "STRIP_TOOL_MESSAGES": "false",
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
        "OBSERVATION_MASKING_WINDOW": "5",
        "STRIP_TOOL_MESSAGES": "false",
        "CONTEXT_OFFLOADING": "false",
        "AGENT_REFLEXION_ENABLED": "true",
        "TREE_BACKTRACK_ENABLED": "true",
        "DYNAMIC_TOOL_PRUNING": "true",
    },
    "no_reflexion": {
        "OBSERVATION_MASKING": "true",
        "OBSERVATION_MASKING_WINDOW": "5",
        "STRIP_TOOL_MESSAGES": "false",
        "CONTEXT_OFFLOADING": "true",
        "AGENT_REFLEXION_ENABLED": "false",
        "TREE_BACKTRACK_ENABLED": "true",
        "DYNAMIC_TOOL_PRUNING": "true",
    },
    "no_backtrack": {
        "OBSERVATION_MASKING": "true",
        "OBSERVATION_MASKING_WINDOW": "5",
        "STRIP_TOOL_MESSAGES": "false",
        "CONTEXT_OFFLOADING": "true",
        "AGENT_REFLEXION_ENABLED": "true",
        "TREE_BACKTRACK_ENABLED": "false",
        "DYNAMIC_TOOL_PRUNING": "true",
    },
    "no_tool_pruning": {
        "OBSERVATION_MASKING": "true",
        "OBSERVATION_MASKING_WINDOW": "5",
        "STRIP_TOOL_MESSAGES": "false",
        "CONTEXT_OFFLOADING": "true",
        "AGENT_REFLEXION_ENABLED": "true",
        "TREE_BACKTRACK_ENABLED": "true",
        "DYNAMIC_TOOL_PRUNING": "false",
    },
    "baseline_equivalent": {
        "OBSERVATION_MASKING": "false",
        "STRIP_TOOL_MESSAGES": "false",
        "CONTEXT_OFFLOADING": "false",
        "AGENT_REFLEXION_ENABLED": "false",
        "TREE_BACKTRACK_ENABLED": "false",
        "DYNAMIC_TOOL_PRUNING": "false",
    },
    "context_only": {
        "OBSERVATION_MASKING": "true",
        "OBSERVATION_MASKING_WINDOW": "5",
        "STRIP_TOOL_MESSAGES": "false",
        "CONTEXT_OFFLOADING": "true",
        "AGENT_REFLEXION_ENABLED": "false",
        "TREE_BACKTRACK_ENABLED": "false",
        "DYNAMIC_TOOL_PRUNING": "false",
    },
    "search_planning_only": {
        "OBSERVATION_MASKING": "false",
        "STRIP_TOOL_MESSAGES": "false",
        "CONTEXT_OFFLOADING": "false",
        "AGENT_REFLEXION_ENABLED": "true",
        "TREE_BACKTRACK_ENABLED": "true",
        "DYNAMIC_TOOL_PRUNING": "true",
    },
}

VARIANT_GROUPS = {
    "core": (
        "optimized_full",
        "no_obs_masking",
        "no_offloading",
        "no_reflexion",
        "no_backtrack",
        "no_tool_pruning",
        "baseline_equivalent",
    ),
    "split": (
        "context_only",
        "search_planning_only",
    ),
    "all": tuple(VARIANTS.keys()),
}

RUNNER_TEMPLATE = """
import asyncio, json, os, sys
sys.path.insert(0, {root})
{env_overrides}
from scripts.benchmark_deep_research import _execute_research_case
result = asyncio.run(_execute_research_case(
    sys.argv[1], mode=\"auto\", base_url=\"asgi\", model=\"\", timeout_s=7200,
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
    "DEEPSEARCH_ENABLE_RESEARCH_FETCHER",
    "RESEARCH_FETCH_TIMEOUT_S",
    "RESEARCH_FETCH_CONCURRENCY",
    "RESEARCH_FETCH_CACHE_TTL_S",
    "RESEARCH_FETCH_RENDER_MODE",
    "CRAWLER_HEADLESS",
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
    for key, value in merged.items():
        env_lines.append(f'os.environ[{json.dumps(key)}] = {json.dumps(value)}')
    env_block = "\n".join(env_lines)
    return RUNNER_TEMPLATE.format(root=json.dumps(str(ROOT)), env_overrides=env_block)


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
                    f"    ... still running {elapsed:.0f}s (hard cap {case_timeout_s}s)",
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
                        f"    ! KeyboardInterrupt received at {elapsed:.0f}s; child still running, continuing monitor ({interrupt_count})",
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
    completed = [row for row in existing if row.get("status") == "completed"]
    done_ids = {row["case_id"] for row in completed if row.get("case_id")}
    return completed, done_ids


def result_path(variant: str) -> Path:
    return RESULTS_DIR / f"v9_ablation_{variant}.json"


def run_variant(variant: str, case_timeout_s: int, monitor_interval_s: int) -> List[dict]:
    out_file = result_path(variant)
    existing_results, done_ids = _load_existing_completed(out_file)
    remaining = [task for task in TASKS if task["id"] not in done_ids]
    if not remaining:
        print(f"\n[{variant}] All {len(TASKS)} cases already completed, skipping.")
        return existing_results

    print(f"\n{'=' * 60}")
    print(f"  VARIANT: {variant}")
    toggled = VARIANTS[variant]
    on_flags = [key for key, value in toggled.items() if value.lower() == "true"]
    off_flags = [key for key, value in toggled.items() if value.lower() == "false"]
    print(f"  ON:  {', '.join(on_flags) or '(none)'}")
    print(f"  OFF: {', '.join(off_flags) or '(none)'}")
    print(f"  Cases: {len(remaining)} remaining / {len(TASKS)} total")
    print(f"  Hard cap: {case_timeout_s}s/case, retry on timeout: {CASE_TIMEOUT_RETRY_COUNT}")
    print(f"{'=' * 60}")

    runner_code = build_runner_code(variant)
    results = list(existing_results)
    total_idx = len(done_ids)
    for task in remaining:
        total_idx += 1
        print(
            f"  [{total_idx}/{len(TASKS)}] {task['id']} ({task['metadata']['domain']}): {task['query'][:55]}...",
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
        print(f"    -> {status} in {wall:.0f}s, report={chars} chars{retry_suffix}", flush=True)
        out_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"  [{variant}] Done: {len(results)} cases saved to {out_file}")
    return results


def print_summary(all_data: Dict[str, List[dict]]) -> None:
    print(f"\n{'=' * 88}")
    print("  v9 TOGGLE ABLATION SUMMARY")
    print(f"{'=' * 88}\n")

    def _avg(vals):
        return round(sum(vals) / len(vals), 3) if vals else 0

    header = f"{'Variant':<22} {'Done':>6} {'Time':>7} {'Chars':>8} {'QC':>6} {'Src':>5} {'UC':>4} {'CitCov':>7}"
    print(header)
    print("-" * len(header))

    for variant, results in all_data.items():
        completed = [row for row in results if row.get("status") == "completed"]
        times = [row["wall_time_s"] for row in completed]
        chars = [row.get("final_report_chars", 0) for row in completed]
        qc_scores = []
        sources = []
        uc_counts = []
        cit_cov = []
        for row in completed:
            qu = row.get("last_quality_update") or {}
            if qu.get("query_coverage_score") is not None:
                qc_scores.append(qu["query_coverage_score"])
            es = row.get("evidence_summary") or {}
            if es.get("sources_count") is not None:
                sources.append(es["sources_count"])
            if es.get("query_coverage_score") is not None and not qc_scores:
                qc_scores.append(es["query_coverage_score"])
            if es.get("unsupported_claims_count") is not None:
                uc_counts.append(es["unsupported_claims_count"])
            if es.get("citation_coverage") is not None:
                cit_cov.append(es["citation_coverage"])
        print(
            f"{variant:<22} {len(completed):>3}/{len(results):<2} {_avg(times):>6.0f}s {_avg(chars):>7.0f} {_avg(qc_scores):>6.2f} {_avg(sources):>4.0f} {_avg(uc_counts):>4.1f} {_avg(cit_cov):>6.2f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="v9 optimization-toggle ablation")
    parser.add_argument("--variant", choices=list(VARIANTS), default=None)
    parser.add_argument("--variant-group", choices=list(VARIANT_GROUPS), default="core")
    parser.add_argument("--case-timeout-s", type=int, default=DEFAULT_CASE_TIMEOUT_S)
    parser.add_argument("--monitor-interval-s", type=int, default=DEFAULT_MONITOR_INTERVAL_S)
    args = parser.parse_args()

    print("=" * 60)
    print("  v9 TOGGLE ABLATION")
    print(f"  Cases: {len(TASKS)} | Case IDs: {', '.join(CASE_IDS)}")
    print("  Parameters: current .env + per-variant optimization toggles")
    print("  Selected .env snapshot:")
    for key, value in env_snapshot().items():
        print(f"    {key}={value or '(unset)'}")
    print("=" * 60)

    variants_to_run = [args.variant] if args.variant else list(VARIANT_GROUPS[args.variant_group])
    all_data: Dict[str, List[dict]] = {}
    for variant in variants_to_run:
        all_data[variant] = run_variant(
            variant,
            case_timeout_s=args.case_timeout_s,
            monitor_interval_s=args.monitor_interval_s,
        )

    for variant in VARIANTS:
        if variant not in all_data:
            path = result_path(variant)
            if path.exists():
                all_data[variant] = json.loads(path.read_text(encoding="utf-8"))

    if all_data:
        print_summary(all_data)


if __name__ == "__main__":
    main()
