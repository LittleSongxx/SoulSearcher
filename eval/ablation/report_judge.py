"""
LLM-as-Judge: offline quality scorer for ablation reports.

Scores each report on 5 dimensions (0-10) using a consistent LLM evaluator,
enabling apples-to-apples comparison across ablation variants.

Usage:
    python -m eval.ablation.report_judge eval/ablation/results/full.json
    python -m eval.ablation.report_judge --all  # score all variant results
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = ROOT / "eval" / "ablation" / "results"

JUDGE_PROMPT = """You are an expert research report evaluator.
Score the following research report on these 5 dimensions (each 0-10):

1. **Coverage** (0-10): Does the report thoroughly answer the query? Does it address all key aspects, subtopics, and perspectives?
2. **Depth** (0-10): Does the report provide deep analysis with specific data, examples, and expert insights — rather than surface-level summaries?
3. **Structure** (0-10): Is the report well-organized with clear headings, logical flow, smooth transitions, and a coherent narrative?
4. **Citations** (0-10): Does the report reference specific sources, data points, and evidence? Are citations properly attributed?
5. **Overall** (0-10): Holistic quality as a professional research report. Would you trust it for decision-making?

Query: {query}

Report:
{report}

Respond with ONLY a JSON object (no markdown fencing):
{{"coverage": <int>, "depth": <int>, "structure": <int>, "citations": <int>, "overall": <int>, "brief_rationale": "<1-2 sentences>"}}"""


def _get_judge_llm():
    """Get the LLM for judging (reuse project's model config)."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from common.config import settings
    from langchain_openai import ChatOpenAI

    model = getattr(settings, "primary_model", None) or "deepseek-chat"
    base_url = getattr(settings, "openai_base_url", None)
    api_key = getattr(settings, "openai_api_key", None)
    kwargs = {}
    if base_url:
        kwargs["base_url"] = base_url
    if api_key:
        kwargs["api_key"] = api_key
    return ChatOpenAI(model=model, temperature=0.1, **kwargs)


def score_report(query: str, report: str, llm: Any = None) -> Dict[str, Any]:
    """Score a single report using LLM-as-Judge."""
    if llm is None:
        llm = _get_judge_llm()

    # Truncate very long reports to avoid excessive cost
    report_text = report[:16000] if len(report) > 16000 else report

    from langchain_core.messages import HumanMessage

    response = llm.invoke(
        [HumanMessage(content=JUDGE_PROMPT.format(query=query, report=report_text))]
    )
    raw = getattr(response, "content", "") or ""

    # Parse JSON from response
    import re

    json_match = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
    if not json_match:
        logger.warning(f"Failed to parse judge response: {raw[:200]}")
        return {"error": "parse_failed", "raw": raw[:500]}

    try:
        scores = json.loads(json_match.group())
        # Validate
        for dim in ("coverage", "depth", "structure", "citations", "overall"):
            val = scores.get(dim)
            if not isinstance(val, (int, float)) or val < 0 or val > 10:
                scores[dim] = 0
        return scores
    except json.JSONDecodeError as e:
        logger.warning(f"JSON decode error: {e}")
        return {"error": "json_decode_failed", "raw": raw[:500]}


def score_variant_file(variant_file: Path, llm: Any = None) -> List[Dict[str, Any]]:
    """Score all completed reports in a variant result file."""
    if not variant_file.exists():
        logger.warning(f"File not found: {variant_file}")
        return []

    data = json.loads(variant_file.read_text())
    if llm is None:
        llm = _get_judge_llm()

    scored = []
    for case in data:
        if case.get("status") != "completed":
            scored.append(
                {
                    "case_id": case.get("case_id"),
                    "status": case.get("status"),
                    "judge_scores": None,
                }
            )
            continue

        query = case.get("query", "")
        report = case.get("final_report_preview", "")
        # If full report not in preview, use what we have
        if not report:
            scored.append(
                {
                    "case_id": case.get("case_id"),
                    "status": "no_report",
                    "judge_scores": None,
                }
            )
            continue

        t0 = time.monotonic()
        scores = score_report(query, report, llm=llm)
        elapsed = time.monotonic() - t0

        entry = {
            "case_id": case.get("case_id"),
            "query": query[:80],
            "status": "scored",
            "judge_scores": scores,
            "judge_time_s": round(elapsed, 1),
        }
        scored.append(entry)

        dims = [
            scores.get(d, 0)
            for d in ("coverage", "depth", "structure", "citations", "overall")
        ]
        print(
            f"  {case.get('case_id', '?'):12s} "
            f"C={dims[0]:2d} D={dims[1]:2d} S={dims[2]:2d} Ci={dims[3]:2d} O={dims[4]:2d}  "
            f"({elapsed:.1f}s)",
            flush=True,
        )

    return scored


def score_all_variants(variant_names: Optional[List[str]] = None) -> Dict[str, Any]:
    """Score all variants and save comparison."""
    llm = _get_judge_llm()
    all_scores: Dict[str, Any] = {}

    files = sorted(RESULTS_DIR.glob("*.json"))
    for f in files:
        if f.name in ("comparison.json", "judge_scores.json"):
            continue
        variant = f.stem
        if variant_names and variant not in variant_names:
            continue

        print(f"\n=== Judging: {variant} ===")
        scored = score_variant_file(f, llm=llm)
        all_scores[variant] = scored

    # Compute aggregates
    summary: Dict[str, Any] = {}
    dims = ("coverage", "depth", "structure", "citations", "overall")
    for variant, cases in all_scores.items():
        scored_cases = [
            c
            for c in cases
            if c.get("judge_scores") and "error" not in c["judge_scores"]
        ]
        if not scored_cases:
            summary[variant] = {"scored": 0, "avg_scores": {}}
            continue

        avgs = {}
        for dim in dims:
            vals = [c["judge_scores"].get(dim, 0) for c in scored_cases]
            avgs[dim] = round(sum(vals) / len(vals), 2) if vals else 0

        summary[variant] = {
            "scored": len(scored_cases),
            "avg_scores": avgs,
        }

    # Print comparison
    print(f"\n{'='*70}")
    print(
        f"  {'Variant':<16} {'N':>3} {'Cover':>6} {'Depth':>6} {'Struct':>6} {'Cite':>6} {'Overall':>7}"
    )
    print(
        f"  {'-'*16} {'---':>3} {'------':>6} {'------':>6} {'------':>6} {'------':>6} {'-------':>7}"
    )
    for variant, info in summary.items():
        n = info["scored"]
        a = info["avg_scores"]
        print(
            f"  {variant:<16} {n:>3} "
            f"{a.get('coverage', 0):>6.1f} {a.get('depth', 0):>6.1f} "
            f"{a.get('structure', 0):>6.1f} {a.get('citations', 0):>6.1f} "
            f"{a.get('overall', 0):>7.1f}"
        )
    print(f"{'='*70}")

    # Save
    output = {
        "summary": summary,
        "details": all_scores,
    }
    out_file = RESULTS_DIR / "judge_scores.json"
    out_file.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"\nJudge scores saved to {out_file}")

    return output


def main():
    parser = argparse.ArgumentParser(description="LLM-as-Judge report scorer")
    parser.add_argument("files", nargs="*", help="Variant result files to score")
    parser.add_argument("--all", action="store_true", help="Score all variants")
    parser.add_argument("--variants", nargs="*", help="Specific variant names")
    args = parser.parse_args()

    if args.all or args.variants:
        score_all_variants(args.variants)
    elif args.files:
        llm = _get_judge_llm()
        for f in args.files:
            p = Path(f)
            if not p.exists():
                print(f"File not found: {f}")
                continue
            print(f"\n=== Judging: {p.stem} ===")
            scored = score_variant_file(p, llm=llm)
            out = p.with_suffix(".judge.json")
            out.write_text(json.dumps(scored, ensure_ascii=False, indent=2))
            print(f"Saved to {out}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
