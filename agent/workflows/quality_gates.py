from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class QualityGateResult:
    name: str
    status: str
    score: Optional[float] = None
    threshold: Optional[float] = None
    action: str = "continue"
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value not in (None, "", [], {})}


@dataclass
class QualityGatePolicy:
    min_query_coverage: float = 0.6
    min_freshness_ratio_30d: float = 0.4
    min_citation_coverage: float = 0.6
    max_unsupported_claims: int = 0
    max_contradicted_claims: int = 0
    min_source_diversity: float = 0.35
    min_primary_source_ratio: float = 0.0
    max_low_value_source_ratio: float = 0.5


def _float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def default_policy(settings: Any = None) -> QualityGatePolicy:
    return QualityGatePolicy(
        min_query_coverage=float(getattr(settings, "deepsearch_quality_gate_min_query_coverage", 0.6) if settings else 0.6),
        min_freshness_ratio_30d=float(getattr(settings, "deepsearch_freshness_warning_min_ratio", 0.4) if settings else 0.4),
        min_citation_coverage=float(getattr(settings, "citation_gate_min_coverage", 0.6) if settings else 0.6),
        max_unsupported_claims=int(getattr(settings, "claim_verifier_gate_max_unsupported", 0) if settings else 0),
        max_contradicted_claims=int(getattr(settings, "claim_verifier_gate_max_contradicted", 0) if settings else 0),
        min_source_diversity=float(getattr(settings, "deepsearch_quality_gate_min_source_diversity", 0.35) if settings else 0.35),
        min_primary_source_ratio=float(getattr(settings, "deepsearch_quality_gate_min_primary_source_ratio", 0.0) if settings else 0.0),
        max_low_value_source_ratio=float(getattr(settings, "deepsearch_quality_gate_max_low_value_source_ratio", 0.5) if settings else 0.5),
    )


