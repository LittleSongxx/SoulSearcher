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
    sys.argv[1], mode="{mode}", base_url="asgi", model="", timeout_s=840,
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
            timeout=900,
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

    def _avg(vals):
        return round(sum(vals) / len(vals), 3) if vals else 0

    def _extract_metrics(completed_cases):
        """Extract all quality metrics from completed cases."""
        times = [r["wall_time_s"] for r in completed_cases]
        chars = [r.get("final_report_chars", 0) for r in completed_cases]
        qc_scores, sources = [], []
        claim_verified, claim_total = [], []
        citation_cov = []

        for r in completed_cases:
            # query_coverage from SSE quality_update event
            qu = r.get("last_quality_update") or {}
            if qu.get("query_coverage_score") is not None:
                qc_scores.append(qu["query_coverage_score"])

            # evidence_summary from run metrics API
            es = r.get("evidence_summary") or {}
            if es.get("sources_count") is not None:
                sources.append(es["sources_count"])
            if es.get("query_coverage_score") is not None and not qc_scores:
                qc_scores.append(es["query_coverage_score"])
            if es.get("citation_coverage") is not None:
                citation_cov.append(es["citation_coverage"])

            # claim verifier stats
            cv_total = es.get("claim_verifier_total")
            cv_verified = es.get("claim_verifier_verified")
            if cv_total is not None and cv_total > 0:
                claim_total.append(cv_total)
                claim_verified.append(cv_verified or 0)

        return {
            "avg_time_s": _avg(times),
            "avg_report_chars": round(_avg(chars)),
            "avg_query_coverage": _avg(qc_scores),
            "avg_sources": round(_avg(sources)),
            "avg_citation_coverage": _avg(citation_cov),
            "avg_claim_verified_ratio": (
                round(sum(claim_verified) / max(1, sum(claim_total)), 3)
                if claim_total
                else None
            ),
            "claim_cases_with_data": len(claim_total),
        }

    # Print per-variant aggregates
    header = (
        f"{'Variant':<16} {'Done':>5} {'Time':>6} {'Chars':>7} "
        f"{'QC':>5} {'Src':>5} {'Cite':>5} {'Claims':>7}"
    )
    print(header)
    print("-" * len(header))

    summary = {}
    for variant, results in all_data.items():
        completed = [r for r in results if r.get("status") == "completed"]
        m = _extract_metrics(completed)

        claim_str = (
            f"{m['avg_claim_verified_ratio']:.0%}"
            if m["avg_claim_verified_ratio"] is not None
            else "n/a"
        )
        cite_str = (
            f"{m['avg_citation_coverage']:.2f}" if m["avg_citation_coverage"] else "n/a"
        )

        print(
            f"{variant:<16} {len(completed):>3}/{len(results):<1} "
            f"{m['avg_time_s']:>5.0f}s {m['avg_report_chars']:>6d} "
            f"{m['avg_query_coverage']:>5.2f} {m['avg_sources']:>4d} "
            f"{cite_str:>5} {claim_str:>7}"
        )
        summary[variant] = {
            "completed": len(completed),
            "total": len(results),
            **m,
            "cases": results,
        }

    # Save comparison
    comp_file = RESULTS_DIR / "comparison.json"
    comp_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nComparison saved to {comp_file}")


if __name__ == "__main__":
    main()
