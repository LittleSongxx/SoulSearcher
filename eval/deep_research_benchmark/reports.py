from __future__ import annotations

from pathlib import Path

from eval.deep_research_benchmark.schemas import MetricSummary


def render_summary_markdown(summary: MetricSummary) -> str:
    data = summary.to_dict()
    lines = [
        "# Deep Research Benchmark Summary",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
    ]
    labels = {
        "total_cases": "Total cases",
        "completed_cases": "Completed cases",
        "failed_cases": "Failed cases",
        "timeout_cases": "Timeout cases",
        "stable_completion_rate": "Stable completion rate",
        "rubric_pass_rate": "Rubric pass rate",
        "citation_accuracy": "Citation accuracy",
        "unsupported_claim_rate": "Unsupported claim rate",
        "avg_effective_citations": "Average effective citations",
        "judge_scores_total": "Judge scores total",
        "report_judge_scored": "Report judge scored",
        "citation_judge_scored": "Citation judge scored",
        "claim_judge_scored": "Claim judge scored",
        "avg_report_evidence_quality": "Average report evidence quality",
        "avg_report_depth": "Average report depth",
        "avg_sources": "Average sources",
        "avg_evidence_items": "Average evidence items",
        "avg_passages": "Average passages",
        "avg_claims_checked": "Average claims checked",
        "avg_claims_supported": "Average claims supported",
        "avg_claims_unsupported": "Average claims unsupported",
        "p50_duration_s": "P50 duration seconds",
        "p90_duration_s": "P90 duration seconds",
        "mean_duration_s": "Mean duration seconds",
    }
    for key, label in labels.items():
        value = data.get(key)
        if value is None:
            rendered = "n/a"
        elif isinstance(value, float):
            rendered = f"{value:.4f}"
        else:
            rendered = str(value)
        lines.append(f"| {label} | {rendered} |")
    for section_key, title in (("by_language", "By language"), ("by_domain", "By domain")):
        breakdown = data.get(section_key)
        if not isinstance(breakdown, dict) or not breakdown:
            continue
        lines.extend(
            [
                "",
                f"## {title}",
                "",
                "| Group | Cases | Completed | Rubric pass rate | Unsupported claim rate |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for group, payload in breakdown.items():
            if not isinstance(payload, dict):
                continue
            rubric = payload.get("rubric_pass_rate")
            unsupported = payload.get("unsupported_claim_rate")
            lines.append(
                "| "
                + str(group)
                + " | "
                + str(payload.get("total_cases", 0))
                + " | "
                + str(payload.get("completed_cases", 0))
                + " | "
                + ("n/a" if rubric is None else f"{float(rubric):.4f}")
                + " | "
                + ("n/a" if unsupported is None else f"{float(unsupported):.4f}")
                + " |"
            )
    lines.append("")
    return "\n".join(lines)


def write_summary_markdown(run_dir: str | Path, summary: MetricSummary) -> Path:
    output = Path(run_dir) / "summary.md"
    output.write_text(render_summary_markdown(summary), encoding="utf-8")
    return output
