"""
v9 Summary: Aggregate production benchmark results + judge scores.

Generates eval/ablation/v9_production_report.md with:
  - Overall comparison table
  - Per-domain breakdown
  - Post-hoc completion rate at multiple time thresholds
  - Per-case detail table
  - v7/v8/v9 historical comparison when available

Usage:
    conda run -n langchain python eval/ablation/run_v9_summary.py
"""

import argparse
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = ROOT / "eval" / "ablation" / "results"
REPORT_FILE = ROOT / "eval" / "ablation" / "v9_production_report.md"
BENCH_FILE = ROOT / "eval" / "benchmarks" / "v7_tasks.jsonl"
DIMS = ("coverage", "depth", "structure", "citations", "overall")
DEFAULT_THRESHOLDS = [600, 900, 1200, 1800]
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


def _avg(vals: List[float]) -> float:
    return round(sum(vals) / len(vals), 3) if vals else 0.0


def _pct_change(new: float, old: float) -> str:
    if old == 0:
        return "N/A"
    pct = (new - old) / abs(old) * 100
    arrow = "⬆️" if pct > 0 else "⬇️" if pct < 0 else "—"
    return f"{pct:+.1f}% {arrow}"


def _load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_results(version: str, variant: str) -> List[Dict[str, Any]]:
    return _load_json(RESULTS_DIR / f"{version}_{variant}.json", [])


def load_judge_scores(version: str) -> Dict[str, List[Dict[str, Any]]]:
    data = _load_json(RESULTS_DIR / f"{version}_judge_scores.json", {})
    return data.get("details", {}) if isinstance(data, dict) else {}


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
        snapshot[key] = os.environ.get(key, dotenv.get(key, ""))
    return snapshot


def git_branch() -> str:
    try:
        proc = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            timeout=5,
        )
        return proc.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def extract_metrics(cases: List[Dict[str, Any]], judge_cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    completed = [r for r in cases if r.get("status") == "completed"]
    times = [r["wall_time_s"] for r in completed]
    chars = [r.get("final_report_chars", 0) for r in completed]
    qc_scores, sources, uc_counts = [], [], []
    freshness, cit_cov = [], []
    cv_total, cv_verified, cv_unsupported, cv_contradicted = [], [], [], []

    for r in completed:
        qu = r.get("last_quality_update") or {}
        if qu.get("query_coverage_score") is not None:
            qc_scores.append(float(qu["query_coverage_score"]))
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

    scored_judge = [
        c for c in (judge_cases or [])
        if c.get("judge_scores") and "error" not in c.get("judge_scores", {})
    ]
    judge_avgs: Dict[str, float] = {}
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
        "avg_cv_total": _avg(cv_total),
        "avg_cv_verified": _avg(cv_verified),
        "avg_cv_unsupported": _avg(cv_unsupported),
        "avg_cv_contradicted": _avg(cv_contradicted),
        "judge_n": len(scored_judge),
        **{f"judge_{dim}": judge_avgs.get(dim, 0) for dim in DIMS},
        "_times": times,
        "_all_cases": cases,
    }


def completion_at_threshold(cases: List[Dict[str, Any]], threshold_s: float) -> str:
    completed = [r for r in cases if r.get("status") == "completed"]
    within = [r for r in completed if r.get("wall_time_s", float("inf")) <= threshold_s]
    return f"{len(within)}/{len(cases)}"


