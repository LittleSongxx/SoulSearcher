"""
v9 Judge: Score all v9 production benchmark reports using LLM-as-Judge.

Reads full reports from v9_baseline.json and v9_optimized.json.
Scores each report 3 times and averages for noise reduction.
Results are saved incrementally for safe resume.

Usage:
    conda run -n langchain python eval/ablation/run_v9_judge.py
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.ablation.report_judge import _get_judge_llm, score_report

RESULTS_DIR = ROOT / "eval" / "ablation" / "results"
OUT_FILE = RESULTS_DIR / "v9_judge_scores.json"
JUDGE_ROUNDS = 3
DIMS = ("coverage", "depth", "structure", "citations", "overall")
VARIANTS = ("baseline", "optimized")


def score_case_multi(
    query: str,
    report: str,
    llm: Any,
    rounds: int = JUDGE_ROUNDS,
) -> Dict[str, Any]:
    all_scores: List[Dict[str, Any]] = []
    for r in range(rounds):
        try:
            scores = score_report(query, report, llm=llm)
            if "error" not in scores:
                all_scores.append(scores)
        except Exception as e:
            print(f"      Round {r + 1} error: {e}", flush=True)

    if not all_scores:
        return {"error": "all_rounds_failed", "rounds_attempted": rounds}

    avg_scores: Dict[str, Any] = {}
    for dim in DIMS:
        vals = [s.get(dim, 0) for s in all_scores if isinstance(s.get(dim), (int, float))]
        avg_scores[dim] = round(sum(vals) / len(vals), 2) if vals else 0

    rationales = [s.get("brief_rationale", "") for s in all_scores if s.get("brief_rationale")]
    return {
        **avg_scores,
        "rounds_completed": len(all_scores),
        "rounds_attempted": rounds,
        "per_round_scores": all_scores,
        "rationales": rationales,
    }


def _load_existing() -> Dict[str, List[Dict[str, Any]]]:
    if not OUT_FILE.exists():
        return {}
    data = json.loads(OUT_FILE.read_text(encoding="utf-8"))
    details = data.get("details", {})
    return details if isinstance(details, dict) else {}


def _save(details: Dict[str, List[Dict[str, Any]]]) -> None:
    summary: Dict[str, Any] = {}
    for variant, cases in details.items():
        scored_cases = [
            c for c in cases
            if c.get("judge_scores") and "error" not in c.get("judge_scores", {})
        ]
        avgs: Dict[str, float] = {}
        for dim in DIMS:
            vals = [c["judge_scores"].get(dim, 0) for c in scored_cases]
            avgs[dim] = round(sum(vals) / len(vals), 2) if vals else 0.0
        summary[variant] = {"scored": len(scored_cases), "avg_scores": avgs}
    OUT_FILE.write_text(
        json.dumps({"summary": summary, "details": details}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _existing_entry(existing: List[Dict[str, Any]], case_id: str) -> Optional[Dict[str, Any]]:
    for entry in existing:
        if entry.get("case_id") == case_id and entry.get("judge_scores"):
            return entry
    return None


def score_variant(
    variant: str,
    llm: Any,
    details: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    result_file = RESULTS_DIR / f"v9_{variant}.json"
    if not result_file.exists():
        print(f"  [SKIP] {result_file} not found", flush=True)
        return details.get(variant, [])

    data = json.loads(result_file.read_text(encoding="utf-8"))
    existing = details.get(variant, [])
    scored: List[Dict[str, Any]] = []

    for case in data:
        cid = case.get("case_id", "?")
        previous = _existing_entry(existing, cid)
        if previous:
            scored.append(previous)
            print(f"  Judging {cid}: already scored, skipping.", flush=True)
            continue

        status = case.get("status", "?")
        if status != "completed":
            entry = {
                "case_id": cid,
                "status": status,
                "judge_scores": None,
            }
            scored.append(entry)
            details[variant] = scored
            _save(details)
            continue

        query = case.get("query", "")
        report = case.get("final_report") or case.get("final_report_preview") or ""
        if not report:
            entry = {
                "case_id": cid,
                "status": "no_report",
                "judge_scores": None,
            }
            scored.append(entry)
            details[variant] = scored
            _save(details)
            continue

        print(f"  Judging {cid} ({len(report)} chars, {JUDGE_ROUNDS} rounds)...", flush=True)
        t0 = time.monotonic()
        scores = score_case_multi(query, report, llm, rounds=JUDGE_ROUNDS)
        elapsed = time.monotonic() - t0
        entry = {
            "case_id": cid,
            "domain": case.get("domain", ""),
            "query": query[:80],
            "status": "scored",
            "report_chars": len(report),
            "judge_scores": scores,
            "judge_time_s": round(elapsed, 1),
        }
        scored.append(entry)
        details[variant] = scored
        _save(details)

        dims = [scores.get(d, 0) for d in DIMS]
        rounds_ok = scores.get("rounds_completed", 0)
        print(
            f"    C={dims[0]:.1f} D={dims[1]:.1f} S={dims[2]:.1f} "
            f"Ci={dims[3]:.1f} O={dims[4]:.1f}  "
            f"({rounds_ok}/{JUDGE_ROUNDS} rounds, {elapsed:.1f}s)",
            flush=True,
        )

    details[variant] = scored
    _save(details)
    return scored


def print_summary(details: Dict[str, List[Dict[str, Any]]]) -> None:
    print(f"\n{'=' * 70}")
    header_fmt = "  {:<12} {:>3} {:>7} {:>7} {:>7} {:>7} {:>8}"
    print(header_fmt.format("Variant", "N", "Cover", "Depth", "Struct", "Cite", "Overall"))
    print(header_fmt.format("-" * 12, "---", "-------", "-------", "-------", "-------", "--------"))
    for variant in VARIANTS:
        cases = details.get(variant, [])
        scored_cases = [
            c for c in cases
            if c.get("judge_scores") and "error" not in c.get("judge_scores", {})
        ]
        avgs: Dict[str, float] = {}
        for dim in DIMS:
            vals = [c["judge_scores"].get(dim, 0) for c in scored_cases]
            avgs[dim] = round(sum(vals) / len(vals), 2) if vals else 0.0
        print(header_fmt.format(
            variant,
            len(scored_cases),
            f"{avgs.get('coverage', 0):.1f}",
            f"{avgs.get('depth', 0):.1f}",
            f"{avgs.get('structure', 0):.1f}",
            f"{avgs.get('citations', 0):.1f}",
            f"{avgs.get('overall', 0):.1f}",
        ))
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="v9 Production Benchmark Judge")
    parser.add_argument(
        "--variant",
        choices=list(VARIANTS),
        default=None,
        help="Score only one variant",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("  v9 PRODUCTION BENCHMARK — LLM-as-Judge (full report × 3 rounds)")
    print("=" * 70)

    llm = _get_judge_llm()
    details = _load_existing()
    variants = (args.variant,) if args.variant else VARIANTS
    for variant in variants:
        print(f"\n{'=' * 50}")
        print(f"  Scoring: {variant}")
        print(f"{'=' * 50}")
        score_variant(variant, llm, details)

    print_summary(details)
    print(f"\nSaved to {OUT_FILE}")


if __name__ == "__main__":
    main()
