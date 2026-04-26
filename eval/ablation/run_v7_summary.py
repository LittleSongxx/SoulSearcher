"""
v7 Summary: Aggregate all 13 metrics from production benchmark results + judge scores.

Generates eval/ablation/v7_production_report.md with:
  - Overall comparison table (13 metrics)
  - Per-domain breakdown
  - Post-hoc completion rate at multiple time thresholds
  - Per-case detail table

Usage:
    conda run -n langchain python eval/ablation/run_v7_summary.py
    conda run -n langchain python eval/ablation/run_v7_summary.py --thresholds 600,900,1200
"""

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = ROOT / "eval" / "ablation" / "results"
REPORT_FILE = ROOT / "eval" / "ablation" / "v7_production_report.md"
BENCH_FILE = ROOT / "eval" / "benchmarks" / "v7_tasks.jsonl"

DIMS = ("coverage", "depth", "structure", "citations", "overall")
DEFAULT_THRESHOLDS = [600, 900, 1200, 1800]


def _avg(vals: List[float]) -> float:
    return round(sum(vals) / len(vals), 3) if vals else 0.0


def _pct_change(new: float, old: float) -> str:
    if old == 0:
        return "N/A"
    pct = (new - old) / abs(old) * 100
    arrow = "⬆️" if pct > 0 else "⬇️" if pct < 0 else "—"
    return f"{pct:+.1f}% {arrow}"


def load_results(variant: str) -> List[Dict[str, Any]]:
    f = RESULTS_DIR / f"v7_{variant}.json"
    if not f.exists():
        print(f"WARNING: {f} not found")
        return []
    return json.loads(f.read_text())


def load_judge_scores() -> Dict[str, List[Dict[str, Any]]]:
    f = RESULTS_DIR / "v7_judge_scores.json"
    if not f.exists():
        print(f"WARNING: {f} not found — judge scores will be empty")
        return {}
    data = json.loads(f.read_text())
    return data.get("details", {})