def _judge_lookup(variant_name: str, judge_list: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {f"{variant_name}_{jc.get('case_id', '')}": jc for jc in judge_list or []}


def _domain_judge_avg(judge_list, dim):
    vals = [c["judge_scores"].get(dim, 0) for c in judge_list]
    return round(sum(vals) / len(vals), 2) if vals else 0


def historical_rows() -> List[str]:
    rows: List[str] = []
    for version in ("v7", "v8", "v9"):
        judge = load_judge_scores(version)
        for variant in ("baseline", "optimized"):
            cases = load_results(version, variant)
            if not cases:
                continue
            metrics = extract_metrics(cases, judge.get(variant, []))
            rows.append(
                f"| {version} | {variant} | {metrics['completion_rate']} | "
                f"{metrics['avg_time_s']:.0f}s | {metrics['avg_sources']:.1f} | "
                f"{metrics['avg_citation_coverage']:.3f} | {metrics['judge_overall']:.2f} | "
                f"{metrics['judge_citations']:.2f} |"
            )
    return rows


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
    branch = git_branch()
    snapshot = env_snapshot()

    lines = [
        "# v9 生产级 Benchmark 报告：Baseline vs Optimized（当前 .env 配置）",
        "",
        f"**日期**: {now}  ",
        f"**分支**: {branch}  ",
        f"**模型**: {snapshot.get('PRIMARY_MODEL') or 'unknown'}（两组相同）  ",
        "**参数**: 当前 .env 配置 + baseline/optimized 特性开关  ",
        "**超时**: 每 case 900s 硬上限；报告中保留事后阈值完成率  ",
        "**测试集**: v7_tasks.jsonl — 与 v7/v8 相同的 10 case（5 领域 × 2）  ",
        "**Judge**: 当前 PRIMARY_MODEL, 完整报告, 每 case 3 次评分取平均",
        "",
        "### v9 当前 .env 参数快照",
        "",
        "| 参数 | 值 |",
        "|---|---|",
    ]
    for key, value in snapshot.items():
        lines.append(f"| {key} | {value or '(unset)'} |")

    lines += [
        "",
        "---",
        "",
        "## 一、总览对比表",
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
        f"| **Claim Verifier total** | {bm['avg_cv_total']:.1f} | {om['avg_cv_total']:.1f} | {_pct_change(om['avg_cv_total'], bm['avg_cv_total'])} |",
        f"| **Claim Verifier verified** | {bm['avg_cv_verified']:.1f} | {om['avg_cv_verified']:.1f} | {_pct_change(om['avg_cv_verified'], bm['avg_cv_verified'])} |",
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

    lines += [
        "",
        "### 1.3 事后完成率（不同耗时阈值）",
        "",
        "| 阈值 | Baseline | Optimized |",
        "|---|---|---|",
    ]
    for t in thresholds:
        lines.append(
            f"| {t}s ({t // 60}min) | "
            f"{completion_at_threshold(baseline_cases, t)} | "
            f"{completion_at_threshold(optimized_cases, t)} |"
        )

    lines += [
        "",
        "---",
        "",
        "## 二、分领域对比",
        "",
    ]
    domains = sorted(set(r.get("domain", "unknown") for r in baseline_cases + optimized_cases))
    for domain in domains:
        b_domain = [r for r in baseline_cases if r.get("domain") == domain and r.get("status") == "completed"]
        o_domain = [r for r in optimized_cases if r.get("domain") == domain and r.get("status") == "completed"]
        bj_domain = [
            c for c in (baseline_judge or [])
            if c.get("domain") == domain and c.get("judge_scores") and "error" not in c.get("judge_scores", {})
        ]
        oj_domain = [
            c for c in (optimized_judge or [])
            if c.get("domain") == domain and c.get("judge_scores") and "error" not in c.get("judge_scores", {})
        ]
        lines += [
            f"### {domain.capitalize()}",
            "",
            f"| 指标 | Baseline (N={len(b_domain)}) | Optimized (N={len(o_domain)}) |",
            "|---|---|---|",
            f"| 平均耗时 | {_avg([r['wall_time_s'] for r in b_domain]):.0f}s | {_avg([r['wall_time_s'] for r in o_domain]):.0f}s |",
            f"| 报告长度 | {_avg([r.get('final_report_chars', 0) for r in b_domain]):.0f} | {_avg([r.get('final_report_chars', 0) for r in o_domain]):.0f} |",
            f"| J-Overall | {_domain_judge_avg(bj_domain, 'overall'):.2f} | {_domain_judge_avg(oj_domain, 'overall'):.2f} |",
            f"| J-Citations | {_domain_judge_avg(bj_domain, 'citations'):.2f} | {_domain_judge_avg(oj_domain, 'citations'):.2f} |",
            "",
        ]

    lines += [
        "---",
        "",
        "## 三、逐 Case 详情",
        "",
        "| Case | Domain | Variant | 耗时 | 长度 | QC | Src | UC | CitCov | J-Overall |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    judge_lookup: Dict[str, Dict[str, Any]] = {}
    judge_lookup.update(_judge_lookup("baseline", baseline_judge))
    judge_lookup.update(_judge_lookup("optimized", optimized_judge))
    for variant_name, cases in [("baseline", baseline_cases), ("optimized", optimized_cases)]:
        for r in cases:
            cid = r.get("case_id", "?")
            domain = r.get("domain", "?")
            status = r.get("status", "?")
            if status != "completed":
                lines.append(f"| {cid} | {domain} | {variant_name} | ❌ {status} | — | — | — | — | — | — |")
                continue
            wall = r.get("wall_time_s", 0)
            chars = r.get("final_report_chars", 0)
            qu = r.get("last_quality_update") or {}
            es = r.get("evidence_summary") or {}
            qc = qu.get("query_coverage_score") or es.get("query_coverage_score") or 0
            src = es.get("sources_count", 0)
            uc = es.get("unsupported_claims_count", 0)
            cit_cov = es.get("citation_coverage", 0)
            jc = judge_lookup.get(f"{variant_name}_{cid}", {})
            js = jc.get("judge_scores", {}) if jc else {}
            jo = js.get("overall", "—") if js and "error" not in js else "—"
            lines.append(
                f"| {cid} | {domain} | {variant_name} | {wall:.0f}s | {chars:,} | "
                f"{qc:.2f} | {src} | {uc} | {cit_cov:.3f} | {jo} |"
            )

    hist = historical_rows()
    if hist:
        lines += [
            "",
            "---",
            "",
            "## 四、v7/v8/v9 横向回顾",
            "",
            "| Version | Variant | 完成率 | 平均耗时 | Src | CitCov | J-Overall | J-Citations |",
            "|---|---|---|---|---|---|---|---|",
            *hist,
        ]

    lines += [
        "",
        "---",
        "",
        "## 附录：测试环境",
        "",
        "| 项目 | 值 |",
        "|---|---|",
        f"| 分支 | {branch} |",
        f"| 模型 | {snapshot.get('PRIMARY_MODEL') or 'unknown'} |",
        "| 参数 | 当前 .env 配置 + baseline/optimized 特性开关 |",
        "| 超时 | 每 case 900s 硬上限；事后阈值判定 |",
        "| 测试集 | eval/benchmarks/v7_tasks.jsonl |",
        "| Judge | 当前 PRIMARY_MODEL, 完整报告, 3 次评分取平均 |",
        "| 数据文件 | v9_baseline.json, v9_optimized.json, v9_judge_scores.json |",
    ]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="v9 Summary Report Generator")
    parser.add_argument(
        "--thresholds",
        default=",".join(str(t) for t in DEFAULT_THRESHOLDS),
        help="Comma-separated time thresholds in seconds for post-hoc completion rate",
    )
    args = parser.parse_args()
    thresholds = [int(t.strip()) for t in args.thresholds.split(",")]

    print("Loading results...")
    baseline_cases = load_results("v9", "baseline")
    optimized_cases = load_results("v9", "optimized")

    print("Loading judge scores...")
    judge_data = load_judge_scores("v9")
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
    REPORT_FILE.write_text(report, encoding="utf-8")
    print(f"\nReport saved to {REPORT_FILE}")
    print(f"\n{'=' * 50}")
    print("QUICK SUMMARY")
    print(f"  Baseline:  {baseline_metrics['completion_rate']} completed, {baseline_metrics['avg_time_s']:.0f}s avg, J-Overall={baseline_metrics['judge_overall']:.2f}")
    print(f"  Optimized: {optimized_metrics['completion_rate']} completed, {optimized_metrics['avg_time_s']:.0f}s avg, J-Overall={optimized_metrics['judge_overall']:.2f}")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
