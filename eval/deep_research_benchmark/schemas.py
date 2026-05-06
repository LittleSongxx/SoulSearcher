from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


DEFAULT_SUPERVISOR_DEEPSEARCH_CONFIG: dict[str, Any] = {
    "deepsearch_strategy": "supervisor_workers",
    "deepsearch_mode": "supervisor_workers",
    "deepsearch_supervisor_rounds": 3,
    "deepsearch_supervisor_max_workers": 4,
    "deepsearch_supervisor_queries_per_worker": 3,
    "deepsearch_supervisor_parallel_workers": 2,
    "deepsearch_results_per_query": 8,
    "deepsearch_max_seconds": 840,
    "deepsearch_report_sources_limit": 30,
    "deepsearch_event_results_limit": 5,
    "deepsearch_visualize_browser": False,
    "deepsearch_enable_research_fetcher": True,
    "deepsearch_supervisor_fetch_passages": True,
    "deepsearch_supervisor_fetch_source_limit": 12,
    "deepsearch_passage_cap": 40,
    "deepsearch_evidence_item_cap": 160,
    "deepsearch_min_evidence_snippet_chars": 40,
    "deepsearch_enable_claim_ledger": True,
    "deepsearch_claim_ledger_max_claims": 30,
    "deepsearch_claim_verifier_max_claims": 10,
    "deepsearch_claim_grounding_gate_enabled": True,
    "deepsearch_final_verifier_revise": True,
    "deepsearch_final_verifier_max_revisions": 1,
    "deepsearch_citation_repair_enabled": True,
    "deepsearch_citation_repair_min_coverage": 0.85,
    "deepsearch_source_curator_enabled": True,
    "deepsearch_reflection_gap_queries": True,
    "deepsearch_fact_card_cap": 80,
    "deepsearch_fact_card_writer_cap": 40,
    "deepsearch_fact_card_min_quote_chars": 40,
    "deepsearch_prewrite_gap_followup_enabled": True,
    "deepsearch_prewrite_gap_followup_queries": 2,
    "deepsearch_stage_warn_after_s": 120.0,
    "deepsearch_sectioned_report_adaptive": True,
    "deepsearch_sectioned_report_adaptive_max_sections": 5,
    "deepsearch_section_results_cap": 6,
    "deepsearch_section_evidence_cap": 8,
    "source_policy": "web",
}


@dataclass
class BenchmarkTask:
    id: str
    query: str
    domain: str = "general"
    task_type: str = "open_research"
    difficulty: str = "medium"
    expected_dimensions: list[str] = field(default_factory=list)
    freshness_requirement: str = ""
    must_cite: bool = True
    judge_rubric: dict[str, Any] = field(default_factory=dict)
    source_constraints: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunConfig:
    dataset_path: Path
    output_dir: Path
    base_url: str = "asgi"
    strategy: str = "supervisor_workers"
    model: str = ""
    max_cases: Optional[int] = 10
    timeout_s: float = 900.0
    concurrency: int = 1
    user_id: str = "benchmark_user"
    deepsearch_config: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_SUPERVISOR_DEEPSEARCH_CONFIG))

    def __post_init__(self) -> None:
        merged = dict(DEFAULT_SUPERVISOR_DEEPSEARCH_CONFIG)
        merged.update(self.deepsearch_config or {})
        self.deepsearch_config = merged

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["dataset_path"] = str(self.dataset_path)
        payload["output_dir"] = str(self.output_dir)
        return payload


@dataclass
class SSEEvent:
    event: str
    data: Any = None
    event_id: Optional[str] = None
    received_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CaseRunResult:
    case_id: str
    query: str
    status: str
    thread_id: Optional[str] = None
    started_at: str = field(default_factory=utc_now_iso)
    ended_at: Optional[str] = None
    duration_ms: float = 0.0
    final_report: str = ""
    final_report_chars: int = 0
    error: str = ""
    event_count: int = 0
    events_path: str = ""
    report_path: str = ""
    evidence_path: str = ""
    run_metrics_path: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    run_metrics: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class JudgeScore:
    case_id: str
    judge_type: str
    status: str
    score: Optional[float] = None
    passed: Optional[bool] = None
    details: dict[str, Any] = field(default_factory=dict)
    raw_response: str = ""
    error: str = ""
    judged_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MetricSummary:
    total_cases: int = 0
    completed_cases: int = 0
    failed_cases: int = 0
    timeout_cases: int = 0
    stable_completion_rate: float = 0.0
    rubric_pass_rate: Optional[float] = None
    citation_accuracy: Optional[float] = None
    unsupported_claim_rate: Optional[float] = None
    avg_effective_citations: Optional[float] = None
    judge_scores_total: int = 0
    report_judge_scored: int = 0
    citation_judge_scored: int = 0
    claim_judge_scored: int = 0
    avg_report_evidence_quality: Optional[float] = None
    avg_report_depth: Optional[float] = None
    avg_sources: Optional[float] = None
    avg_evidence_items: Optional[float] = None
    avg_passages: Optional[float] = None
    avg_claims_checked: Optional[float] = None
    avg_claims_supported: Optional[float] = None
    avg_claims_unsupported: Optional[float] = None
    hallucination_rate: Optional[float] = None
    hallucination_count: Optional[float] = None
    hallucination_judge_scored: int = 0
    by_language: dict[str, Any] = field(default_factory=dict)
    by_domain: dict[str, Any] = field(default_factory=dict)
    p50_duration_s: Optional[float] = None
    p90_duration_s: Optional[float] = None
    mean_duration_s: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
