import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = ROOT / "eval" / "ablation" / "results"
REPORT_FILE = ROOT / "eval" / "ablation" / "v9_toggle_ablation_report.md"
JUDGE_FILE = RESULTS_DIR / "v9_ablation_judge_scores.json"
VARIANTS = (
    "optimized_full",
    "no_obs_masking",
    "no_offloading",
    "no_reflexion",
    "no_backtrack",
    "no_tool_pruning",
    "baseline_equivalent",
    "context_only",
    "search_planning_only",
)
CASE_ORDER = (
    "v7_010",
    "v7_008",
    "v7_001",
    "v7_006",
    "v7_004",
)
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
DIMS = ("coverage", "depth", "structure", "citations", "overall")


def _avg(vals: List[float]) -> float:
    return round(sum(vals) / len(vals), 3) if vals else 0.0


def _load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


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


def load_variant_results(variant: str) -> List[Dict[str, Any]]:
    return _load_json(RESULTS_DIR / f"v9_ablation_{variant}.json", [])


def load_judge() -> Dict[str, List[Dict[str, Any]]]:
    data = _load_json(JUDGE_FILE, {})
    return data.get("details", {}) if isinstance(data, dict) else {}


def extract_metrics(cases: List[Dict[str, Any]], judge_cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    completed = [case for case in cases if case.get("status") == "completed"]
    times = [case.get("wall_time_s", 0) for case in completed]
    chars = [case.get("final_report_chars", 0) for case in completed]
    qc_scores: List[float] = []
    sources: List[int] = []
    uc_counts: List[int] = []
    cit_cov: List[float] = []
    for case in completed:
        quality = case.get("last_quality_update") or {}
        if quality.get("query_coverage_score") is not None:
            qc_scores.append(float(quality["query_coverage_score"]))
        evidence = case.get("evidence_summary") or {}
        if evidence.get("sources_count") is not None:
            sources.append(int(evidence["sources_count"]))
        if evidence.get("unsupported_claims_count") is not None:
            uc_counts.append(int(evidence["unsupported_claims_count"]))
        if evidence.get("citation_coverage") is not None:
            cit_cov.append(float(evidence["citation_coverage"]))
    scored = [case for case in judge_cases if case.get("judge_scores") and "error" not in case.get("judge_scores", {})]
    judge_avgs: Dict[str, float] = {}
    for dim in DIMS:
        vals = [case["judge_scores"].get(dim, 0) for case in scored]
        judge_avgs[dim] = round(sum(vals) / len(vals), 2) if vals else 0.0
    return {
        "completed": len(completed),
        "total": len(cases),
        "avg_time_s": _avg(times),
        "avg_report_chars": round(_avg(chars)),
        "avg_qc": _avg(qc_scores),
        "avg_sources": round(_avg(sources), 1),
        "avg_uc": _avg(uc_counts),
        "avg_citation_coverage": _avg(cit_cov),
        **{f"judge_{dim}": judge_avgs.get(dim, 0.0) for dim in DIMS},
    }


def _judge_lookup(judge_cases: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {case.get("case_id", ""): case for case in judge_cases or []}


def _result_lookup(results: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {case.get("case_id", ""): case for case in results or []}


def _rank_variants(metrics: Dict[str, Dict[str, Any]], key: str) -> List[Tuple[str, float]]:
    rows = [(variant, float(info.get(key, 0))) for variant, info in metrics.items() if info.get("completed")]
    return sorted(rows, key=lambda item: item[1], reverse=True)


def _build_findings(metrics: Dict[str, Dict[str, Any]]) -> List[str]:
    findings: List[str] = []
    ranked_overall = _rank_variants(metrics, "judge_overall")
    ranked_citations = _rank_variants(metrics, "judge_citations")
    if ranked_overall:
        findings.append(f"- **最佳 Overall**：`{ranked_overall[0][0]}` = {ranked_overall[0][1]:.2f}")
    if ranked_citations:
        findings.append(f"- **最佳 Citations**：`{ranked_citations[0][0]}` = {ranked_citations[0][1]:.2f}")
    optimized = metrics.get("optimized_full")
    baseline = metrics.get("baseline_equivalent")
    if optimized and baseline:
        findings.append(
            f"- **optimized_full vs baseline_equivalent**：Overall {optimized['judge_overall']:.2f} vs {baseline['judge_overall']:.2f}；Citations {optimized['judge_citations']:.2f} vs {baseline['judge_citations']:.2f}"
        )
    for candidate in ("no_offloading", "no_obs_masking", "no_reflexion", "no_backtrack", "no_tool_pruning"):
        if optimized and metrics.get(candidate):
            current = metrics[candidate]
            delta_overall = current["judge_overall"] - optimized["judge_overall"]
            delta_citations = current["judge_citations"] - optimized["judge_citations"]
            findings.append(
                f"- `{candidate}` 相对 `optimized_full`：ΔOverall={delta_overall:+.2f}，ΔCitations={delta_citations:+.2f}"
            )
    return findings


def build_report(metrics: Dict[str, Dict[str, Any]], results: Dict[str, List[Dict[str, Any]]], judge: Dict[str, List[Dict[str, Any]]]) -> str:
    snapshot = env_snapshot()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    branch = git_branch()
    lines = [
        "# v9 优化开关定位实验报告",
        "",
        f"**日期**: {now}  ",
        f"**分支**: {branch}  ",
        f"**模型**: {snapshot.get('PRIMARY_MODEL') or 'unknown'}  ",
        f"**样本**: {', '.join(CASE_ORDER)}（5 个代表 case）  ",
        f"**Judge**: 当前 PRIMARY_MODEL, 完整报告, 每 case 3 次评分取平均",
        "",
        "## 一、当前 .env 快照",
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
        "## 二、总体排名",
        "",
        "| Variant | 完成率 | 平均耗时 | 报告长度 | QC | Src | CitCov | J-Citations | J-Overall |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for variant in VARIANTS:
        info = metrics.get(variant)
        if not info or not info.get("total"):
            continue
        lines.append(
            f"| {variant} | {info['completed']}/{info['total']} | {info['avg_time_s']:.0f}s | {info['avg_report_chars']:,} | {info['avg_qc']:.3f} | {info['avg_sources']:.1f} | {info['avg_citation_coverage']:.3f} | {info['judge_citations']:.2f} | {info['judge_overall']:.2f} |"
        )
    lines += [
        "",
        "## 三、关键观察",
        "",
    ]
    lines.extend(_build_findings(metrics))
    lines += [
        "",
        "## 四、逐 Case × Variant Judge 对比",
        "",
        "| Case | Domain | Variant | Time | Src | CitCov | J-Citations | J-Overall |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for variant in VARIANTS:
        if variant not in results:
            continue
        result_lookup = _result_lookup(results[variant])
        judge_lookup = _judge_lookup(judge.get(variant, []))
        for case_id in CASE_ORDER:
            case = result_lookup.get(case_id)
            judge_case = judge_lookup.get(case_id, {})
            if not case:
                continue
            evidence = case.get("evidence_summary") or {}
            scores = judge_case.get("judge_scores") or {}
            lines.append(
                f"| {case_id} | {case.get('domain', '')} | {variant} | {case.get('wall_time_s', 0):.0f}s | {evidence.get('sources_count', 0)} | {float(evidence.get('citation_coverage') or 0):.3f} | {float(scores.get('citations') or 0):.2f} | {float(scores.get('overall') or 0):.2f} |"
            )
    lines += [
        "",
        "## 五、最可能的责任开关判断",
        "",
        "- 如果 `no_offloading` 或 `no_obs_masking` 明显提升 `J-Citations`，优先怀疑上下文压缩导致引用映射丢失。",
        "- 如果 `no_reflexion` / `no_backtrack` / `no_tool_pruning` 改善更明显，则更可能是搜索分支扩张后 source selection 质量下降。",
        "- 如果 `context_only` 优于 `search_planning_only`，说明上下文优化保留、搜索规划开关收缩更适合当前 v9 环境。",
        "",
        "## 六、输出文件",
        "",
        f"- 结果 JSON: `{RESULTS_DIR}/v9_ablation_*.json`",
        f"- Judge JSON: `{JUDGE_FILE}`",
        f"- 本报告: `{REPORT_FILE}`",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    judge = load_judge()
    results: Dict[str, List[Dict[str, Any]]] = {}
    metrics: Dict[str, Dict[str, Any]] = {}
    for variant in VARIANTS:
        cases = load_variant_results(variant)
        if not cases:
            continue
        results[variant] = cases
        metrics[variant] = extract_metrics(cases, judge.get(variant, []))
    report = build_report(metrics, results, judge)
    REPORT_FILE.write_text(report, encoding="utf-8")
    print(f"Report saved to {REPORT_FILE}")


if __name__ == "__main__":
    main()
