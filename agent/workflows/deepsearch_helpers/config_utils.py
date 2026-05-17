"""Configuration resolution and cancellation helpers for DeepSearch."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional

from agent.workflows.domain_router import ResearchDomain, build_provider_profile
from common.cancellation import check_cancellation as _check_cancel_token
from common.config import settings
from tools.search.multi_search import SearchStrategy

logger = logging.getLogger(__name__)

_DEEPSEARCH_MODES = {"tree", "supervisor_workers"}
_SIMPLE_FACT_PATTERNS = (
    r"\bwhat\s+is\b",
    r"\bwho\s+is\b",
    r"\bwhen\s+(?:is|was|did)\b",
    r"\bwhere\s+(?:is|was)\b",
    r"\bwhich\s+is\b",
    r"\bhow\s+many\b",
    r"\bcapital\s+of\b",
    r"\bpopulation\s+of\b",
    r"\breply\s+with\b",
    r"\bone\s+word\b",
    r"是什么",
    r"谁是",
    r"何时",
    r"哪里",
    r"在哪",
    r"多少",
    r"首都",
    r"人口",
    r"只回答",
    r"一个词",
)
_BROAD_RESEARCH_CUES = (
    "analysis",
    "analyze",
    "assess",
    "case study",
    "cases",
    "compare",
    "comparison",
    "deep research",
    "evaluate",
    "framework",
    "histor",
    "impact",
    "investigate",
    "latest",
    "market",
    "overview",
    "policy",
    "regulation",
    "report",
    "research",
    "survey",
    "timeline",
    "trend",
    "updates",
    "versus",
    "vs",
    "分析",
    "影响",
    "报告",
    "对比",
    "挑战",
    "政策",
    "框架",
    "比较",
    "法规",
    "深度",
    "研究",
    "综述",
    "调研",
    "趋势",
    "历史",
)


def _check_cancel(state: dict[str, Any]) -> None:
    """Respect cancellation flags/tokens."""
    if state.get("is_cancelled"):
        raise asyncio.CancelledError("Task was cancelled (flag)")
    token_id = state.get("cancel_token_id")
    if token_id:
        _check_cancel_token(token_id)


def _normalize_deepsearch_mode(value: Any) -> str:
    """Normalize deepsearch mode to a supported DeepSearch execution mode."""
    mode = str(value or "").strip().lower().replace("-", "_")
    if mode in {"reflection", "reflection_loop", "hybrid", "hybrid_private_web"}:
        mode = "supervisor_workers"
    if mode in {"supervisor", "workers", "supervisor_worker"}:
        mode = "supervisor_workers"
    if mode in {"linear", "linear_light", "light"}:
        mode = "supervisor_workers"
    if mode in _DEEPSEARCH_MODES:
        return mode
    return "supervisor_workers"


def _resolve_deepsearch_mode(config: dict[str, Any]) -> str:
    """
    Resolve deepsearch mode with precedence:
    1. request/configurable.deepsearch_mode
    2. settings.deepsearch_mode
    3. supervisor_workers
    """
    cfg = config.get("configurable") or {}
    runtime_mode = cfg.get("deepsearch_mode") if isinstance(cfg, dict) else None
    if runtime_mode is not None:
        return _normalize_deepsearch_mode(runtime_mode)

    return _normalize_deepsearch_mode(
        getattr(settings, "deepsearch_mode", "supervisor_workers")
    )


def _configurable_value(config: dict[str, Any], key: str) -> Any:
    cfg = config.get("configurable") or {}
    if isinstance(cfg, dict):
        return cfg.get(key)
    return None


def _configurable_int(config: dict[str, Any], key: str, default: int) -> int:
    value = _configurable_value(config, key)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _configurable_float(config: dict[str, Any], key: str, default: float) -> float:
    value = _configurable_value(config, key)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _configurable_bool(config: dict[str, Any], key: str, default: bool) -> bool:
    value = _configurable_value(config, key)
    if value is None:
        return bool(default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _browser_visualization_enabled(config: dict[str, Any]) -> bool:
    value = _configurable_value(config, "deepsearch_visualize_browser")
    if value is None:
        return bool(getattr(settings, "deepsearch_visualize_browser", True))
    return bool(value)


def _auto_mode_prefers_linear(topic: str) -> bool:
    """Use the cheaper linear runner for obvious factual prompts in auto mode."""
    text = re.sub(r"\s+", " ", str(topic or "")).strip()
    if not text:
        return False

    lowered = text.lower()
    if any(
        re.search(pattern, lowered, flags=re.IGNORECASE)
        for pattern in _SIMPLE_FACT_PATTERNS
    ):
        return True

    if any(cue in lowered for cue in _BROAD_RESEARCH_CUES):
        return False
    return False


def _resolve_search_strategy() -> SearchStrategy:
    raw = (
        str(getattr(settings, "search_strategy", "fallback") or "fallback")
        .strip()
        .lower()
    )
    try:
        return SearchStrategy(raw)
    except ValueError:
        logger.warning(
            f"[deepsearch] invalid search_strategy='{raw}', fallback to 'fallback'"
        )
        return SearchStrategy.FALLBACK


def _normalize_multi_search_results(
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for r in results:
        if not isinstance(r, dict):
            continue
        published_date = next(
            (
                r.get(key)
                for key in (
                    "published_date",
                    "publishedDate",
                    "datePublished",
                    "publishedAt",
                    "published_at",
                    "date_published",
                    "pubDate",
                    "displayDate",
                    "date",
                    "timestamp",
                )
                if r.get(key)
            ),
            None,
        )
        normalized.append(
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "summary": r.get("summary") or r.get("snippet", ""),
                "raw_excerpt": r.get("raw_excerpt") or r.get("content", ""),
                "score": float(r.get("score", 0.5) or 0.5),
                "published_date": published_date,
                "publishedDate": published_date,
                "provider": r.get("provider", ""),
            }
        )
    return normalized


def _resolve_provider_profile(state: dict[str, Any]) -> Optional[list[str]]:
    """Build provider profile from domain routing metadata if present."""
    domain_config = state.get("domain_config") or {}
    suggested_sources = domain_config.get("suggested_sources", [])
    domain_value = state.get("domain") or domain_config.get("domain") or "general"
    try:
        domain = ResearchDomain(str(domain_value).strip().lower())
    except ValueError:
        domain = ResearchDomain.GENERAL

    profile = build_provider_profile(suggested_sources=suggested_sources, domain=domain)
    return profile or None
