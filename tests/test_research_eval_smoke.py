from eval.benchmarks.deep_research_bench_loader import load_benchmark_tasks
from eval.benchmarks.research_eval_smoke import (
    build_research_eval_smoke_cases,
    summarize_research_eval_smoke,
)


def test_research_eval_smoke_builds_race_fact_like_metrics():
    tasks = load_benchmark_tasks("eval/benchmarks/sample_tasks.jsonl", max_cases=3)
    cases = build_research_eval_smoke_cases(tasks)
    summary = summarize_research_eval_smoke(cases).to_dict()

    assert len(cases) == 3
    assert summary["schema_version"] == 1
    assert summary["case_count"] == 3
    assert summary["citation_accuracy_required"] is True
    assert "citation_accuracy_sample" in summary["required_metrics"]
    assert "unsupported_claim_rate" in summary["required_metrics"]
    assert summary["cases"][0]["rubric"]["citation_coverage"] is True