def extract_metrics(cases: List[Dict[str, Any]], judge_cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract all 13 metrics from run results + judge scores."""
    completed = [r for r in cases if r.get("status") == "completed"]

    times = [r["wall_time_s"] for r in completed]
    chars = [r.get("final_report_chars", 0) for r in completed]

    qc_scores, sources, uc_counts = [], [], []
    freshness, cit_cov = [], []
    cv_total, cv_verified, cv_unsupported, cv_contradicted = [], [], [], []

    for r in completed:
        # From SSE quality_update
        qu = r.get("last_quality_update") or {}
        if qu.get("query_coverage_score") is not None:
            qc_scores.append(float(qu["query_coverage_score"]))

        # From run metrics API
        es = r.get("evidence_summary") or {}
        if es.get("sources_count") is not None:
            sources.append(int(es["sources_count"]))
        if es.get("query_coverage_score") is not None and not qc_scores:
            qc_scores.append(float(es["query_coverage_score"]))
        if es.get("unsupported_claims_count") is not None:
            uc_counts.append(int(es["unsupported_claims_count"]))
        if es.get("freshness_ratio_30d") is not None:
            freshness.append(float(es["freshness_ratio_30d"]))
        if es.get("citation_coverage") is not None:
            cit_cov.append(float(es["citation_coverage"]))
        if es.get("claim_verifier_total") is not None:
            cv_total.append(int(es["claim_verifier_total"]))
            cv_verified.append(int(es.get("claim_verifier_verified", 0)))
            cv_unsupported.append(int(es.get("claim_verifier_unsupported", 0)))
            cv_contradicted.append(int(es.get("claim_verifier_contradicted", 0)))

    # Judge scores
    judge_avgs: Dict[str, float] = {}
    scored_judge = [
        c for c in (judge_cases or [])
        if c.get("judge_scores") and "error" not in c.get("judge_scores", {})
    ]
    for dim in DIMS:
        vals = [c["judge_scores"].get(dim, 0) for c in scored_judge]
        judge_avgs[dim] = round(sum(vals) / len(vals), 2) if vals else 0.0

    return {
        "total": len(cases),
        "completed": len(completed),
        "completion_rate": f"{len(completed)}/{len(cases)}",
        "avg_time_s": _avg(times),
        "avg_report_chars": round(_avg(chars)),
        "avg_qc": _avg(qc_scores),
        "avg_sources": round(_avg(sources), 1),
        "avg_uc": _avg(uc_counts),
        "avg_freshness_30d": _avg(freshness),
        "avg_citation_coverage": _avg(cit_cov),
        "judge_n": len(scored_judge),
        **{f"judge_{dim}": judge_avgs.get(dim, 0) for dim in DIMS},
        # Raw lists for per-threshold analysis
        "_times": times,
        "_all_cases": cases,
    }


def completion_at_threshold(cases: List[Dict[str, Any]], threshold_s: float) -> str:
    """Compute completion rate at a given time threshold."""
    completed = [r for r in cases if r.get("status") == "completed"]
    within = [r for r in completed if r.get("wall_time_s", float("inf")) <= threshold_s]
    return f"{len(within)}/{len(cases)}"


def build_report(
    baseline_metrics: Dict[str, Any],
    optimized_metrics: Dict[str, Any],
    baseline_cases: List[Dict[str, Any]],
    optimized_cases: List[Dict[str, Any]],
    baseline_judge: List[Dict[str, Any]],
    optimized_judge: List[Dict[str, Any]],
    thresholds: List[int],
) -> str:
    bm = baseline_metrics
    om = optimized_metrics
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        "# v7 生产级 Benchmark 报告：Baseline vs Optimized",
        "",
        f"**日期**: {now}  ",
        "**分支**: dev2  ",
        "**模型**: deepseek-v4-flash（两组相同）  ",
        "**参数**: 生产默认值 (epochs=3, depth=2, branches=4, queries/branch=3, results/query=5, max_searches=30)  ",
        "**超时**: 无（事后阈值判定完成率）  ",
        "**测试集**: v7_tasks.jsonl — 10 个全新 case（5 领域 × 2）  ",
        "**Judge**: deepseek-v4-flash, 完整报告, 每 case 3 次评分取平均",
        "",
        "---",
        "",
        "## 一、总览对比表（13 项指标）",
        "",
        "### 1.1 运行指标",
        "",
        "| 指标 | Baseline | Optimized | 变化 |",
        "|---|---|---|---|",
        f"| **完成率** | {bm['completion_rate']} | {om['completion_rate']} | — |",
        f"| **平均耗时** | {bm['avg_time_s']:.0f}s | {om['avg_time_s']:.0f}s | {_pct_change(om['avg_time_s'], bm['avg_time_s'])} |",
        f"| **报告长度** | {bm['avg_report_chars']:,} chars | {om['avg_report_chars']:,} chars | {_pct_change(om['avg_report_chars'], bm['avg_report_chars'])} |",
        f"| **Query Coverage** | {bm['avg_qc']:.3f} | {om['avg_qc']:.3f} | {_pct_change(om['avg_qc'], bm['avg_qc'])} |",
        f"| **引用源数** | {bm['avg_sources']:.1f} | {om['avg_sources']:.1f} | {_pct_change(om['avg_sources'], bm['avg_sources'])} |",
        f"| **无支撑声明数 (UC)** | {bm['avg_uc']:.1f} | {om['avg_uc']:.1f} | {_pct_change(om['avg_uc'], bm['avg_uc'])} |",
        f"| **30天新鲜度** | {bm['avg_freshness_30d']:.3f} | {om['avg_freshness_30d']:.3f} | {_pct_change(om['avg_freshness_30d'], bm['avg_freshness_30d'])} |",
        f"| **引用覆盖率** | {bm['avg_citation_coverage']:.3f} | {om['avg_citation_coverage']:.3f} | {_pct_change(om['avg_citation_coverage'], bm['avg_citation_coverage'])} |",
        "",
        "### 1.2 LLM-as-Judge 评分（完整报告 × 3 次平均）",
        "",
        f"| 维度 | Baseline (N={bm['judge_n']}) | Optimized (N={om['judge_n']}) | 变化 |",
        "|---|---|---|---|",
    ]

    for dim in DIMS:
        bv = bm[f"judge_{dim}"]
        ov = om[f"judge_{dim}"]
        lines.append(f"| **{dim.capitalize()}** | {bv:.2f} | {ov:.2f} | {_pct_change(ov, bv)} |")

    # Post-hoc completion rate at thresholds
    lines += [
        "",
        "### 1.3 事后完成率（不同耗时阈值）",
        "",
        "| 阈值 | Baseline | Optimized |",
        "|---|---|---|",
    ]
    for t in thresholds:
        bc = completion_at_threshold(baseline_cases, t)
        oc = completion_at_threshold(optimized_cases, t)
        lines.append(f"| {t}s ({t // 60}min) | {bc} | {oc} |")

    # Per-domain breakdown
    lines += [
        "",
        "---",
        "",
        "## 二、分领域对比",
        "",
    ]

    # Collect domains
    domains = sorted(set(
        r.get("domain", "unknown") for r in baseline_cases + optimized_cases
    ))

    for domain in domains:
        b_domain = [r for r in baseline_cases if r.get("domain") == domain and r.get("status") == "completed"]
        o_domain = [r for r in optimized_cases if r.get("domain") == domain and r.get("status") == "completed"]

        b_times = [r["wall_time_s"] for r in b_domain]
        o_times = [r["wall_time_s"] for r in o_domain]
        b_chars = [r.get("final_report_chars", 0) for r in b_domain]
        o_chars = [r.get("final_report_chars", 0) for r in o_domain]

        # Judge scores for this domain
        bj_domain = [c for c in (baseline_judge or []) if c.get("domain") == domain and c.get("judge_scores") and "error" not in c.get("judge_scores", {})]
        oj_domain = [c for c in (optimized_judge or []) if c.get("domain") == domain and c.get("judge_scores") and "error" not in c.get("judge_scores", {})]

        def _domain_judge_avg(judge_list, dim):
            vals = [c["judge_scores"].get(dim, 0) for c in judge_list]
            return round(sum(vals) / len(vals), 2) if vals else 0

        lines += [
            f"### {domain.capitalize()}",
            "",
            f"| 指标 | Baseline (N={len(b_domain)}) | Optimized (N={len(o_domain)}) |",
            "|---|---|---|",
            f"| 平均耗时 | {_avg(b_times):.0f}s | {_avg(o_times):.0f}s |",
            f"| 报告长度 | {_avg(b_chars):.0f} | {_avg(o_chars):.0f} |",
            f"| J-Overall | {_domain_judge_avg(bj_domain, 'overall'):.2f} | {_domain_judge_avg(oj_domain, 'overall'):.2f} |",
            f"| J-Citations | {_domain_judge_avg(bj_domain, 'citations'):.2f} | {_domain_judge_avg(oj_domain, 'citations'):.2f} |",
            "",
        ]

    # Per-case detail table
    lines += [
        "---",
        "",
        "## 三、逐 Case 详情",
        "",
        "| Case | Domain | Variant | 耗时 | 长度 | QC | Src | UC | J-Overall |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    # Build judge lookup
    judge_lookup: Dict[str, Dict[str, Any]] = {}
    for variant_name, judge_list in [("baseline", baseline_judge), ("optimized", optimized_judge)]:
        for jc in (judge_list or []):
            key = f"{variant_name}_{jc.get('case_id', '')}"
            judge_lookup[key] = jc

    for variant_name, cases in [("baseline", baseline_cases), ("optimized", optimized_cases)]:
        for r in cases:
            cid = r.get("case_id", "?")
            domain = r.get("domain", "?")
            status = r.get("status", "?")

            if status != "completed":
                lines.append(f"| {cid} | {domain} | {variant_name} | ❌ {status} | — | — | — | — | — |")
                continue

            wall = r.get("wall_time_s", 0)
            chars = r.get("final_report_chars", 0)

            # QC
            qu = r.get("last_quality_update") or {}
            es = r.get("evidence_summary") or {}
            qc = qu.get("query_coverage_score") or es.get("query_coverage_score") or 0
            src = es.get("sources_count", 0)
            uc = es.get("unsupported_claims_count", 0)

            # Judge
            jkey = f"{variant_name}_{cid}"
            jc = judge_lookup.get(jkey, {})
            js = jc.get("judge_scores", {}) if jc else {}
            jo = js.get("overall", "—") if js and "error" not in js else "—"

            lines.append(
                f"| {cid} | {domain} | {variant_name} | {wall:.0f}s | {chars:,} | {qc:.2f} | {src} | {uc} | {jo} |"
            )

    # Appendix
    lines += [
        "",
        "---",
        "",
        "## 附录：测试环境",
        "",
        "| 项目 | 值 |",
        "|---|---|",
        "| 分支 | dev2 |",
        "| 模型 | deepseek-v4-flash |",
        "| 参数 | epochs=3, depth=2, branches=4, queries/branch=3, results/query=5, max_searches=30 |",
        "| 超时 | 无（事后阈值判定） |",
        "| 测试集 | eval/benchmarks/v7_tasks.jsonl |",
        "| Judge | deepseek-v4-flash, 完整报告, 3 次评分取平均 |",
        f"| 数据文件 | v7_baseline.json, v7_optimized.json, v7_judge_scores.json |",
    ]

    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="v7 Summary Report Generator")
    parser.add_argument(
        "--thresholds",
        default=",".join(str(t) for t in DEFAULT_THRESHOLDS),
        help="Comma-separated time thresholds in seconds for post-hoc completion rate",
    )
    args = parser.parse_args()
    thresholds = [int(t.strip()) for t in args.thresholds.split(",")]

    print("Loading results...")
    baseline_cases = load_results("baseline")
    optimized_cases = load_results("optimized")

    print("Loading judge scores...")
    judge_data = load_judge_scores()
    baseline_judge = judge_data.get("baseline", [])
    optimized_judge = judge_data.get("optimized", [])

    print("Computing metrics...")
    baseline_metrics = extract_metrics(baseline_cases, baseline_judge)
    optimized_metrics = extract_metrics(optimized_cases, optimized_judge)

    print("Generating report...")
    report = build_report(
        baseline_metrics,
        optimized_metrics,
        baseline_cases,
        optimized_cases,
        baseline_judge,
        optimized_judge,
        thresholds,
    )

    REPORT_FILE.write_text(report)
    print(f"\nReport saved to {REPORT_FILE}")

    # Also print a quick summary to stdout
    bm, om = baseline_metrics, optimized_metrics
    print(f"\n{'=' * 50}")
    print("QUICK SUMMARY")
    print(f"  Baseline:  {bm['completion_rate']} completed, {bm['avg_time_s']:.0f}s avg, J-Overall={bm['judge_overall']:.2f}")
    print(f"  Optimized: {om['completion_rate']} completed, {om['avg_time_s']:.0f}s avg, J-Overall={om['judge_overall']:.2f}")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
