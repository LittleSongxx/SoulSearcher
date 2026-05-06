from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from eval.benchmarks.deep_research_bench_loader import BenchmarkTask


@dataclass
class ResearchEvalSmokeCase:
    task_id: str
    query: str
    expected_fields: list[str] = field(default_factory=list)
    source_policy: str = "web"
    min_citations: int = 2
    freshness_required: bool = False
    rubric: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResearchEvalSmokeSummary:
    schema_version: int
    case_count: int
    citation_accuracy_required: bool
    effective_citation_minimum: int
    required_metrics: list[str]
    cases: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_research_eval_smoke_cases(tasks: list[BenchmarkTask]) -> list[ResearchEvalSmokeCase]:
    cases: list[ResearchEvalSmokeCase] = []
    for task in tasks:
        constraints = task.constraints if isinstance(task.constraints, dict) else {}
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        source_policy = str(constraints.get("source_policy") or metadata.get("source_policy") or "web")
        min_citations = constraints.get("min_citations")
        if not isinstance(min_citations, int):
            min_citations = max(2, min(8, len(task.expected_fields or []) + 1))
        freshness_days = constraints.get("freshness_days")
        cases.append(
            ResearchEvalSmokeCase(
                task_id=task.task_id,
                query=task.query,
                expected_fields=list(task.expected_fields or []),
                source_policy=source_policy,
                min_citations=min_citations,
                freshness_required=isinstance(freshness_days, (int, float)) and freshness_days > 0,
                rubric={
                    "required_dimensions": list(task.expected_fields or []),
                    "citation_coverage": True,
                    "unsupported_claim_detection": True,
                    "source_diversity": True,
                    "latency_budget": constraints.get("latency_budget_seconds", 900),
                },
            )
        )
    return cases


def summarize_research_eval_smoke(cases: list[ResearchEvalSmokeCase]) -> ResearchEvalSmokeSummary:
    minimum = min((case.min_citations for case in cases), default=0)
    return ResearchEvalSmokeSummary(
        schema_version=1,
        case_count=len(cases),
        citation_accuracy_required=True,
        effective_citation_minimum=minimum,
        required_metrics=[
            "citation_coverage",
            "citation_accuracy_sample",
            "effective_citation_count",
            "source_diversity",
            "freshness",
            "unsupported_claim_rate",
            "query_coverage",
            "latency_seconds",
            "estimated_cost",
        ],
        cases=[case.to_dict() for case in cases],
    )
