"""
v8 Judge: Score all v8 production benchmark reports using LLM-as-Judge.

Reads full reports from v8_baseline.json and v8_optimized.json.
Scores each report 3 times and averages for noise reduction.

Usage:
    conda run -n langchain python eval/ablation/run_v8_judge.py
"""

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.ablation.report_judge import score_report, _get_judge_llm

RESULTS_DIR = ROOT / "eval" / "ablation" / "results"
JUDGE_ROUNDS = 3
DIMS = ("coverage", "depth", "structure", "citations", "overall")


def score_case_multi(
    query: str, report: str, llm: Any, rounds: int = JUDGE_ROUNDS
) -> Dict[str, Any]:
    """Score a single report multiple times and return averaged scores."""
    all_scores: List[Dict[str, Any]] = []

    for r in range(rounds):
        try:
            scores = score_report(query, report, llm=llm)
            if "error" not in scores:
                all_scores.append(scores)
        except Exception as e:
            print(f"      Round {r + 1} error: {e}")

    if not all_scores:
        return {"error": "all_rounds_failed", "rounds_attempted": rounds}

    # Average numeric scores across rounds
    avg_scores: Dict[str, Any] = {}
    for dim in DIMS:
        vals = [s.get(dim, 0) for s in all_scores if isinstance(s.get(dim), (int, float))]
        avg_scores[dim] = round(sum(vals) / len(vals), 2) if vals else 0

    # Collect rationales
    rationales = [s.get("brief_rationale", "") for s in all_scores if s.get("brief_rationale")]

    return {
        **avg_scores,
        "rounds_completed": len(all_scores),
        "rounds_attempted": rounds,
        "per_round_scores": all_scores,
        "rationales": rationales,
    }


def score_variant(variant: str, llm: Any) -> List[Dict[str, Any]]:
    """Score all completed cases in a variant result file."""
    result_file = RESULTS_DIR / f"v8_{variant}.json"
    if not result_file.exists():
        print(f"  [SKIP] {result_file} not found")
        return []

    data = json.loads(result_file.read_text())
    scored: List[Dict[str, Any]] = []

    for case in data:
        cid = case.get("case_id", "?")
        status = case.get("status", "?")

        if status != "completed":
            scored.append({
                "case_id": cid,
                "status": status,
                "judge_scores": None,
            })
            continue

        query = case.get("query", "")
        # Prefer full report; fallback to preview
        report = case.get("final_report") or case.get("final_report_preview") or ""
        if not report:
            scored.append({
                "case_id": cid,
                "status": "no_report",
                "judge_scores": None,
            })
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

        dims = [scores.get(d, 0) for d in DIMS]
        rounds_ok = scores.get("rounds_completed", 0)
        print(
            f"    C={dims[0]:.1f} D={dims[1]:.1f} S={dims[2]:.1f} "
            f"Ci={dims[3]:.1f} O={dims[4]:.1f}  "
            f"({rounds_ok}/{JUDGE_ROUNDS} rounds, {elapsed:.1f}s)",
            flush=True,
        )

    return scored


def main():
    print("=" * 70)
    print("  v8 PRODUCTION BENCHMARK — LLM-as-Judge (full report × 3 rounds)")
    print("=" * 70)

    llm = _get_judge_llm()
    all_scores: Dict[str, List[Dict[str, Any]]] = {}

    for variant in ("baseline", "optimized"):
        print(f"\n{'=' * 50}")
        print(f"  Scoring: {variant}")
        print(f"{'=' * 50}")
        scored = score_variant(variant, llm)
        if scored:
            all_scores[variant] = scored

    # Print summary table
    print(f"\n{'=' * 70}")
    header_fmt = "  {:<12} {:>3} {:>7} {:>7} {:>7} {:>7} {:>8}"
    print(header_fmt.format("Variant", "N", "Cover", "Depth", "Struct", "Cite", "Overall"))
    print(header_fmt.format("-" * 12, "---", "-------", "-------", "-------", "-------", "--------"))

    summary: Dict[str, Any] = {}
    for variant, cases in all_scores.items():
        scored_cases = [
            c for c in cases
            if c.get("judge_scores") and "error" not in c["judge_scores"]
        ]
        if not scored_cases:
            summary[variant] = {"scored": 0, "avg_scores": {}}
            continue

        avgs: Dict[str, float] = {}
        for dim in DIMS:
            vals = [c["judge_scores"].get(dim, 0) for c in scored_cases]
            avgs[dim] = round(sum(vals) / len(vals), 2) if vals else 0

        summary[variant] = {"scored": len(scored_cases), "avg_scores": avgs}
        a = avgs
        print(header_fmt.format(
            variant, len(scored_cases),
            f"{a.get('coverage', 0):.1f}",
            f"{a.get('depth', 0):.1f}",
            f"{a.get('structure', 0):.1f}",
            f"{a.get('citations', 0):.1f}",
            f"{a.get('overall', 0):.1f}",
        ))

    print("=" * 70)

    # Save
    out = {"summary": summary, "details": all_scores}
    out_file = RESULTS_DIR / "v8_judge_scores.json"
    out_file.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\nSaved to {out_file}")


if __name__ == "__main__":
    main()