def evaluate_quality_gates(
    diagnostics: dict[str, Any],
    *,
    quality_summary: Optional[dict[str, Any]] = None,
    epoch: int = 0,
    policy: Optional[QualityGatePolicy] = None,
) -> list[QualityGateResult]:
    quality_summary = quality_summary or {}
    policy = policy or default_policy()
    results: list[QualityGateResult] = []

    query_score = _float(
        diagnostics.get("query_coverage_score"),
        _float((diagnostics.get("query_coverage") or {}).get("score") if isinstance(diagnostics.get("query_coverage"), dict) else None, 0.0),
    )
    missing_dimensions = []
    if isinstance(diagnostics.get("query_coverage"), dict):
        missing_dimensions = diagnostics["query_coverage"].get("missing_dimensions") or []
    elif isinstance(diagnostics.get("query_dimensions_missing"), list):
        missing_dimensions = diagnostics.get("query_dimensions_missing") or []
    if query_score is not None and query_score < policy.min_query_coverage:
        results.append(
            QualityGateResult(
                name="query_coverage",
                status="fail",
                score=query_score,
                threshold=policy.min_query_coverage,
                action="add_gap_queries",
                reason="query coverage below threshold",
                details={"missing_dimensions": missing_dimensions, "epoch": epoch},
            )
        )
    else:
        results.append(
            QualityGateResult(
                name="query_coverage",
                status="pass",
                score=query_score,
                threshold=policy.min_query_coverage,
                action="continue",
                details={"epoch": epoch},
            )
        )

    freshness_summary = diagnostics.get("freshness_summary") if isinstance(diagnostics.get("freshness_summary"), dict) else {}
    freshness_ratio = _float(freshness_summary.get("fresh_30_ratio"), 0.0)
    time_sensitive = bool(diagnostics.get("time_sensitive_query"))
    if time_sensitive and freshness_ratio is not None and freshness_ratio < policy.min_freshness_ratio_30d:
        results.append(
            QualityGateResult(
                name="freshness",
                status="fail",
                score=freshness_ratio,
                threshold=policy.min_freshness_ratio_30d,
                action="prefer_fresh_provider_profile",
                reason="freshness ratio below threshold for time-sensitive query",
                details={"freshness_summary": freshness_summary, "epoch": epoch},
            )
        )
    else:
        results.append(
            QualityGateResult(
                name="freshness",
                status="pass",
                score=freshness_ratio,
                threshold=policy.min_freshness_ratio_30d,
                action="continue",
                details={"epoch": epoch},
            )
        )

    citation_coverage = _float(
        quality_summary.get("citation_coverage"),
        _float(quality_summary.get("citation_coverage_score"), None),
    )
    if citation_coverage is not None:
        if citation_coverage < policy.min_citation_coverage:
            results.append(
                QualityGateResult(
                    name="citation_coverage",
                    status="fail",
                    score=citation_coverage,
                    threshold=policy.min_citation_coverage,
                    action="improve_citations",
                    reason="citation coverage below threshold",
                    details={"epoch": epoch},
                )
            )
        else:
            results.append(
                QualityGateResult(
                    name="citation_coverage",
                    status="pass",
                    score=citation_coverage,
                    threshold=policy.min_citation_coverage,
                    action="continue",
                    details={"epoch": epoch},
                )
            )

    unsupported = _int(quality_summary.get("claim_verifier_unsupported"), 0)
    contradicted = _int(quality_summary.get("claim_verifier_contradicted"), 0)
    if unsupported > policy.max_unsupported_claims or contradicted > policy.max_contradicted_claims:
        results.append(
            QualityGateResult(
                name="claim_verifier",
                status="fail",
                action="resolve_claims",
                reason="claim verifier thresholds exceeded",
                details={
                    "unsupported": unsupported,
                    "contradicted": contradicted,
                    "max_unsupported": policy.max_unsupported_claims,
                    "max_contradicted": policy.max_contradicted_claims,
                    "epoch": epoch,
                },
            )
        )
    elif "claim_verifier_total" in quality_summary:
        results.append(
            QualityGateResult(
                name="claim_verifier",
                status="pass",
                action="continue",
                details={"unsupported": unsupported, "contradicted": contradicted, "epoch": epoch},
            )
        )

    source_diversity = _float(quality_summary.get("source_diversity_score"), None)
    if source_diversity is not None:
        if source_diversity < policy.min_source_diversity:
            results.append(
                QualityGateResult(
                    name="source_diversity",
                    status="fail",
                    score=source_diversity,
                    threshold=policy.min_source_diversity,
                    action="add_diverse_sources",
                    reason="source diversity below threshold",
                    details={"epoch": epoch},
                )
            )
        else:
            results.append(
                QualityGateResult(
                    name="source_diversity",
                    status="pass",
                    score=source_diversity,
                    threshold=policy.min_source_diversity,
                    action="continue",
                    details={"epoch": epoch},
                )
            )

    primary_ratio = _float(quality_summary.get("primary_source_ratio"), None)
    if primary_ratio is not None and policy.min_primary_source_ratio > 0:
        if primary_ratio < policy.min_primary_source_ratio:
            results.append(
                QualityGateResult(
                    name="primary_source_ratio",
                    status="fail",
                    score=primary_ratio,
                    threshold=policy.min_primary_source_ratio,
                    action="prefer_primary_sources",
                    reason="primary-source ratio below threshold",
                    details={"epoch": epoch},
                )
            )
        else:
            results.append(
                QualityGateResult(
                    name="primary_source_ratio",
                    status="pass",
                    score=primary_ratio,
                    threshold=policy.min_primary_source_ratio,
                    action="continue",
                    details={"epoch": epoch},
                )
            )

    low_value_ratio = _float(quality_summary.get("low_value_source_ratio"), None)
    if low_value_ratio is not None:
        if low_value_ratio > policy.max_low_value_source_ratio:
            results.append(
                QualityGateResult(
                    name="low_value_sources",
                    status="fail",
                    score=low_value_ratio,
                    threshold=policy.max_low_value_source_ratio,
                    action="replace_low_value_sources",
                    reason="low-value source ratio above threshold",
                    details={"epoch": epoch},
                )
            )
        else:
            results.append(
                QualityGateResult(
                    name="low_value_sources",
                    status="pass",
                    score=low_value_ratio,
                    threshold=policy.max_low_value_source_ratio,
                    action="continue",
                    details={"epoch": epoch},
                )
            )

    return results


def serialize_gate_results(results: list[QualityGateResult]) -> list[dict[str, Any]]:
    return [result.to_dict() for result in results]


def missing_topics_from_gates(results: list[QualityGateResult]) -> list[str]:
    topics: list[str] = []
    seen = set()
    for result in results:
        if result.status != "fail" or result.action != "add_gap_queries":
            continue
        missing = result.details.get("missing_dimensions") if isinstance(result.details, dict) else []
        if not isinstance(missing, list):
            continue
        for item in missing:
            text = str(item or "").strip()
            if text and text not in seen:
                seen.add(text)
                topics.append(text)
    return topics
