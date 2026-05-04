from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


_TIME_MARKERS = (
    "latest",
    "recent",
    "today",
    "current",
    "update",
    "news",
    "202",
    "最新",
    "近期",
    "今天",
    "当前",
    "动态",
    "新闻",
)

_COMPARISON_MARKERS = (
    "compare",
    "comparison",
    "versus",
    " vs ",
    "tradeoff",
    "benchmark",
    "对比",
    "比较",
    "差异",
    "优劣",
)

_RESEARCH_MARKERS = (
    "research",
    "analysis",
    "analyze",
    "report",
    "survey",
    "evaluate",
    "assess",
    "deep",
    "研究",
    "分析",
    "报告",
    "调研",
    "综述",
    "评估",
)


@dataclass
class ResearchBrief:
    original_query: str
    clarified_goal: str
    scope: str = ""
    constraints: Dict[str, Any] = field(default_factory=dict)
    expected_fields: List[str] = field(default_factory=list)
    preferred_sources: List[str] = field(default_factory=list)
    excluded_sources: List[str] = field(default_factory=list)
    freshness_requirement: str = ""
    output_format: str = "research_report"
    success_criteria: List[str] = field(default_factory=list)
    source_policy: str = "web"
    complexity: str = "standard"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def prompt_context(self) -> str:
        parts = [f"Research goal: {self.clarified_goal or self.original_query}"]
        if self.scope:
            parts.append(f"Scope: {self.scope}")
        if self.expected_fields:
            parts.append("Expected fields: " + ", ".join(self.expected_fields))
        if self.success_criteria:
            parts.append("Success criteria: " + "; ".join(self.success_criteria))
        if self.freshness_requirement:
            parts.append(f"Freshness requirement: {self.freshness_requirement}")
        if self.source_policy:
            parts.append(f"Source policy: {self.source_policy}")
        return "\n".join(parts)


def _clean_list(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    cleaned: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned


def _has_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def _derive_expected_fields(query: str) -> List[str]:
    fields: List[str] = []
    if _has_any(query, _COMPARISON_MARKERS):
        fields.extend(["comparison_dimensions", "pros_cons", "recommendation"])
    if _has_any(query, _TIME_MARKERS):
        fields.append("recent_updates")
    if _has_any(query, _RESEARCH_MARKERS):
        fields.extend(["background", "key_findings", "evidence", "risks"])
    if not fields:
        fields.extend(["answer", "evidence"])
    deduped: List[str] = []
    seen = set()
    for field_name in fields:
        if field_name not in seen:
            seen.add(field_name)
            deduped.append(field_name)
    return deduped


def _derive_complexity(query: str, expected_fields: List[str]) -> str:
    if len(expected_fields) >= 5 or _has_any(query, _COMPARISON_MARKERS):
        return "broad"
    if _has_any(query, _RESEARCH_MARKERS):
        return "standard"
    return "light"


def _derive_freshness(query: str, constraints: Dict[str, Any]) -> str:
    freshness_days = constraints.get("freshness_days") if isinstance(constraints, dict) else None
    if isinstance(freshness_days, (int, float)) and int(freshness_days) > 0:
        return f"within_{int(freshness_days)}_days"
    if _has_any(query, _TIME_MARKERS) or re.search(r"\b20\d{2}\b", query):
        return "recent_preferred"
    return "not_required"


def _derive_source_policy(state: Dict[str, Any], config: Dict[str, Any]) -> str:
    cfg = config.get("configurable") if isinstance(config, dict) else {}
    cfg = cfg if isinstance(cfg, dict) else {}
    configured = str(cfg.get("source_policy") or state.get("source_policy") or "").strip().lower()
    if configured in {"web", "web-only", "private-first", "hybrid", "rag", "local"}:
        return "web" if configured == "web-only" else configured
    if bool(cfg.get("use_rag") or state.get("use_rag")):
        return "hybrid"
    return "web"


def build_research_brief(state: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> ResearchBrief:
    config = config or {}
    existing = state.get("research_brief") or state.get("deepsearch_research_brief")
    if isinstance(existing, ResearchBrief):
        return existing
    if isinstance(existing, dict):
        return ResearchBrief(
            original_query=str(existing.get("original_query") or state.get("input") or ""),
            clarified_goal=str(existing.get("clarified_goal") or existing.get("goal") or state.get("input") or ""),
            scope=str(existing.get("scope") or ""),
            constraints=existing.get("constraints") if isinstance(existing.get("constraints"), dict) else {},
            expected_fields=_clean_list(existing.get("expected_fields")),
            preferred_sources=_clean_list(existing.get("preferred_sources")),
            excluded_sources=_clean_list(existing.get("excluded_sources")),
            freshness_requirement=str(existing.get("freshness_requirement") or ""),
            output_format=str(existing.get("output_format") or "research_report"),
            success_criteria=_clean_list(existing.get("success_criteria")),
            source_policy=str(existing.get("source_policy") or "web"),
            complexity=str(existing.get("complexity") or "standard"),
        )

    query = str(state.get("input") or state.get("topic") or "").strip()
    constraints = state.get("constraints") if isinstance(state.get("constraints"), dict) else {}
    expected_fields = _clean_list(state.get("expected_fields")) or _derive_expected_fields(query)
    freshness = _derive_freshness(query, constraints)
    success_criteria = [f"cover:{field_name}" for field_name in expected_fields]
    if freshness != "not_required":
        success_criteria.append(f"freshness:{freshness}")
    return ResearchBrief(
        original_query=query,
        clarified_goal=query,
        scope=str(state.get("scope") or "").strip(),
        constraints=constraints,
        expected_fields=expected_fields,
        preferred_sources=_clean_list(state.get("preferred_sources")),
        excluded_sources=_clean_list(state.get("excluded_sources")),
        freshness_requirement=freshness,
        output_format=str(state.get("output_format") or "research_report"),
        success_criteria=success_criteria,
        source_policy=_derive_source_policy(state, config),
        complexity=_derive_complexity(query, expected_fields),
    )


def brief_topic(brief: ResearchBrief) -> str:
    context = brief.prompt_context()
    return context if context.strip() else brief.original_query
