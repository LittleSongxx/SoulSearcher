from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from agent.workflows.source_routing import (
    build_source_routing_policy,
    source_policy_from_routing,
)

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
    constraints: dict[str, Any] = field(default_factory=dict)
    expected_fields: list[str] = field(default_factory=list)
    preferred_sources: list[str] = field(default_factory=list)
    excluded_sources: list[str] = field(default_factory=list)
    source_preferences: list[str] = field(default_factory=list)
    open_dimensions: list[str] = field(default_factory=list)
    skill_ids: list[str] = field(default_factory=list)
    mcp_policy: dict[str, Any] = field(default_factory=dict)
    freshness_requirement: str = ""
    language: str = ""
    audience: str = ""
    output_format: str = "research_report"
    citation_policy: str = "required"
    budget_policy: dict[str, Any] = field(default_factory=dict)
    success_criteria: list[str] = field(default_factory=list)
    source_policy: str = "web"
    source_routing: dict[str, Any] = field(default_factory=dict)
    complexity: str = "standard"

    def to_dict(self) -> dict[str, Any]:
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


def _clean_list(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [part.strip() for part in values.split(",") if part.strip()]
    if not isinstance(values, list):
        return []
    cleaned: list[str] = []
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


def _derive_expected_fields(query: str) -> list[str]:
    fields: list[str] = []
    if _has_any(query, _COMPARISON_MARKERS):
        fields.extend(["comparison_dimensions", "pros_cons", "recommendation"])
    if _has_any(query, _TIME_MARKERS):
        fields.append("recent_updates")
    if _has_any(query, _RESEARCH_MARKERS):
        fields.extend(["background", "key_findings", "evidence", "risks"])
    if not fields:
        fields.extend(["answer", "evidence"])
    deduped: list[str] = []
    seen = set()
    for field_name in fields:
        if field_name not in seen:
            seen.add(field_name)
            deduped.append(field_name)
    return deduped


def _derive_complexity(query: str, expected_fields: list[str]) -> str:
    if len(expected_fields) >= 5 or _has_any(query, _COMPARISON_MARKERS):
        return "broad"
    if _has_any(query, _RESEARCH_MARKERS):
        return "standard"
    return "light"


def _derive_freshness(query: str, constraints: dict[str, Any]) -> str:
    freshness_days = (
        constraints.get("freshness_days") if isinstance(constraints, dict) else None
    )
    if isinstance(freshness_days, (int, float)) and int(freshness_days) > 0:
        return f"within_{int(freshness_days)}_days"
    if _has_any(query, _TIME_MARKERS) or re.search(r"\b20\d{2}\b", query):
        return "recent_preferred"
    return "not_required"


def _derive_source_policy(state: dict[str, Any], config: dict[str, Any]) -> str:
    cfg = config.get("configurable") if isinstance(config, dict) else {}
    cfg = cfg if isinstance(cfg, dict) else {}
    routing = cfg.get("source_routing") or state.get("source_routing")
    if isinstance(routing, dict) and routing:
        routed = source_policy_from_routing(routing)
        if routed:
            return routed
    configured = (
        str(cfg.get("source_policy") or state.get("source_policy") or "")
        .strip()
        .lower()
    )
    if configured in {
        "web",
        "web-only",
        "private-first",
        "hybrid",
        "rag",
        "local",
        "mcp",
    }:
        return "web" if configured == "web-only" else configured
    if bool(cfg.get("use_rag") or state.get("use_rag")):
        return "hybrid"
    return "web"


def _derive_open_dimensions(query: str, expected_fields: list[str]) -> list[str]:
    dimensions = []
    if not any(field in expected_fields for field in ("risks", "limitations")):
        dimensions.append("risks_or_limitations")
    if (
        _has_any(query, _COMPARISON_MARKERS)
        and "evaluation_criteria" not in expected_fields
    ):
        dimensions.append("evaluation_criteria")
    if not _has_any(query, _TIME_MARKERS):
        dimensions.append("time_range_if_relevant")
    return dimensions


def _derive_mcp_policy(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    cfg = config.get("configurable") if isinstance(config, dict) else {}
    cfg = cfg if isinstance(cfg, dict) else {}
    whitelist = _clean_list(
        cfg.get("mcp_tool_whitelist")
        or cfg.get("mcp_tools_to_include")
        or state.get("mcp_tool_whitelist")
        or state.get("mcp_tools_to_include")
    )
    strategy = (
        str(
            cfg.get("mcp_strategy")
            or cfg.get("deepsearch_mcp_strategy")
            or state.get("mcp_strategy")
            or ""
        )
        .strip()
        .lower()
    )
    auth_required = bool(
        cfg.get("mcp_auth_required")
        or cfg.get("mcp_requires_auth")
        or state.get("mcp_auth_required")
    )
    policy = {
        "strategy": strategy or "disabled",
        "tool_whitelist": whitelist,
        "auth_required": auth_required,
    }
    return {
        key: value for key, value in policy.items() if value not in (None, "", [], {})
    }


def build_research_brief(
    state: dict[str, Any], config: Optional[dict[str, Any]] = None
) -> ResearchBrief:
    config = config or {}
    existing = state.get("research_brief") or state.get("deepsearch_research_brief")
    if isinstance(existing, ResearchBrief):
        return existing
    if isinstance(existing, dict):
        brief = ResearchBrief(
            original_query=str(
                existing.get("original_query") or state.get("input") or ""
            ),
            clarified_goal=str(
                existing.get("clarified_goal")
                or existing.get("goal")
                or state.get("input")
                or ""
            ),
            scope=str(existing.get("scope") or ""),
            constraints=(
                existing.get("constraints")
                if isinstance(existing.get("constraints"), dict)
                else {}
            ),
            expected_fields=_clean_list(existing.get("expected_fields")),
            preferred_sources=_clean_list(existing.get("preferred_sources")),
            excluded_sources=_clean_list(existing.get("excluded_sources")),
            source_preferences=_clean_list(existing.get("source_preferences")),
            open_dimensions=_clean_list(existing.get("open_dimensions")),
            skill_ids=_clean_list(existing.get("skill_ids")),
            mcp_policy=(
                existing.get("mcp_policy")
                if isinstance(existing.get("mcp_policy"), dict)
                else _derive_mcp_policy(state, config)
            ),
            freshness_requirement=str(existing.get("freshness_requirement") or ""),
            language=str(existing.get("language") or ""),
            audience=str(existing.get("audience") or ""),
            output_format=str(existing.get("output_format") or "research_report"),
            citation_policy=str(existing.get("citation_policy") or "required"),
            budget_policy=(
                existing.get("budget_policy")
                if isinstance(existing.get("budget_policy"), dict)
                else {}
            ),
            success_criteria=_clean_list(existing.get("success_criteria")),
            source_policy=str(existing.get("source_policy") or "web"),
            source_routing=(
                existing.get("source_routing")
                if isinstance(existing.get("source_routing"), dict)
                else {}
            ),
            complexity=str(existing.get("complexity") or "standard"),
        )
        if not brief.source_routing:
            brief.source_routing = build_source_routing_policy(
                brief=brief, config=config, state=state
            )
        brief.source_policy = source_policy_from_routing(brief.source_routing)
        return brief

    query = str(state.get("input") or state.get("topic") or "").strip()
    constraints = (
        state.get("constraints") if isinstance(state.get("constraints"), dict) else {}
    )
    expected_fields = _clean_list(
        state.get("expected_fields")
    ) or _derive_expected_fields(query)
    freshness = _derive_freshness(query, constraints)
    success_criteria = [f"cover:{field_name}" for field_name in expected_fields]
    if freshness != "not_required":
        success_criteria.append(f"freshness:{freshness}")
    brief = ResearchBrief(
        original_query=query,
        clarified_goal=query,
        scope=str(state.get("scope") or "").strip(),
        constraints=constraints,
        expected_fields=expected_fields,
        preferred_sources=_clean_list(state.get("preferred_sources")),
        excluded_sources=_clean_list(state.get("excluded_sources")),
        source_preferences=_clean_list(state.get("source_preferences")),
        open_dimensions=_derive_open_dimensions(query, expected_fields),
        skill_ids=_clean_list(
            state.get("skill_ids") or state.get("deepsearch_skill_ids")
        ),
        mcp_policy=_derive_mcp_policy(state, config),
        freshness_requirement=freshness,
        language=str(state.get("language") or "").strip(),
        audience=str(state.get("audience") or "").strip(),
        output_format=str(state.get("output_format") or "research_report"),
        citation_policy=str(state.get("citation_policy") or "required"),
        budget_policy=(
            state.get("budget_policy")
            if isinstance(state.get("budget_policy"), dict)
            else {}
        ),
        success_criteria=success_criteria,
        source_policy=_derive_source_policy(state, config),
        complexity=_derive_complexity(query, expected_fields),
    )
    brief.source_routing = build_source_routing_policy(
        brief=brief, config=config, state=state
    )
    brief.source_policy = source_policy_from_routing(brief.source_routing)
    return brief


def brief_topic(brief: ResearchBrief) -> str:
    context = brief.prompt_context()
    return context if context.strip() else brief.original_query
