"""
Optimized DeepSearch implementation with enhanced features.

Key improvements:
1. URL deduplication mechanism
2. Detailed performance logging
3. Enhanced error handling
4. Better cancellation support
5. OOP encapsulation (optional)
6. Tree-based exploration (new)
7. Multi-model support (new)

Based on: deep_search-dev reference implementation
"""

import asyncio
import copy
import hashlib
import json
import logging
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from agent.core.llm_factory import create_chat_model
from agent.core.search_cache import get_search_cache
from agent.workflows.domain_router import ResearchDomain, build_provider_profile
from agent.workflows.evidence_passages import split_into_passages
from agent.workflows.knowledge_gap import KnowledgeGapAnalyzer
from agent.workflows.citation_artifacts import (
    build_citation_annotations,
    build_timeline_artifacts,
)
from agent.workflows.claim_ledger import (
    build_claim_ledger,
    format_claim_ledger_for_writer,
    serialize_claim_checks,
    summarize_claim_checks,
)
from agent.workflows.evidence import build_evidence_items
from agent.workflows.evidence_providers import (
    build_evidence_providers,
    build_provider_capability_artifact,
    merge_provider_evidence,
    merge_provider_results,
    search_with_evidence_providers,
)
from agent.workflows.deepsearch_model_profile import build_deepsearch_model_profile
from agent.workflows.model_context_policy import build_deepsearch_context_policy
from agent.workflows.parsing_utils import format_search_results, parse_list_output
from agent.workflows.quality_gates import (
    default_policy,
    evaluate_quality_gates,
    missing_topics_from_gates,
    serialize_gate_results,
)
from agent.workflows.query_strategy import (
    analyze_query_coverage,
    backfill_diverse_queries,
    is_time_sensitive_topic,
    summarize_freshness,
)
from agent.workflows.research_reflection import gap_queries_from_quality_gates
from agent.workflows.research_brief import ResearchBrief, brief_topic, build_research_brief
from agent.workflows.research_pipeline import build_supervisor_workers_pipeline_artifact
from agent.workflows.research_task_runtime import ResearchTaskRuntime
from agent.workflows.research_tree import TreeExplorationBudgetExceeded, TreeExplorer
from agent.workflows.report_plan import build_sectioned_report_artifact, build_sectioned_report_plan
from agent.workflows.source_curator import curate_sources
from agent.workflows.source_url_utils import canonicalize_source_url, compact_unique_sources
from agent.workflows.strategy_selector import select_deepsearch_strategy
from agent.workflows.supervisor_workers import (
    build_intermediate_steps,
    build_worker_run,
    build_worker_tasks,
    decide_supervisor_next_step,
)
from common.cancellation import check_cancellation as _check_cancel_token
from common.config import settings
from prompts.templates.deepsearch import (
    final_summary_prompt,
    formulate_query_prompt,
    related_url_prompt,
    summary_crawl_prompt,
    summary_text_prompt,
)
from tools.crawl.crawler import crawl_urls
from tools.research.content_fetcher import ContentFetcher
from tools.search.multi_search import SearchStrategy, multi_search
from tools.search.search import tavily_search

logger = logging.getLogger(__name__)

# Use shared implementations
_chat_model = create_chat_model
_parse_list_output = parse_list_output
_format_results = format_search_results

_DEEPSEARCH_MODES = {"auto", "tree", "linear", "reflection_loop", "supervisor_workers"}
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


def _check_cancel(state: Dict[str, Any]) -> None:
    """Respect cancellation flags/tokens."""
    if state.get("is_cancelled"):
        raise asyncio.CancelledError("Task was cancelled (flag)")
    token_id = state.get("cancel_token_id")
    if token_id:
        _check_cancel_token(token_id)


def _normalize_deepsearch_mode(value: Any) -> str:
    """Normalize deepsearch mode to one of: auto, tree, linear, reflection_loop."""
    mode = str(value or "").strip().lower().replace("-", "_")
    if mode == "reflection":
        mode = "reflection_loop"
    if mode in {"supervisor", "workers", "supervisor_worker"}:
        mode = "supervisor_workers"
    if mode in _DEEPSEARCH_MODES:
        return mode
    return "auto"


def _resolve_deepsearch_mode(config: Dict[str, Any]) -> str:
    """
    Resolve deepsearch mode with precedence:
    1. request/configurable.deepsearch_mode
    2. settings.deepsearch_mode
    3. auto
    """
    cfg = config.get("configurable") or {}
    runtime_mode = cfg.get("deepsearch_mode") if isinstance(cfg, dict) else None
    if runtime_mode is not None:
        return _normalize_deepsearch_mode(runtime_mode)

    return _normalize_deepsearch_mode(getattr(settings, "deepsearch_mode", "auto"))


def _configurable_value(config: Dict[str, Any], key: str) -> Any:
    cfg = config.get("configurable") or {}
    if isinstance(cfg, dict):
        return cfg.get(key)
    return None


def _configurable_int(config: Dict[str, Any], key: str, default: int) -> int:
    value = _configurable_value(config, key)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _configurable_float(config: Dict[str, Any], key: str, default: float) -> float:
    value = _configurable_value(config, key)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _configurable_bool(config: Dict[str, Any], key: str, default: bool) -> bool:
    value = _configurable_value(config, key)
    if value is None:
        return bool(default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _browser_visualization_enabled(config: Dict[str, Any]) -> bool:
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
    if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in _SIMPLE_FACT_PATTERNS):
        return True

    if any(cue in lowered for cue in _BROAD_RESEARCH_CUES):
        return False
    return False


def _resolve_search_strategy() -> SearchStrategy:
    raw = str(getattr(settings, "search_strategy", "fallback") or "fallback").strip().lower()
    try:
        return SearchStrategy(raw)
    except ValueError:
        logger.warning(f"[deepsearch] invalid search_strategy='{raw}', fallback to 'fallback'")
        return SearchStrategy.FALLBACK


def _normalize_multi_search_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for r in results:
        if not isinstance(r, dict):
            continue
        normalized.append(
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "summary": r.get("summary") or r.get("snippet", ""),
                "raw_excerpt": r.get("raw_excerpt") or r.get("content", ""),
                "score": float(r.get("score", 0.5) or 0.5),
                "published_date": r.get("published_date"),
                "provider": r.get("provider", ""),
            }
        )
    return normalized


def _resolve_provider_profile(state: Dict[str, Any]) -> Optional[List[str]]:
    """Build provider profile from domain routing metadata if present."""
    domain_config = state.get("domain_config") or {}
    suggested_sources = domain_config.get("suggested_sources", [])
    domain_value = (state.get("domain") or domain_config.get("domain") or "general")
    try:
        domain = ResearchDomain(str(domain_value).strip().lower())
    except ValueError:
        domain = ResearchDomain.GENERAL

    profile = build_provider_profile(suggested_sources=suggested_sources, domain=domain)
    return profile or None


def _cache_query_key(
    query: str,
    max_results: int,
    strategy: SearchStrategy,
    provider_profile: Optional[List[str]] = None,
) -> str:
    profile = ",".join((provider_profile or []))
    return f"deepsearch::{strategy.value}::{max_results}::{profile}::{query}"


def _estimate_tokens_from_text(text: str) -> int:
    if not text:
        return 0
    return max(1, len(str(text)) // 4)


def _estimate_tokens_from_results(results: List[Dict[str, Any]]) -> int:
    tokens = 0
    for result in results or []:
        if not isinstance(result, dict):
            continue
        tokens += _estimate_tokens_from_text(result.get("title", ""))
        snippet = (
            result.get("raw_excerpt")
            or result.get("summary")
            or result.get("snippet")
            or result.get("content")
            or ""
        )
        tokens += _estimate_tokens_from_text(str(snippet)[:600])
    return tokens


def _budget_stop_reason(
    start_ts: float,
    tokens_used: int,
    max_seconds: float,
    max_tokens: int,
) -> Optional[str]:
    if max_seconds > 0 and (time.time() - start_ts) >= max_seconds:
        return "time_budget_exceeded"
    if max_tokens > 0 and tokens_used >= max_tokens:
        return "token_budget_exceeded"
    return None


def _search_query(
    query: str,
    max_results: int,
    config: Dict[str, Any],
    provider_profile: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Search with multi-provider orchestration first, then Tavily fallback."""
    strategy = _resolve_search_strategy()
    cache = get_search_cache()
    cache_key = _cache_query_key(query, max_results, strategy, provider_profile)
    cached = cache.get(cache_key)
    if cached is not None:
        logger.info(f"[deepsearch] cache hit for query='{query[:80]}'")
        return copy.deepcopy(cached)

    try:
        kwargs: Dict[str, Any] = {
            "query": query,
            "max_results": max_results,
            "strategy": strategy,
        }
        if provider_profile:
            kwargs["provider_profile"] = provider_profile
        multi_results = multi_search(**kwargs)
        normalized = _normalize_multi_search_results(multi_results)
        if normalized:
            cache.set(cache_key, copy.deepcopy(normalized))
            return normalized
        logger.info(f"[deepsearch] multi_search returned no results for query='{query[:80]}'")
    except Exception as e:
        logger.warning(f"[deepsearch] multi_search failed, falling back to tavily: {e}")

    try:
        fallback_results = tavily_search.invoke(
            {"query": query, "max_results": max_results},
            config=config,
        )
        if fallback_results:
            cache.set(cache_key, copy.deepcopy(fallback_results))
        return fallback_results
    except Exception as e:
        logger.warning(f"[deepsearch] tavily fallback failed: {e}")
        return []


def _selected_model(config: Dict[str, Any], fallback: str) -> str:
    cfg = config.get("configurable") or {}
    if isinstance(cfg, dict):
        val = cfg.get("model")
        if isinstance(val, str) and val.strip():
            return val.strip()
    return fallback


def _selected_reasoning_model(config: Dict[str, Any], fallback: str) -> str:
    cfg = config.get("configurable") or {}
    if isinstance(cfg, dict):
        val = cfg.get("reasoning_model")
        if isinstance(val, str) and val.strip():
            return val.strip()
    return fallback


def _model_for_task(task_type: str, config: Dict[str, Any]) -> str:
    """
    Get model name for a specific task type using the ModelRouter.

    Args:
        task_type: One of: planning, query_gen, research, critique, synthesis, writing
        config: RunnableConfig dict with optional overrides
    """
    try:
        from agent.core.multi_model import TaskType, get_model_router

        tt = TaskType(task_type)
        router = get_model_router()
        return router.get_model_name(tt, config)
    except Exception:
        # Fallback to legacy behavior
        if task_type in ("planning", "query_gen", "critique", "gap_analysis"):
            return _selected_reasoning_model(config, settings.reasoning_model)
        return _selected_model(config, settings.primary_model)


def _generate_queries(
    llm: ChatOpenAI,
    topic: str,
    have_query: List[str],
    summary_notes: List[str],
    query_num: int,
    config: Dict[str, Any],
    missing_topics: Optional[List[str]] = None,
) -> List[str]:
    """Generate new search queries based on topic, existing knowledge, and knowledge gaps.

    If missing_topics is provided (from gap analysis), prioritizes those areas.
    """
    # If we have missing topics from gap analysis, incorporate them
    enhanced_topic = topic
    if missing_topics:
        gap_hint = f"\n\n注意：以下方面信息仍然不足，请优先覆盖：{', '.join(missing_topics[:3])}"
        enhanced_topic = topic + gap_hint

    prompt = ChatPromptTemplate.from_messages([("user", formulate_query_prompt)])
    msg = prompt.format_messages(
        topic=enhanced_topic,
        have_query=", ".join(have_query) or "[]",
        summary_search="\n\n".join(summary_notes) or "暂无",
        query_num=query_num,
    )
    response = llm.invoke(msg, config=config)
    content = getattr(response, "content", "") or ""
    queries = _parse_list_output(content)
    # Deduplicate and trim
    seen = set(q.lower() for q in have_query)
    clean: List[str] = []
    for q in queries:
        if not q:
            continue
        q_norm = q.strip()
        if not q_norm or q_norm.lower() in seen:
            continue
        seen.add(q_norm.lower())
        clean.append(q_norm)
        if len(clean) >= query_num:
            break
    return backfill_diverse_queries(
        topic=topic,
        existing_queries=clean,
        historical_queries=have_query,
        query_num=query_num,
    )


def _pick_relevant_urls(
    llm: ChatOpenAI,
    topic: str,
    summary_notes: List[str],
    results: List[Dict[str, Any]],
    max_urls: int,
    config: Dict[str, Any],
    selected_urls_set: set,  # Use set for O(1) lookup
) -> List[str]:
    """Pick relevant URLs from search results, excluding already selected ones."""
    if not results:
        return []

    # Filter already selected URLs with O(1) set lookup
    available_results = []
    for r in results:
        if not isinstance(r, dict):
            continue
        canonical_url = canonicalize_source_url(r.get("url"))
        if not canonical_url or canonical_url in selected_urls_set:
            continue
        enriched = dict(r)
        enriched["_canonical_url"] = canonical_url
        available_results.append(enriched)

    if not available_results:
        logger.info("All URLs have been selected, no new URLs available")
        return []

    formatted = _format_results(available_results)
    prompt = ChatPromptTemplate.from_messages([("user", related_url_prompt)])
    msg = prompt.format_messages(
        topic=topic,
        summary_search="\n\n".join(summary_notes) or "暂无",
        text=formatted,
    )
    response = llm.invoke(msg, config=config)
    urls = _parse_list_output(getattr(response, "content", "") or "")

    # Fallback: top scores
    if not urls:
        sorted_results = sorted(available_results, key=lambda r: r.get("score", 0), reverse=True)
        urls = [r.get("_canonical_url") for r in sorted_results if r.get("_canonical_url")]

    # Clamp and dedupe
    deduped: List[str] = []
    seen = set()
    for u in urls:
        if not isinstance(u, str):
            continue
        u = canonicalize_source_url(u)
        if not u or u in seen or u in selected_urls_set:
            continue
        seen.add(u)
        deduped.append(u)
        if len(deduped) >= max_urls:
            break
    return deduped


def _summarize_new_knowledge(
    llm: ChatOpenAI,
    topic: str,
    summary_notes: List[str],
    chosen_results: List[Dict[str, Any]],
    config: Dict[str, Any],
) -> Tuple[bool, str]:
    """Summarize new knowledge and judge if information is sufficient."""
    if not chosen_results:
        return False, ""

    prompt = ChatPromptTemplate.from_messages([("user", summary_crawl_prompt)])
    msg = prompt.format_messages(
        summary_search="\n\n".join(summary_notes) or "暂无",
        crawl_res=_format_results(chosen_results),
        topic=topic,
    )
    response = llm.invoke(msg, config=config)
    content = getattr(response, "content", "") or ""
    lowered = content.lower()
    enough = "回答" in lowered and "yes" in lowered.split("回答", 1)[-1]

    # Extract summary after "总结:" if present
    summary_text = ""
    if "总结" in content:
        summary_text = content.split("总结", 1)[-1].strip(":： \n")
    if not summary_text:
        summary_text = content
    return enough, summary_text.strip()


def _final_report(
    llm: ChatOpenAI,
    topic: str,
    summary_notes: List[str],
    config: Dict[str, Any],
    *,
    sources: str = "",
) -> str:
    """Generate final report based on all summaries."""
    prompt = ChatPromptTemplate.from_messages([("user", final_summary_prompt)])
    msg = prompt.format_messages(
        topic=topic,
        summary_search="\n\n".join(summary_notes) or "暂无",
        sources=sources or "暂无",
    )
    response = llm.invoke(msg, config=config)
    return getattr(response, "content", "") or summary_text_prompt


def _reorder_search_runs_for_citations(
    search_runs: List[Dict[str, Any]],
    *,
    preferred_urls: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Stable reorder to improve citation/source relevance.

    We keep the same content, but move "preferred" URLs (typically the selected URLs that were
    summarized) earlier so that `extract_message_sources()` assigns lower citation numbers to them.

    This improves:
    - report citation usefulness ([1]..[N] are more likely to be the actually-used sources)
    - frontend SourceInspector alignment (since sources are extracted from `scraped_content`)
    """
    if not search_runs:
        return []
    if not preferred_urls:
        return list(search_runs)

    try:
        from agent.workflows.source_registry import SourceRegistry

        registry = SourceRegistry()
        preferred_canonical: set[str] = set()
        for raw in preferred_urls:
            canon = registry.canonicalize_url(str(raw or ""))
            if canon:
                preferred_canonical.add(canon)
        if not preferred_canonical:
            return list(search_runs)

        preferred_runs: List[Dict[str, Any]] = []
        other_runs: List[Dict[str, Any]] = []

        for run in search_runs:
            if not isinstance(run, dict):
                continue

            results = run.get("results") or []
            if not isinstance(results, list):
                results = []

            preferred_results: List[Any] = []
            other_results: List[Any] = []
            for r in results:
                if not isinstance(r, dict):
                    other_results.append(r)
                    continue
                url = str(r.get("url") or "").strip()
                canon = registry.canonicalize_url(url) if url else ""
                if canon and canon in preferred_canonical:
                    preferred_results.append(r)
                else:
                    other_results.append(r)

            new_run = {**run, "results": preferred_results + other_results}
            if preferred_results:
                preferred_runs.append(new_run)
            else:
                other_runs.append(new_run)

        # Preserve determinism: stable partition only.
        return preferred_runs + other_runs
    except Exception:
        return list(search_runs)


def _format_sources_for_writer(
    sources: List[Dict[str, Any]],
    search_runs: List[Dict[str, Any]],
    *,
    limit: int,
) -> str:
    """
    Render sources into a compact, numbered block for the writer prompt.

    Numbering must match `extract_message_sources()` ordering so the frontend can
    display the same `[n]` mapping.
    """
    if not sources:
        return "暂无可引用来源。"

    max_count = max(1, int(limit or 0)) if limit else len(sources)
    rendered_sources = sources[:max_count]

    snippet_by_canonical: Dict[str, str] = {}
    try:
        from agent.workflows.source_registry import SourceRegistry

        registry = SourceRegistry()
        for run in search_runs or []:
            if not isinstance(run, dict):
                continue
            results = run.get("results") or []
            if not isinstance(results, list):
                continue
            for item in results:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "").strip()
                if not url:
                    continue
                canon = registry.canonicalize_url(url) or url
                if canon in snippet_by_canonical:
                    continue

                raw_snippet = (
                    item.get("raw_excerpt")
                    or item.get("summary")
                    or item.get("snippet")
                    or item.get("content")
                    or ""
                )
                snippet = re.sub(r"\s+", " ", str(raw_snippet)).strip()
                if snippet:
                    snippet_by_canonical[canon] = snippet[:280]
    except Exception:
        snippet_by_canonical = {}

    lines: List[str] = []
    for idx, src in enumerate(rendered_sources, 1):
        if not isinstance(src, dict):
            continue
        title = str(src.get("title") or "").strip() or "Untitled"
        canonical_url = str(src.get("url") or "").strip()
        raw_url = str(src.get("rawUrl") or "").strip()
        href = raw_url or canonical_url

        domain = str(src.get("domain") or "").strip()
        provider = str(src.get("provider") or "").strip()
        published = str(src.get("publishedDate") or "").strip()
        meta_parts = [p for p in (domain, provider, published) if p and p.lower() != "none"]
        meta = " | ".join(meta_parts)

        snippet = ""
        if canonical_url:
            snippet = snippet_by_canonical.get(canonical_url, "")
        if not snippet and href:
            snippet = snippet_by_canonical.get(href, "")

        header = f"[{idx}] {title}"
        if meta:
            header = f"{header} ({meta})"
        lines.append(header)
        if href:
            lines.append(f"URL: {href}")
        if snippet:
            lines.append(f"摘要片段: {snippet}")
        lines.append("")  # blank line between sources

    return "\n".join(lines).strip() or "暂无可引用来源。"


def _append_auto_references(
    report: str,
    sources: List[Dict[str, Any]],
    *,
    limit: int,
) -> str:
    if not report:
        return report

    heading = "## 参考来源（自动生成）"
    if heading in report:
        return report

    max_count = max(1, int(limit or 0)) if limit else len(sources)
    rendered_sources = sources[:max_count]

    items: List[str] = []
    for idx, src in enumerate(rendered_sources, 1):
        if not isinstance(src, dict):
            continue
        title = str(src.get("title") or "").strip() or "Untitled"
        url = str(src.get("rawUrl") or src.get("url") or "").strip()
        if not url:
            continue
        items.append(f"- [{idx}] {title} — {url}")

    if not items:
        return report

    block = "\n".join([heading, "", *items]).rstrip()
    return report.rstrip() + "\n\n" + block + "\n"


def _source_urls_for_fetch(sources: List[Dict[str, Any]], *, limit: int) -> List[str]:
    urls: List[str] = []
    seen = set()
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        raw = str(source.get("rawUrl") or source.get("url") or "").strip()
        canonical = canonicalize_source_url(raw) or raw
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        urls.append(raw or canonical)
        if len(urls) >= max(1, int(limit or 1)):
            break
    return urls


def _estimate_citation_coverage(report: str) -> Tuple[List[str], float]:
    if not report:
        return [], 1.0
    body = report.split("## 参考来源（自动生成）", 1)[0]
    sentences = re.split(r"(?<=[。！？.!?])\s+|\n+", body)
    markers = (
        r"\d{4}",
        r"\d+%",
        r"\d+\.\d+",
        r"according to|report|study|data|shows|found|announced",
        r"报告|研究|数据显示|统计|公告|监管|增长|下降|发布",
    )
    citation_pattern = re.compile(r"\[(?:S?\d+)\]")
    claim_like: List[str] = []
    for sentence in sentences:
        text = re.sub(r"\s+", " ", sentence).strip()
        if len(text) < 20:
            continue
        if any(re.search(marker, text, flags=re.IGNORECASE) for marker in markers):
            claim_like.append(text)
    if not claim_like:
        return [], 1.0
    uncited = [sentence for sentence in claim_like if not citation_pattern.search(sentence)]
    coverage = 1.0 - (len(uncited) / max(1, len(claim_like)))
    return uncited[:5], round(max(0.0, min(1.0, coverage)), 4)


def _verify_report_claims(
    report: str,
    search_runs: List[Dict[str, Any]],
    *,
    passages: Optional[List[Dict[str, Any]]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Any], List[Dict[str, Any]], Dict[str, int]]:
    config = config or {}
    from agent.workflows.claim_verifier import ClaimVerifier

    verifier = ClaimVerifier(
        min_overlap_tokens=_configurable_int(
            config,
            "deepsearch_claim_verifier_min_overlap_tokens",
            int(getattr(settings, "deepsearch_claim_verifier_min_overlap_tokens", 2) or 2),
        ),
        max_evidence_per_claim=_configurable_int(
            config,
            "deepsearch_claim_verifier_max_evidence_per_claim",
            int(getattr(settings, "deepsearch_claim_verifier_max_evidence_per_claim", 3) or 3),
        ),
    )
    use_passages = _configurable_bool(
        config,
        "deepsearch_claim_verifier_use_passages",
        bool(getattr(settings, "deepsearch_claim_verifier_use_passages", True)),
    )
    checks = verifier.verify_report(
        report,
        search_runs,
        passages=passages if use_passages and passages else None,
    )
    return checks, serialize_claim_checks(checks), summarize_claim_checks(checks)


def _revise_report_for_claim_failures(
    llm: ChatOpenAI,
    *,
    topic: str,
    report: str,
    claims: List[Dict[str, Any]],
    sources: str,
    config: Dict[str, Any],
) -> str:
    failing = [
        claim
        for claim in claims or []
        if isinstance(claim, dict) and claim.get("status") in {"unsupported", "contradicted"}
    ]
    if not failing:
        return report
    lines = []
    for claim in failing[:8]:
        lines.append(f"- status={claim.get('status')}; claim={claim.get('claim')}")
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "user",
                "请修订下面研究报告，目标是降低未支撑声明。"
                "\n主题：{topic}"
                "\n问题声明：\n{claims}"
                "\n可引用来源：\n{sources}"
                "\n原报告：\n{report}"
                "\n要求：保留 Markdown 结构；删除、弱化或标注资料不足的未支撑/矛盾声明；所有事实、数据、时间点、比较结论句末保留或补充已有编号引用；不要新增参考来源列表。",
            )
        ]
    )
    response = llm.invoke(
        prompt.format_messages(
            topic=topic,
            claims="\n".join(lines),
            sources=sources or "暂无",
            report=report,
        ),
        config=config,
    )
    revised = getattr(response, "content", "") or ""
    return revised.strip() or report


def _hydrate_with_crawler(results: List[Dict[str, Any]]) -> None:
    """Enrich results in-place with crawled content when Tavily lacks body text."""
    if not settings.deepsearch_enable_crawler or not results:
        return

    # Pick URLs that need content
    targets = []
    for r in results:
        body = r.get("raw_excerpt") or r.get("summary") or ""
        if len(body) < 200 and r.get("url"):
            targets.append(r["url"])
    if not targets:
        return

    crawled = {item["url"]: item for item in crawl_urls(targets)}
    for r in results:
        url = r.get("url")
        if not url or url not in crawled:
            continue
        content = crawled[url].get("content") or ""
        if content:
            r["raw_excerpt"] = content[:1200]
            if not r.get("summary"):
                r["summary"] = content[:400]


def _build_fetcher_evidence(
    urls: List[str],
    config: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    config = config or {}
    if not _configurable_bool(
        config,
        "deepsearch_enable_research_fetcher",
        bool(getattr(settings, "deepsearch_enable_research_fetcher", False)),
    ):
        return [], []

    def _looks_like_cookie_banner(text: str) -> bool:
        if not text:
            return False
        lowered = str(text).lower()
        if "cookie" not in lowered:
            return False
        return bool(
            "accept" in lowered
            or "consent" in lowered
            or "preferences" in lowered
            or "manage cookies" in lowered
            or "cookie settings" in lowered
            or "reject" in lowered
        )

    def _looks_like_interstitial(text: str) -> bool:
        if not text:
            return False
        lowered = str(text).lower()
        if "please enable javascript" in lowered:
            return True
        if "enable javascript" in lowered and ("cookies" in lowered or "continue" in lowered):
            return True
        if "checking your browser" in lowered:
            return True
        if "verify you are human" in lowered:
            return True
        if "just a moment" in lowered and "checking your browser" in lowered:
            return True
        return False

    def _passage_quality_score(passage: Dict[str, Any]) -> float:
        text = passage.get("text") or ""
        if not isinstance(text, str):
            text = str(text)
        stripped = text.strip()
        if not stripped:
            return -1e9
        if _looks_like_interstitial(stripped) or _looks_like_cookie_banner(stripped):
            return -1e9

        length = len(stripped)
        sentence_marks = sum(stripped.count(ch) for ch in (".", "?", "!", "。", "？", "！"))
        pipes = stripped.count("|")
        score = min(length, 800) / 800.0
        score += min(sentence_marks, 12) / 12.0
        if pipes >= 10:
            score -= 0.5
        return float(score)

    def _select_passages(passages: List[Dict[str, Any]], *, max_count: int) -> List[Dict[str, Any]]:
        if not passages:
            return []
        scored: List[tuple[float, Dict[str, Any]]] = [(_passage_quality_score(p), p) for p in passages]
        candidates = [(s, p) for s, p in scored if s > -1e8]
        if not candidates:
            return passages[:max(1, max_count)]
        candidates.sort(
            key=lambda pair: (
                -pair[0],
                int((pair[1].get("start_char") or 0) if isinstance(pair[1], dict) else 0),
            )
        )
        best = [p for _s, p in candidates[: max(1, max_count)]]
        best.sort(key=lambda p: int((p.get("start_char") or 0) if isinstance(p, dict) else 0))
        return best

    def _collapse_whitespace(text: str) -> str:
        return re.sub(r"\s+", " ", str(text or "")).strip()

    def _quote_for_passage(text: str, *, max_chars: int = 240) -> str:
        normalized = _collapse_whitespace(text)
        if not normalized:
            return ""
        return normalized[: max(1, int(max_chars))]

    def _snippet_hash_for_passage(text: str) -> str:
        normalized = _collapse_whitespace(text)
        if not normalized:
            return ""
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    fetcher = ContentFetcher()
    fetched_pages: List[Dict[str, Any]] = []
    passages: List[Dict[str, Any]] = []
    canonical_urls: List[str] = []
    seen: set = set()
    for url in urls or []:
        canonical_url = canonicalize_source_url(url)
        if not canonical_url or canonical_url in seen:
            continue
        seen.add(canonical_url)
        canonical_urls.append(canonical_url)

    for page in fetcher.fetch_many(canonical_urls):
        fetched_pages.append(page.to_dict())

        text = page.markdown or page.text or ""
        if not isinstance(text, str) or not text.strip():
            continue

        page_passages = split_into_passages(text, max_chars=800)
        for passage in _select_passages(page_passages, max_count=10):
            enriched = {"url": page.url, **passage}
            page_title = getattr(page, "title", None)
            if page_title:
                enriched["page_title"] = page_title
            retrieved_at = getattr(page, "retrieved_at", None)
            if retrieved_at:
                enriched["retrieved_at"] = retrieved_at
            method = getattr(page, "method", None)
            if method:
                enriched["method"] = method

            quote = _quote_for_passage(enriched.get("text") or "")
            if quote:
                enriched["quote"] = quote
            snippet_hash = _snippet_hash_for_passage(enriched.get("text") or "")
            if snippet_hash:
                enriched["snippet_hash"] = snippet_hash
            passages.append(enriched)

    return fetched_pages, passages


def _safe_filename(name: str) -> str:
    return re.sub(r'[\/\\:\*\?"<>\|]', "_", name)[:80]




def _build_quality_diagnostics(topic: str, queries: List[str], search_runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build query-coverage and freshness diagnostics for deepsearch runs."""
    query_coverage = analyze_query_coverage(queries)
    freshness_summary = summarize_freshness(search_runs)
    time_sensitive_query = is_time_sensitive_topic(topic)
    min_known_results = max(
        1, int(getattr(settings, "deepsearch_freshness_warning_min_known", 3) or 3)
    )
    min_fresh_ratio = max(
        0.0,
        min(1.0, float(getattr(settings, "deepsearch_freshness_warning_min_ratio", 0.4) or 0.4)),
    )

    freshness_warning = ""
    if (
        time_sensitive_query
        and freshness_summary.get("known_count", 0) >= min_known_results
        and freshness_summary.get("fresh_30_ratio", 0.0) < min_fresh_ratio
    ):
        freshness_warning = "low_freshness_for_time_sensitive_query"

    return {
        "query_coverage": query_coverage,
        "query_coverage_score": query_coverage.get("score", 0.0),
        "query_dimensions_covered": query_coverage.get("covered_dimensions", []),
        "query_dimensions_missing": query_coverage.get("missing_dimensions", []),
        "query_dimension_hits": query_coverage.get("dimension_hits", {}),
        "freshness_summary": freshness_summary,
        "time_sensitive_query": time_sensitive_query,
        "freshness_warning": freshness_warning,
    }


def _record_quality_gates(
    *,
    diagnostics: Dict[str, Any],
    quality_gate_history: List[Dict[str, Any]],
    emitter: Any,
    epoch: int,
    stage: str,
    quality_summary: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    gates = evaluate_quality_gates(
        diagnostics,
        quality_summary=quality_summary or {},
        epoch=epoch,
        policy=default_policy(settings),
    )
    serialized = serialize_gate_results(gates)
    record = {"epoch": epoch, "stage": stage, "gates": serialized}
    quality_gate_history.append(record)
    _emit_event(emitter, "quality_gate_evaluated", record)
    gaps = missing_topics_from_gates(gates)
    if gaps:
        _emit_event(
            emitter,
            "gap_detected",
            {"epoch": epoch, "stage": stage, "missing_topics": gaps},
        )
    return serialized


def _missing_topics_from_gate_payload(gates: List[Dict[str, Any]]) -> List[str]:
    topics: List[str] = []
    seen = set()
    for gate in gates or []:
        if not isinstance(gate, dict):
            continue
        if gate.get("status") != "fail" or gate.get("action") != "add_gap_queries":
            continue
        details = gate.get("details") if isinstance(gate.get("details"), dict) else {}
        missing = details.get("missing_dimensions")
        if not isinstance(missing, list):
            continue
        for item in missing:
            text = str(item or "").strip()
            if text and text not in seen:
                seen.add(text)
                topics.append(text)
    return topics


def _merge_evidence_item_payloads(*groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen = set()
    for group in groups:
        for item in group or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("id") or "").strip()
            if not key:
                key = "|".join(
                    [
                        str(item.get("source_type") or ""),
                        str(item.get("url") or ""),
                        str(item.get("document_id") or ""),
                        str(item.get("content_ref") or ""),
                        str(item.get("snippet") or "")[:120],
                    ]
                )
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


def _strategy_payload(
    state: Dict[str, Any],
    *,
    fallback_strategy: str,
    fallback_reason: str,
) -> Dict[str, Any]:
    decision = state.get("deepsearch_strategy_decision")
    if isinstance(decision, dict):
        payload = dict(decision)
    else:
        payload = {"strategy": fallback_strategy, "reason": fallback_reason}
    payload.setdefault("strategy", fallback_strategy)
    payload.setdefault("reason", fallback_reason)
    return payload


def _resolve_event_emitter(state: Dict[str, Any], config: Dict[str, Any]) -> Any:
    """Resolve thread-scoped emitter if available (best effort)."""
    cfg = config.get("configurable") if isinstance(config, dict) else {}
    thread_id = ""
    if isinstance(cfg, dict):
        thread_id = str(cfg.get("thread_id") or "").strip()
    if not thread_id:
        thread_id = str(state.get("cancel_token_id") or "").strip()
    if not thread_id:
        return None

    try:
        from agent.core.events import get_emitter_sync

        return get_emitter_sync(thread_id)
    except Exception:
        return None


def _emit_event(emitter: Any, event_type: str, data: Dict[str, Any]) -> None:
    """Emit an event from sync context without interrupting deepsearch flow."""
    if emitter is None:
        return
    try:
        emitter.emit_sync(event_type, data or {})
    except Exception as e:
        logger.debug(f"[deepsearch] failed to emit event '{event_type}': {e}")


def _compact_search_results(results: List[Dict[str, Any]], limit: int = 5) -> List[Dict[str, Any]]:
    return compact_unique_sources(results, limit=limit)


def _provider_breakdown(results: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in results or []:
        if not isinstance(item, dict):
            continue
        provider = str(item.get("provider") or "unknown").strip() or "unknown"
        counts[provider] = counts.get(provider, 0) + 1
    return counts


def _event_results_limit() -> int:
    return max(1, min(20, int(getattr(settings, "deepsearch_event_results_limit", 5) or 5)))


def _save_deepsearch_data(
    topic: str,
    have_query: List[str],
    summary_notes: List[str],
    search_runs: List[Dict[str, Any]],
    final_report: str,
    epoch: int,
) -> str:
    """Persist deepsearch run data if enabled."""
    if not settings.deepsearch_save_data:
        return ""

    try:
        save_dir = Path(settings.deepsearch_save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = f"{_safe_filename(topic)}_{ts}.json"
        path = save_dir / fname
        data = {
            "topic": topic,
            "queries": have_query,
            "summaries": summary_notes,
            "search_runs": search_runs,
            "final_report": final_report,
            "epoch": epoch,
            "mode": "deepsearch_optimized",
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"[deepsearch] saved run data -> {path}")
        return str(path)
    except Exception as e:
        logger.warning(f"[deepsearch] failed to save data: {e}")
        return ""


def run_deepsearch_optimized(state: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Optimized iterative deep-search pipeline.

    Improvements:
    1. URL deduplication to avoid repeated crawling
    2. Detailed performance logging for each step
    3. Enhanced error handling (single epoch failure doesn't break flow)
    4. Better cancellation support
    5. Maintains all_searched_urls and selected_urls
    """
    brief = build_research_brief(state, config)
    state["research_brief"] = brief.to_dict()
    topic = brief.clarified_goal or state.get("input", "")
    topic_for_planning = brief_topic(brief)
    _check_cancel(state)

    max_epochs = _configurable_int(
        config,
        "deepsearch_max_epochs",
        int(getattr(settings, "deepsearch_max_epochs", 3)),
    )
    query_num = _configurable_int(
        config,
        "deepsearch_query_num",
        int(getattr(settings, "deepsearch_query_num", 5)),
    )
    per_query_results = _configurable_int(
        config,
        "deepsearch_results_per_query",
        int(getattr(settings, "deepsearch_results_per_query", 5)),
    )
    top_urls = max(3, min(5, per_query_results))
    max_seconds = max(
        0.0,
        _configurable_float(
            config,
            "deepsearch_max_seconds",
            float(getattr(settings, "deepsearch_max_seconds", 0.0)),
        ),
    )
    max_tokens = max(
        0,
        _configurable_int(
            config,
            "deepsearch_max_tokens",
            int(getattr(settings, "deepsearch_max_tokens", 0)),
        ),
    )

    # Use multi-model routing for different task types
    planning_model = _model_for_task("planning", config)
    research_model = _model_for_task("research", config)
    writing_model = _model_for_task("writing", config)

    planner_llm = _chat_model(planning_model, temperature=0.8)
    critic_llm = _chat_model(research_model, temperature=0.2)
    writer_llm = _chat_model(writing_model, temperature=0.5)

    have_query: List[str] = []
    summary_notes: List[str] = []
    search_runs: List[Dict[str, Any]] = []
    provider_evidence_items: List[Dict[str, Any]] = []
    provider_profile = _resolve_provider_profile(state)
    evidence_providers = build_evidence_providers(
        brief=brief,
        config=config,
        search_func=_search_query,
        provider_profile=provider_profile,
    )

    # URL deduplication mechanism - use set for O(1) lookup
    all_searched_urls: List[str] = []  # Ordered list for logging
    all_searched_urls_set: set = set()  # Fast lookup
    selected_urls: List[str] = []  # Already crawled URLs
    selected_urls_set: set = set()  # Fast lookup
    fetched_pages: List[Dict[str, Any]] = []
    passages: List[Dict[str, Any]] = []

    logger.info(f"[deepsearch] topic='{topic}' epochs={max_epochs}")
    logger.info("[deepsearch] 开始优化版深度搜索")

    start_ts = time.time()
    tokens_used = _estimate_tokens_from_text(topic)
    budget_stop_reason = ""
    emitter = _resolve_event_emitter(state, config)
    visualize_browser = _browser_visualization_enabled(config)
    quality_gate_history: List[Dict[str, Any]] = []
    strategy_payload = _strategy_payload(
        state,
        fallback_strategy="linear",
        fallback_reason="explicit linear pipeline or auto fallback",
    )
    _emit_event(emitter, "brief_created", {"research_brief": brief.to_dict(), "mode": "linear"})
    _emit_event(
        emitter,
        "strategy_selected",
        {
            **strategy_payload,
            "research_brief": brief.to_dict(),
        },
    )

    try:
        for epoch in range(max_epochs):
            try:
                _check_cancel(state)
                epoch_start = time.time()
                budget_stop_reason = _budget_stop_reason(
                    start_ts=start_ts,
                    tokens_used=tokens_used,
                    max_seconds=max_seconds,
                    max_tokens=max_tokens,
                )
                if budget_stop_reason:
                    logger.info(f"[deepsearch] 预算触发提前停止: {budget_stop_reason}")
                    break
                logger.info(f"[deepsearch] ===== Epoch {epoch + 1}/{max_epochs} =====")
                epoch_node_id = f"deepsearch_epoch_{epoch + 1}"
                _emit_event(
                    emitter,
                    "research_node_start",
                    {
                        "node_id": epoch_node_id,
                        "topic": topic,
                        "depth": 1,
                        "parent_id": "deepsearch",
                        "epoch": epoch + 1,
                    },
                )

                # ⏱️ Step 1: 生成查询 (利用知识空白分析结果)
                query_start = time.time()
                missing_topics = state.get("missing_topics", []) if epoch > 0 else []
                queries = _generate_queries(
                    planner_llm, topic_for_planning, have_query, summary_notes, query_num, config,
                    missing_topics=missing_topics,
                )
                if epoch == 0 and query_num > 1 and topic not in queries:
                    queries.append(topic)
                if not queries:
                    queries = [topic]
                queries = queries[: max(1, query_num)]
                tokens_used += sum(_estimate_tokens_from_text(q) for q in queries)
                have_query.extend(q for q in queries if q not in have_query)
                logger.info(
                    f"[deepsearch] Epoch {epoch + 1}: 生成 {len(queries)} 个查询"
                    f" | 耗时 {time.time() - query_start:.2f}s"
                )
                logger.debug(f"[deepsearch] 查询列表: {queries}")

                # ⏱️ Step 2: 并行搜索
                search_start = time.time()
                combined_results: List[Dict[str, Any]] = []
                for q in queries:
                    _check_cancel(state)
                    budget_stop_reason = _budget_stop_reason(
                        start_ts=start_ts,
                        tokens_used=tokens_used,
                        max_seconds=max_seconds,
                        max_tokens=max_tokens,
                    )
                    if budget_stop_reason:
                        logger.info(f"[deepsearch] 搜索阶段触发预算停止: {budget_stop_reason}")
                        break

                    # While API search is running (blocking), render a small animated status page
                    # so the Live browser viewer isn't stuck on a blank about:blank.
                    if visualize_browser:
                        try:
                            from agent.workflows.browser_visualizer import show_browser_status_page

                            show_browser_status_page(
                                state=state,
                                config=config,
                                title="Searching the web…",
                                detail=q,
                            )
                        except Exception:
                            pass

                    provider_outputs = search_with_evidence_providers(
                        providers=evidence_providers,
                        query=q,
                        max_results=per_query_results,
                        config=config,
                    )
                    results = merge_provider_results(provider_outputs)
                    provider_evidence_items.extend(merge_provider_evidence(provider_outputs))
                    tokens_used += _estimate_tokens_from_results(results)
                    combined_results.extend(results)
                    search_runs.append(
                        {
                            "query": q,
                            "results": results,
                            "timestamp": datetime.now().isoformat(),
                        }
                    )
                    provider_breakdown = _provider_breakdown(results)
                    provider_name = "unknown"
                    if len(provider_breakdown) > 1:
                        provider_name = "multi"
                    elif len(provider_breakdown) == 1:
                        provider_name = next(iter(provider_breakdown))
                    _emit_event(
                        emitter,
                        "search",
                        {
                            "query": q,
                            "provider": provider_name,
                            "provider_breakdown": provider_breakdown,
                            "results": _compact_search_results(results, limit=_event_results_limit()),
                            "count": len(results),
                            "epoch": epoch + 1,
                        },
                    )

                    # Keep the sandbox browser Live view "alive" by previewing a top result.
                    # This is best-effort UX only; failures must not break research.
                    if visualize_browser:
                        try:
                            from agent.workflows.browser_visualizer import visualize_urls_from_results

                            visualize_urls_from_results(
                                state=state,
                                config=config,
                                results=results if isinstance(results, list) else [],
                                max_urls=1,
                                reason=f"deepsearch:search:epoch{epoch + 1}",
                            )
                        except Exception:
                            pass

                    # Record all searched URLs (dedupe with O(1) set lookup)
                    for r in results:
                        url = canonicalize_source_url(r.get("url"))
                        if url and url not in all_searched_urls_set:
                            all_searched_urls.append(url)
                            all_searched_urls_set.add(url)

                if budget_stop_reason:
                    break

                logger.info(
                    f"[deepsearch] Epoch {epoch + 1}: 搜索到 {len(combined_results)} 个结果"
                    f" | 累计 URL: {len(all_searched_urls)}"
                    f" | 耗时 {time.time() - search_start:.2f}s"
                )

                if not combined_results:
                    logger.info(f"[deepsearch] Epoch {epoch + 1}: 无搜索结果，跳过本轮")
                    epoch_diagnostics = _build_quality_diagnostics(topic, have_query, search_runs)
                    gate_results = _record_quality_gates(
                        diagnostics=epoch_diagnostics,
                        quality_gate_history=quality_gate_history,
                        emitter=emitter,
                        epoch=epoch + 1,
                        stage="epoch",
                    )
                    gate_missing_topics = _missing_topics_from_gate_payload(gate_results)
                    if gate_missing_topics:
                        state["missing_topics"] = gate_missing_topics
                    _emit_event(
                        emitter,
                        "quality_update",
                        {
                            "epoch": epoch + 1,
                            "stage": "epoch",
                            **epoch_diagnostics,
                        },
                    )
                    _emit_event(
                        emitter,
                        "research_node_complete",
                        {
                            "node_id": epoch_node_id,
                            "summary": "",
                            "sources": [],
                            "quality": epoch_diagnostics,
                            "epoch": epoch + 1,
                        },
                    )
                    continue

                # Step 3: Pick most relevant URLs (excluding already selected)
                pick_start = time.time()
                chosen_urls = _pick_relevant_urls(
                    critic_llm,
                    topic,
                    summary_notes,
                    combined_results,
                    top_urls,
                    config,
                    selected_urls_set,  # Pass set for O(1) lookup
                )
                normalized_chosen_urls: List[str] = []
                normalized_chosen_set = set()
                for url in chosen_urls:
                    canonical_url = canonicalize_source_url(url)
                    if (
                        not canonical_url
                        or canonical_url in normalized_chosen_set
                        or canonical_url in selected_urls_set
                    ):
                        continue
                    normalized_chosen_urls.append(canonical_url)
                    normalized_chosen_set.add(canonical_url)
                chosen_urls = normalized_chosen_urls

                non_url_results = [
                    r
                    for r in combined_results
                    if isinstance(r, dict)
                    and not canonicalize_source_url(r.get("url"))
                    and str(r.get("source_type") or r.get("provider") or "").lower() in {"rag", "local"}
                ]

                if not chosen_urls and not non_url_results:
                    logger.warning(
                        f"[deepsearch] Epoch {epoch + 1}: No new URLs available, skipping"
                    )
                    epoch_diagnostics = _build_quality_diagnostics(topic, have_query, search_runs)
                    gate_results = _record_quality_gates(
                        diagnostics=epoch_diagnostics,
                        quality_gate_history=quality_gate_history,
                        emitter=emitter,
                        epoch=epoch + 1,
                        stage="epoch",
                    )
                    gate_missing_topics = _missing_topics_from_gate_payload(gate_results)
                    if gate_missing_topics:
                        state["missing_topics"] = gate_missing_topics
                    _emit_event(
                        emitter,
                        "quality_update",
                        {
                            "epoch": epoch + 1,
                            "stage": "epoch",
                            **epoch_diagnostics,
                        },
                    )
                    _emit_event(
                        emitter,
                        "research_node_complete",
                        {
                            "node_id": epoch_node_id,
                            "summary": "",
                            "sources": [],
                            "quality": epoch_diagnostics,
                            "epoch": epoch + 1,
                        },
                    )
                    continue

                # Update selected URLs list and set
                selected_urls.extend(chosen_urls)
                selected_urls_set.update(chosen_urls)

                # Preview chosen URLs in the sandbox browser (helps Live view match "selected sources").
                if visualize_browser:
                    try:
                        from agent.workflows.browser_visualizer import visualize_urls

                        visualize_urls(
                            state=state,
                            config=config,
                            urls=chosen_urls,
                            max_urls=min(3, len(chosen_urls)),
                            reason=f"deepsearch:selected:epoch{epoch + 1}",
                        )
                    except Exception:
                        pass

                new_pages, new_passages = _build_fetcher_evidence(chosen_urls, config)
                fetched_pages.extend(new_pages)
                passages.extend(new_passages)

                chosen_urls_set = set(chosen_urls)
                chosen_results = [
                    r
                    for r in combined_results
                    if canonicalize_source_url(r.get("url")) in chosen_urls_set
                ]
                if non_url_results:
                    chosen_results.extend(non_url_results[:top_urls])
                if not chosen_results:
                    chosen_results = sorted(
                        combined_results, key=lambda r: r.get("score", 0), reverse=True
                    )[:top_urls]

                logger.info(
                    f"[deepsearch] Epoch {epoch + 1}: 选择 {len(chosen_urls)} 个 URL"
                    f" | 已选总数: {len(selected_urls)}"
                    f" | 耗时 {time.time() - pick_start:.2f}s"
                )

                # ⏱️ Step 4: 爬虫补充内容（可选）
                if settings.deepsearch_enable_crawler:
                    crawl_start = time.time()
                    _hydrate_with_crawler(chosen_results)
                    logger.info(
                        f"[deepsearch] Epoch {epoch + 1}: 爬虫增强完成"
                        f" | 耗时 {time.time() - crawl_start:.2f}s"
                    )

                # ⏱️ Step 5: 摘要新知识 + 判断是否足够
                summary_start = time.time()
                enough, summary_text = _summarize_new_knowledge(
                    critic_llm, topic, summary_notes, chosen_results, config
                )
                if summary_text:
                    summary_notes.append(summary_text)
                    tokens_used += _estimate_tokens_from_text(summary_text)

                logger.info(
                    f"[deepsearch] Epoch {epoch + 1}: 摘要完成"
                    f" | 足够: {enough}"
                    f" | 摘要长度: {len(summary_text)}"
                    f" | 耗时 {time.time() - summary_start:.2f}s"
                )
                budget_stop_reason = _budget_stop_reason(
                    start_ts=start_ts,
                    tokens_used=tokens_used,
                    max_seconds=max_seconds,
                    max_tokens=max_tokens,
                )
                if budget_stop_reason:
                    logger.info(f"[deepsearch] 摘要后触发预算停止: {budget_stop_reason}")
                    break

                # ⏱️ Step 5.5: 知识空白分析 (可选)
                use_gap_analysis = getattr(settings, "deepsearch_use_gap_analysis", True)
                if use_gap_analysis and not enough and epoch < max_epochs - 1:
                    gap_start = time.time()
                    try:
                        gap_model = _model_for_task("gap_analysis", config)
                        gap_llm = _chat_model(gap_model, temperature=0.3)
                        gap_analyzer = KnowledgeGapAnalyzer(gap_llm, config, coverage_threshold=0.8)

                        # Analyze current knowledge state
                        collected_knowledge = "\n\n".join(summary_notes)
                        gap_result = gap_analyzer.analyze(topic, have_query, collected_knowledge)

                        logger.info(
                            f"[deepsearch] Epoch {epoch + 1}: 知识空白分析完成"
                            f" | 覆盖率: {gap_result.overall_coverage:.2f}"
                            f" | 空白数: {len(gap_result.gaps)}"
                            f" | 耗时 {time.time() - gap_start:.2f}s"
                        )

                        # Use gap analysis to determine if we can stop early
                        if gap_analyzer.is_research_sufficient(gap_result):
                            logger.info(f"[deepsearch] Epoch {epoch + 1}: 知识空白分析判定信息足够")
                            enough = True

                        # Get high-priority aspects for next round's query generation
                        high_priority_aspects = gap_analyzer.get_high_priority_aspects(gap_result)
                        if high_priority_aspects:
                            logger.info(
                                f"[deepsearch] 高优先级空白: {', '.join(high_priority_aspects[:3])}"
                            )
                            # Store for use in next epoch's query generation
                            state["missing_topics"] = high_priority_aspects

                    except Exception as e:
                        logger.warning(f"[deepsearch] 知识空白分析失败，继续常规流程: {e}")

                epoch_duration = time.time() - epoch_start
                logger.info(f"[deepsearch] Epoch {epoch + 1}: 总耗时 {epoch_duration:.2f}s")
                epoch_diagnostics = _build_quality_diagnostics(topic, have_query, search_runs)
                gate_results = _record_quality_gates(
                    diagnostics=epoch_diagnostics,
                    quality_gate_history=quality_gate_history,
                    emitter=emitter,
                    epoch=epoch + 1,
                    stage="epoch",
                )
                gate_missing_topics = _missing_topics_from_gate_payload(gate_results)
                if gate_missing_topics and not enough:
                    state["missing_topics"] = gate_missing_topics
                _emit_event(
                    emitter,
                    "quality_update",
                    {
                        "epoch": epoch + 1,
                        "stage": "epoch",
                        **epoch_diagnostics,
                    },
                )
                _emit_event(
                    emitter,
                    "research_node_complete",
                    {
                        "node_id": epoch_node_id,
                        "summary": summary_text[:1200] if isinstance(summary_text, str) else "",
                        "sources": _compact_search_results(
                            chosen_results,
                            limit=_event_results_limit(),
                        ),
                        "quality": epoch_diagnostics,
                        "epoch": epoch + 1,
                    },
                )

                # 如果信息足够，提前结束
                if enough:
                    logger.info(f"[deepsearch] Epoch {epoch + 1}: 信息已足够，提前结束")
                    break

            except asyncio.CancelledError:
                raise  # 继续向上抛出
            except Exception as e:
                logger.error(f"[deepsearch] Epoch {epoch + 1} 失败: {str(e)}", exc_info=True)
                logger.error(traceback.format_exc())
                logger.info("[deepsearch] 继续下一轮搜索...")
                continue  # 单轮失败不影响整体流程

        # Prefer sources we actually summarized when assigning citation numbers.
        # This helps the report and the frontend agree on `[n]` semantics.
        citation_runs = _reorder_search_runs_for_citations(
            search_runs,
            preferred_urls=selected_urls,
        )

        report_sources_limit = int(
            getattr(settings, "deepsearch_report_sources_limit", 20) or 20
        )
        all_sources: List[Dict[str, Any]] = []
        try:
            from agent.workflows.evidence_extractor import extract_message_sources

            all_sources = extract_message_sources(citation_runs)
        except Exception:
            all_sources = []
        report_sources = all_sources[: max(1, report_sources_limit)]
        sources_block = _format_sources_for_writer(
            report_sources,
            citation_runs,
            limit=report_sources_limit,
        )

        # ⏱️ Step 6: 生成最终报告（带强引用来源编号）
        report_start = time.time()
        final_report = (
            _final_report(writer_llm, topic, summary_notes, config, sources=sources_block)
            if summary_notes
            else summary_text_prompt
        )
        final_report = _append_auto_references(
            final_report,
            report_sources,
            limit=report_sources_limit,
        )
        _emit_event(
            emitter,
            "report_written",
            {
                "mode": "linear",
                "length": len(final_report),
                "source_count": len(report_sources),
            },
        )
        logger.info(
            f"[deepsearch] 最终报告生成完成"
            f" | 字数: {len(final_report)}"
            f" | 耗时 {time.time() - report_start:.2f}s"
        )

        elapsed = time.time() - start_ts
        logger.info(
            f"[deepsearch] ===== 完成 ====="
            f"\n  总耗时: {elapsed:.2f}s"
            f"\n  总轮次: {epoch + 1}"
            f"\n  总查询: {len(have_query)}"
            f"\n  总 URL: {len(all_searched_urls)}"
            f"\n  已爬取: {len(selected_urls)}"
            f"\n  摘要数: {len(summary_notes)}"
            f"\n  估算Token: {tokens_used}"
            f"\n  预算停止原因: {budget_stop_reason or 'none'}"
        )

        diagnostics = _build_quality_diagnostics(topic, have_query, citation_runs)
        quality_summary = {
            "epochs_completed": epoch + 1,
            "summary_count": len(summary_notes),
            "source_count": len(all_searched_urls),
            "selected_url_count": len(selected_urls),
            "budget_stop_reason": budget_stop_reason or "",
            "tokens_used": tokens_used,
            "elapsed_seconds": elapsed,
            **diagnostics,
        }
        claims = []
        try:
            from agent.workflows.claim_verifier import ClaimVerifier

            min_overlap = int(
                getattr(settings, "deepsearch_claim_verifier_min_overlap_tokens", 2) or 2
            )
            max_evidence = int(
                getattr(settings, "deepsearch_claim_verifier_max_evidence_per_claim", 3) or 3
            )
            use_passages = bool(
                getattr(settings, "deepsearch_claim_verifier_use_passages", True)
            )

            verifier = ClaimVerifier(
                min_overlap_tokens=min_overlap,
                max_evidence_per_claim=max_evidence,
            )
            checks = verifier.verify_report(
                final_report,
                citation_runs,
                passages=passages if use_passages else None,
            )
            claims = [
                {
                    "claim": c.claim,
                    "status": c.status.value,
                    "evidence_urls": c.evidence_urls,
                    "evidence_passages": c.evidence_passages,
                    "score": c.score,
                    "notes": c.notes,
                }
                for c in checks
            ]
        except Exception:
            claims = []

        # ---- Patch quality_summary with citation_coverage & claim_verifier stats ----
        # _build_run_evidence_summary reads these from quality_summary; without them
        # the evidence_summary endpoint returns None for these metrics.
        try:
            import re as _re
            _claim_like = []
            for _sent in _re.split(r"(?<=[。！？.!?])\s+", final_report):
                _t = _sent.strip()
                if len(_t) < 15:
                    continue
                _markers = [
                    r"\d{4}", r"\d+%", r"\d+\.\d+",
                    r"(?:research|study|report|data|according to|shows|found)",
                    r"(?:研究|数据显示|统计|报告|发现|增长|下降)",
                ]
                if any(_re.search(m, _t, flags=_re.IGNORECASE) for m in _markers):
                    _claim_like.append(_t)
            if _claim_like:
                _cite_pat = _re.compile(
                    r"\[(?:S\d+-\d+|\d+)\]|\[来源[：:].*?\]|https?://\S+", _re.IGNORECASE
                )
                _uncited = [s for s in _claim_like if not _cite_pat.search(s)]
                _cov = 1.0 - (len(_uncited) / max(1, len(_claim_like)))
                quality_summary["citation_coverage"] = max(0.0, min(1.0, _cov))
            else:
                quality_summary["citation_coverage"] = 1.0
        except Exception:
            pass

        _cv_total = len(claims)
        _cv_verified = sum(1 for c in claims if isinstance(c, dict) and c.get("status") == "verified")
        _cv_unsupported = sum(1 for c in claims if isinstance(c, dict) and c.get("status") == "unsupported")
        _cv_contradicted = sum(1 for c in claims if isinstance(c, dict) and c.get("status") == "contradicted")
        quality_summary["claim_verifier_total"] = _cv_total
        quality_summary["claim_verifier_verified"] = _cv_verified
        quality_summary["claim_verifier_unsupported"] = _cv_unsupported
        quality_summary["claim_verifier_contradicted"] = _cv_contradicted
        _record_quality_gates(
            diagnostics=diagnostics,
            quality_summary=quality_summary,
            quality_gate_history=quality_gate_history,
            emitter=emitter,
            epoch=epoch + 1,
            stage="final",
        )
        evidence_items = _merge_evidence_item_payloads(
            provider_evidence_items,
            build_evidence_items(
                search_runs=citation_runs,
                sources=all_sources,
                fetched_pages=fetched_pages,
                passages=passages,
            ),
        )
        citation_annotations = build_citation_annotations(
            report=final_report,
            sources=all_sources,
            evidence_items=evidence_items,
        )
        timeline = build_timeline_artifacts(
            search_runs=citation_runs,
            sources=all_sources,
            evidence_items=evidence_items,
            quality_gates=quality_gate_history,
        )
        _emit_event(
            emitter,
            "evidence_selected",
            {"mode": "linear", "count": len(evidence_items)},
        )

        deepsearch_artifacts = {
            "mode": "linear",
            "research_brief": brief.to_dict(),
            "strategy_decision": strategy_payload,
            "queries": have_query,
            "research_tree": None,
            "quality_summary": quality_summary,
            "quality_gates": quality_gate_history,
            "query_coverage": diagnostics.get("query_coverage", {}),
            "freshness_summary": diagnostics.get("freshness_summary", {}),
            "evidence_items": evidence_items,
            "citation_annotations": citation_annotations,
            "timeline": timeline,
            "fetched_pages": fetched_pages,
            "passages": passages,
            "sources": all_sources,
            "claims": claims,
        }

        # 保存数据
        save_path = _save_deepsearch_data(
            topic,
            have_query,
            summary_notes,
            citation_runs,
            final_report,
            epoch=epoch + 1,
        )

        messages = [AIMessage(content=final_report)]
        if save_path:
            messages.append(AIMessage(content=f"(数据已保存: {save_path})"))
        if budget_stop_reason:
            messages.append(
                AIMessage(
                    content=(
                        "（由于预算限制提前收敛："
                        f"{budget_stop_reason}; tokens={tokens_used}; elapsed={elapsed:.2f}s）"
                    )
                )
            )
        if diagnostics.get("freshness_warning"):
            messages.append(
                AIMessage(
                    content="（时间敏感问题的新鲜来源占比较低，建议补充近30天来源并重试。）"
                )
            )

        return {
            "research_plan": have_query,
            "scraped_content": citation_runs,
            "draft_report": final_report,
            "final_report": final_report,
            "quality_summary": quality_summary,
            "sources": all_sources,
            "deepsearch_artifacts": deepsearch_artifacts,
            "deepsearch_mode": "linear",
            "messages": messages,
            "is_complete": False,
            "budget_stop_reason": budget_stop_reason,
            "deepsearch_tokens_used": tokens_used,
            "deepsearch_elapsed_seconds": elapsed,
        }

    except asyncio.CancelledError:
        logger.warning("[deepsearch] 收到取消信号，停止任务")
        return {
            "is_cancelled": True,
            "is_complete": True,
            "errors": ["DeepSearch was cancelled"],
            "final_report": "任务已被取消",
        }


def run_deepsearch_tree(state: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Tree-based deep search pipeline.

    Uses hierarchical topic decomposition and parallel branch exploration
    for more comprehensive research coverage.

    Inspired by GPT Researcher's tree exploration approach.
    """
    brief = build_research_brief(state, config)
    state["research_brief"] = brief.to_dict()
    topic = brief.clarified_goal or state.get("input", "")
    _check_cancel(state)

    # Use multi-model routing for different task types
    planning_model = _model_for_task("planning", config)
    research_model = _model_for_task("research", config)
    writing_model = _model_for_task("writing", config)

    planner_llm = _chat_model(planning_model, temperature=0.8)
    critic_llm = _chat_model(research_model, temperature=0.2)
    writer_llm = _chat_model(writing_model, temperature=0.5)

    max_depth = int(getattr(settings, "tree_max_depth", 2))
    max_branches = int(getattr(settings, "tree_max_branches", 4))
    queries_per_branch = int(getattr(settings, "tree_queries_per_branch", 3))
    per_query_results = int(getattr(settings, "deepsearch_results_per_query", 5))
    parallel_branches = int(getattr(settings, "tree_parallel_branches", 3))
    max_seconds = max(
        0.0,
        _configurable_float(
            config,
            "deepsearch_max_seconds",
            float(getattr(settings, "deepsearch_max_seconds", 0.0)),
        ),
    )
    max_tokens = max(
        0,
        _configurable_int(
            config,
            "deepsearch_max_tokens",
            int(getattr(settings, "deepsearch_max_tokens", 0)),
        ),
    )
    max_searches = max(
        0,
        _configurable_int(
            config,
            "deepsearch_tree_max_searches",
            int(getattr(settings, "deepsearch_tree_max_searches", 30) or 30),
        ),
    )

    logger.info(
        f"[deepsearch-tree] Starting tree exploration: topic='{topic}' "
        f"depth={max_depth} branches={max_branches} parallel={parallel_branches}"
    )
    provider_profile = _resolve_provider_profile(state)
    emitter = _resolve_event_emitter(state, config)
    search_runs: List[Dict[str, Any]] = []
    live_search_events_emitted = 0
    quality_gate_history: List[Dict[str, Any]] = []
    strategy_payload = _strategy_payload(
        state,
        fallback_strategy="tree",
        fallback_reason="explicit tree pipeline or auto broad research selection",
    )
    _emit_event(emitter, "brief_created", {"research_brief": brief.to_dict(), "mode": "tree"})
    _emit_event(
        emitter,
        "strategy_selected",
        {
            **strategy_payload,
            "research_brief": brief.to_dict(),
        },
    )
    _emit_event(
        emitter,
        "research_node_start",
        {
            "node_id": "deepsearch_tree",
            "topic": topic,
            "depth": 0,
            "parent_id": "deepsearch",
        },
    )

    start_ts = time.time()
    budget_stop_reason = ""
    tokens_used = _estimate_tokens_from_text(topic)
    searches_used = 0

    budget_stop_reason = _budget_stop_reason(
        start_ts=start_ts,
        tokens_used=tokens_used,
        max_seconds=max_seconds,
        max_tokens=max_tokens,
    )
    if budget_stop_reason:
        diagnostics = _build_quality_diagnostics(topic, [], [])
        quality_summary = {
            "epochs_completed": 0,
            "summary_count": 0,
            "source_count": 0,
            "budget_stop_reason": budget_stop_reason,
            "tokens_used": tokens_used,
            "elapsed_seconds": 0.0,
            **diagnostics,
        }
        _emit_event(emitter, "quality_update", {"epoch": 0, "stage": "budget_stop", **diagnostics})
        _emit_event(
            emitter,
            "research_node_complete",
            {
                "node_id": "deepsearch_tree",
                "summary": "",
                "sources": [],
                "quality": diagnostics,
                "epoch": 0,
            },
        )
        return {
            "research_plan": [],
            "scraped_content": [],
            "draft_report": summary_text_prompt,
            "final_report": summary_text_prompt,
            "messages": [
                AIMessage(content=f"（预算限制触发，未执行树搜索：{budget_stop_reason}）")
            ],
            "is_complete": False,
            "budget_stop_reason": budget_stop_reason,
            "deepsearch_tokens_used": tokens_used,
            "deepsearch_elapsed_seconds": 0.0,
            "quality_summary": quality_summary,
            "deepsearch_artifacts": {
                "mode": "tree",
                "queries": [],
                "research_tree": None,
                "quality_summary": quality_summary,
                "query_coverage": diagnostics.get("query_coverage", {}),
                "freshness_summary": diagnostics.get("freshness_summary", {}),
            },
            "deepsearch_mode": "tree",
        }

    try:
        def _tree_budget_reason() -> Optional[str]:
            nonlocal budget_stop_reason
            if budget_stop_reason:
                return budget_stop_reason
            reason = _budget_stop_reason(
                start_ts=start_ts,
                tokens_used=tokens_used,
                max_seconds=max_seconds,
                max_tokens=max_tokens,
            )
            if reason:
                return reason
            if max_searches > 0 and searches_used >= max_searches:
                return "search_budget_exceeded"
            return None

        def _tree_search(payload, config_payload=None, **kwargs):
            nonlocal live_search_events_emitted, budget_stop_reason, tokens_used, searches_used
            stop_reason = _tree_budget_reason()
            if stop_reason:
                budget_stop_reason = stop_reason
                raise TreeExplorationBudgetExceeded(stop_reason)
            query = (payload or {}).get("query", "")
            max_results = int((payload or {}).get("max_results", per_query_results))
            effective_config = (
                kwargs.get("config")
                if isinstance(kwargs.get("config"), dict)
                else config_payload
                if isinstance(config_payload, dict)
                else config
            )
            tokens_used += _estimate_tokens_from_text(query)
            results = _search_query(
                query,
                max_results,
                effective_config,
                provider_profile=provider_profile,
            )
            searches_used += 1
            if isinstance(results, list):
                tokens_used += _estimate_tokens_from_results(results)
            search_runs.append(
                {
                    "query": query,
                    "results": results if isinstance(results, list) else [],
                    "timestamp": datetime.now().isoformat(),
                }
            )
            provider_breakdown = _provider_breakdown(results if isinstance(results, list) else [])
            provider_name = "unknown"
            if len(provider_breakdown) > 1:
                provider_name = "multi"
            elif len(provider_breakdown) == 1:
                provider_name = next(iter(provider_breakdown))
            _emit_event(
                emitter,
                "search",
                {
                    "query": query,
                    "provider": provider_name,
                    "provider_breakdown": provider_breakdown,
                    "results": _compact_search_results(
                        results if isinstance(results, list) else [],
                        limit=_event_results_limit(),
                    ),
                    "count": len(results) if isinstance(results, list) else 0,
                    "mode": "tree",
                    "epoch": 1,
                },
            )
            live_search_events_emitted += 1
            post_budget_reason = _budget_stop_reason(
                start_ts=start_ts,
                tokens_used=tokens_used,
                max_seconds=max_seconds,
                max_tokens=max_tokens,
            )
            if post_budget_reason:
                budget_stop_reason = post_budget_reason
            return results

        # Create tree explorer
        explorer = TreeExplorer(
            planner_llm=planner_llm,
            researcher_llm=critic_llm,
            writer_llm=writer_llm,
            search_func=_tree_search,
            config=config,
            max_depth=max_depth,
            max_branches=max_branches,
            queries_per_branch=queries_per_branch,
        )

        # Run tree exploration (use async if parallel_branches > 0)
        tree = None
        if parallel_branches > 0:
            # Use async parallel exploration
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # If already in async context, use run_in_executor
                    import concurrent.futures

                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(
                            lambda: asyncio.run(explorer.run_async(topic, state, decompose_root=True))
                        )
                        tree = future.result()
                else:
                    tree = loop.run_until_complete(explorer.run_async(topic, state, decompose_root=True))
                logger.info("[deepsearch-tree] Used async parallel exploration")
            except TreeExplorationBudgetExceeded as e:
                budget_stop_reason = budget_stop_reason or getattr(e, "reason", "") or str(e)
                tree = getattr(explorer, "tree", None)
            except RuntimeError:
                # No event loop, create one
                try:
                    tree = asyncio.run(explorer.run_async(topic, state, decompose_root=True))
                    logger.info("[deepsearch-tree] Used async parallel exploration")
                except TreeExplorationBudgetExceeded as e:
                    budget_stop_reason = budget_stop_reason or getattr(e, "reason", "") or str(e)
                    tree = getattr(explorer, "tree", None)
        else:
            try:
                tree = explorer.run(topic, state, decompose_root=True)
            except TreeExplorationBudgetExceeded as e:
                budget_stop_reason = budget_stop_reason or getattr(e, "reason", "") or str(e)
                tree = getattr(explorer, "tree", None)

        if tree is None:
            tree = getattr(explorer, "tree", None)

        # Get merged summary from all branches
        merged_summary = explorer.get_final_summary()
        if not merged_summary and search_runs:
            # Budget stops can interrupt branch completion; fall back to a cheap synthesis so
            # we still generate a useful final report from partial search results.
            try:
                flat_results: List[Dict[str, Any]] = []
                for run in search_runs[-min(10, len(search_runs)) :]:
                    if not isinstance(run, dict):
                        continue
                    results = run.get("results")
                    if isinstance(results, list):
                        for r in results:
                            if isinstance(r, dict):
                                flat_results.append(r)
                            if len(flat_results) >= 10:
                                break
                    if len(flat_results) >= 10:
                        break
                merged_summary = _format_results(flat_results) if flat_results else ""
            except Exception:
                merged_summary = ""
        raw_sources = explorer.get_all_sources()
        all_sources: List[str] = []
        all_sources_set = set()
        for source in raw_sources:
            canonical_source = canonicalize_source_url(source)
            if canonical_source and canonical_source not in all_sources_set:
                all_sources.append(canonical_source)
                all_sources_set.add(canonical_source)
        all_findings = explorer.get_all_findings()

        summary_notes = [merged_summary] if merged_summary else []
        # Collect queries + search runs from all nodes
        have_query: List[str] = []
        for node in tree.nodes.values():
            have_query.extend(node.queries)
        if not search_runs:
            for node in tree.nodes.values():
                for finding in node.findings:
                    search_runs.append({
                        "query": finding.get("query", ""),
                        "results": [finding.get("result", {})],
                        "timestamp": finding.get("timestamp", ""),
                        "branch_id": node.id,
                        "branch_topic": node.topic,
                    })

        report_sources_limit = int(
            getattr(settings, "deepsearch_report_sources_limit", 20) or 20
        )
        extracted_sources: List[Dict[str, Any]] = []
        try:
            from agent.workflows.evidence_extractor import extract_message_sources

            extracted_sources = extract_message_sources(search_runs)
        except Exception:
            extracted_sources = []
        report_sources = extracted_sources[: max(1, report_sources_limit)]
        sources_block = _format_sources_for_writer(
            report_sources,
            search_runs,
            limit=report_sources_limit,
        )

        # Generate final report (strong citations aligned to extracted_sources order)
        final_report = (
            _final_report(writer_llm, topic, summary_notes, config, sources=sources_block)
            if summary_notes
            else summary_text_prompt
        )
        final_report = _append_auto_references(
            final_report,
            report_sources,
            limit=report_sources_limit,
        )
        _emit_event(
            emitter,
            "report_written",
            {
                "mode": "tree",
                "length": len(final_report),
                "source_count": len(report_sources),
            },
        )

        elapsed = time.time() - start_ts
        tokens_used += _estimate_tokens_from_text(merged_summary)
        tokens_used += _estimate_tokens_from_text(final_report)
        post_budget_reason = _budget_stop_reason(
            start_ts=start_ts,
            tokens_used=tokens_used,
            max_seconds=max_seconds,
            max_tokens=max_tokens,
        )
        if post_budget_reason:
            budget_stop_reason = post_budget_reason

        logger.info(
            f"[deepsearch-tree] ===== Completed =====\n"
            f"  Total time: {elapsed:.2f}s\n"
            f"  Tree nodes: {len(tree.nodes)}\n"
            f"  Total sources: {len(all_sources)}\n"
            f"  Report length: {len(final_report)} chars"
        )

        # Save data
        save_path = _save_deepsearch_data(
            topic, have_query, summary_notes, search_runs, final_report, epoch=1,
        )
        diagnostics = _build_quality_diagnostics(topic, have_query, search_runs)
        if live_search_events_emitted == 0:
            for run in search_runs:
                results = run.get("results") if isinstance(run, dict) else []
                provider_breakdown = _provider_breakdown(results if isinstance(results, list) else [])
                provider_name = "unknown"
                if len(provider_breakdown) > 1:
                    provider_name = "multi"
                elif len(provider_breakdown) == 1:
                    provider_name = next(iter(provider_breakdown))
                _emit_event(
                    emitter,
                    "search",
                    {
                        "query": run.get("query", "") if isinstance(run, dict) else "",
                        "provider": provider_name,
                        "provider_breakdown": provider_breakdown,
                        "results": _compact_search_results(
                            results if isinstance(results, list) else [],
                            limit=_event_results_limit(),
                        ),
                        "count": len(results) if isinstance(results, list) else 0,
                        "mode": "tree",
                        "epoch": 1,
                    },
                )

        quality_summary = {
            "epochs_completed": 1,
            "summary_count": len(summary_notes),
            "source_count": len(all_sources),
            "tree_node_count": len(tree.nodes),
            "budget_stop_reason": budget_stop_reason or "",
            "tokens_used": tokens_used,
            "elapsed_seconds": elapsed,
            **diagnostics,
        }
        fetched_pages, passages = _build_fetcher_evidence(all_sources[:10], config)

        claims = []
        try:
            from agent.workflows.claim_verifier import ClaimVerifier

            min_overlap = int(
                getattr(settings, "deepsearch_claim_verifier_min_overlap_tokens", 2) or 2
            )
            max_evidence = int(
                getattr(settings, "deepsearch_claim_verifier_max_evidence_per_claim", 3) or 3
            )
            use_passages = bool(
                getattr(settings, "deepsearch_claim_verifier_use_passages", True)
            )

            verifier = ClaimVerifier(
                min_overlap_tokens=min_overlap,
                max_evidence_per_claim=max_evidence,
            )
            checks = verifier.verify_report(
                final_report,
                search_runs,
                passages=passages if use_passages else None,
            )
            claims = [
                {
                    "claim": c.claim,
                    "status": c.status.value,
                    "evidence_urls": c.evidence_urls,
                    "evidence_passages": c.evidence_passages,
                    "score": c.score,
                    "notes": c.notes,
                }
                for c in checks
            ]
        except Exception:
            claims = []

        # ---- Patch quality_summary with citation_coverage & claim_verifier stats ----
        try:
            import re as _re
            _claim_like = []
            for _sent in _re.split(r"(?<=[。！？.!?])\s+", final_report):
                _t = _sent.strip()
                if len(_t) < 15:
                    continue
                _markers = [
                    r"\d{4}", r"\d+%", r"\d+\.\d+",
                    r"(?:research|study|report|data|according to|shows|found)",
                    r"(?:研究|数据显示|统计|报告|发现|增长|下降)",
                ]
                if any(_re.search(m, _t, flags=_re.IGNORECASE) for m in _markers):
                    _claim_like.append(_t)
            if _claim_like:
                _cite_pat = _re.compile(
                    r"\[(?:S\d+-\d+|\d+)\]|\[来源[：:].*?\]|https?://\S+", _re.IGNORECASE
                )
                _uncited = [s for s in _claim_like if not _cite_pat.search(s)]
                _cov = 1.0 - (len(_uncited) / max(1, len(_claim_like)))
                quality_summary["citation_coverage"] = max(0.0, min(1.0, _cov))
            else:
                quality_summary["citation_coverage"] = 1.0
        except Exception:
            pass

        _cv_total = len(claims)
        _cv_verified = sum(1 for c in claims if isinstance(c, dict) and c.get("status") == "verified")
        _cv_unsupported = sum(1 for c in claims if isinstance(c, dict) and c.get("status") == "unsupported")
        _cv_contradicted = sum(1 for c in claims if isinstance(c, dict) and c.get("status") == "contradicted")
        quality_summary["claim_verifier_total"] = _cv_total
        quality_summary["claim_verifier_verified"] = _cv_verified
        quality_summary["claim_verifier_unsupported"] = _cv_unsupported
        quality_summary["claim_verifier_contradicted"] = _cv_contradicted
        _record_quality_gates(
            diagnostics=diagnostics,
            quality_summary=quality_summary,
            quality_gate_history=quality_gate_history,
            emitter=emitter,
            epoch=1,
            stage="final",
        )
        evidence_items = build_evidence_items(
            search_runs=search_runs,
            sources=extracted_sources,
            fetched_pages=fetched_pages,
            passages=passages,
        )
        citation_annotations = build_citation_annotations(
            report=final_report,
            sources=extracted_sources,
            evidence_items=evidence_items,
        )
        timeline = build_timeline_artifacts(
            search_runs=search_runs,
            sources=extracted_sources,
            evidence_items=evidence_items,
            quality_gates=quality_gate_history,
        )
        _emit_event(
            emitter,
            "evidence_selected",
            {"mode": "tree", "count": len(evidence_items)},
        )

        deepsearch_artifacts = {
            "mode": "tree",
            "research_brief": brief.to_dict(),
            "strategy_decision": strategy_payload,
            "queries": have_query,
            "research_tree": tree.to_dict(),
            "quality_summary": quality_summary,
            "quality_gates": quality_gate_history,
            "query_coverage": diagnostics.get("query_coverage", {}),
            "freshness_summary": diagnostics.get("freshness_summary", {}),
            "evidence_items": evidence_items,
            "citation_annotations": citation_annotations,
            "timeline": timeline,
            "fetched_pages": fetched_pages,
            "passages": passages,
            "sources": extracted_sources,
            "claims": claims,
        }
        _emit_event(emitter, "quality_update", {"epoch": 1, "stage": "final", **diagnostics})
        _emit_event(
            emitter,
            "research_tree_update",
            {
                "tree": tree.to_dict(),
                "quality": diagnostics,
            },
        )
        _emit_event(
            emitter,
            "research_node_complete",
            {
                "node_id": "deepsearch_tree",
                "summary": final_report[:1200] if isinstance(final_report, str) else "",
                "sources": _compact_search_results(
                    [r.get("result", {}) for r in all_findings],
                    limit=_event_results_limit(),
                ),
                "quality": diagnostics,
            },
        )

        messages = [AIMessage(content=final_report)]
        if save_path:
            messages.append(AIMessage(content=f"(数据已保存: {save_path})"))
        if budget_stop_reason:
            messages.append(AIMessage(content=f"（预算限制提示：{budget_stop_reason}）"))
        if diagnostics.get("freshness_warning"):
            messages.append(
                AIMessage(content="（时间敏感问题的新鲜来源占比较低，建议补充近30天来源并重试。）")
            )

        return {
            "research_plan": have_query,
            "scraped_content": search_runs,
            "draft_report": final_report,
            "final_report": final_report,
            "quality_summary": quality_summary,
            "sources": extracted_sources,
            "deepsearch_artifacts": deepsearch_artifacts,
            "deepsearch_mode": "tree",
            "messages": messages,
            "research_tree": tree.to_dict(),
            "is_complete": False,
            "budget_stop_reason": budget_stop_reason,
            "deepsearch_tokens_used": tokens_used,
            "deepsearch_elapsed_seconds": elapsed,
        }

    except asyncio.CancelledError:
        logger.warning("[deepsearch-tree] 收到取消信号，停止任务")
        return {
            "is_cancelled": True,
            "is_complete": True,
            "errors": ["DeepSearch was cancelled"],
            "final_report": "任务已被取消",
        }
    except Exception as e:
        logger.error(f"[deepsearch-tree] Failed: {e}", exc_info=True)
        # Fallback to linear mode
        logger.info("[deepsearch-tree] Falling back to linear deepsearch...")
        return run_deepsearch_optimized(state, config)


def run_deepsearch_reflection_loop(state: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    brief = build_research_brief(state, config)
    state["research_brief"] = brief.to_dict()
    topic = brief.clarified_goal or state.get("input", "")
    topic_for_planning = brief_topic(brief)
    _check_cancel(state)

    max_loops = max(
        1,
        _configurable_int(
            config,
            "deepsearch_reflection_loops",
            _configurable_int(
                config,
                "deepsearch_max_epochs",
                int(getattr(settings, "deepsearch_max_epochs", 3)),
            ),
        ),
    )
    per_query_results = _configurable_int(
        config,
        "deepsearch_results_per_query",
        int(getattr(settings, "deepsearch_results_per_query", 5)),
    )
    planning_model = _model_for_task("planning", config)
    research_model = _model_for_task("research", config)
    writing_model = _model_for_task("writing", config)
    planner_llm = _chat_model(planning_model, temperature=0.4)
    critic_llm = _chat_model(research_model, temperature=0.2)
    writer_llm = _chat_model(writing_model, temperature=0.4)

    provider_profile = _resolve_provider_profile(state)
    evidence_providers = build_evidence_providers(
        brief=brief,
        config=config,
        search_func=_search_query,
        provider_profile=provider_profile,
    )
    emitter = _resolve_event_emitter(state, config)
    strategy_payload = _strategy_payload(
        state,
        fallback_strategy="reflection_loop",
        fallback_reason="low cost iterative reflection strategy",
    )
    _emit_event(emitter, "brief_created", {"research_brief": brief.to_dict(), "mode": "reflection_loop"})
    _emit_event(
        emitter,
        "strategy_selected",
        {**strategy_payload, "research_brief": brief.to_dict()},
    )

    start_ts = time.time()
    have_query: List[str] = []
    summary_notes: List[str] = []
    search_runs: List[Dict[str, Any]] = []
    provider_evidence_items: List[Dict[str, Any]] = []
    quality_gate_history: List[Dict[str, Any]] = []
    current_query = topic
    loops_completed = 0

    try:
        for loop_idx in range(max_loops):
            _check_cancel(state)
            loops_completed = loop_idx + 1
            node_id = f"deepsearch_reflection_{loops_completed}"
            _emit_event(
                emitter,
                "research_node_start",
                {
                    "node_id": node_id,
                    "topic": current_query,
                    "depth": 1,
                    "parent_id": "deepsearch",
                    "epoch": loops_completed,
                },
            )

            if current_query not in have_query:
                have_query.append(current_query)
            provider_outputs = search_with_evidence_providers(
                providers=evidence_providers,
                query=current_query,
                max_results=per_query_results,
                config=config,
            )
            results = merge_provider_results(provider_outputs)
            provider_evidence_items.extend(merge_provider_evidence(provider_outputs))
            search_runs.append(
                {
                    "query": current_query,
                    "results": results,
                    "timestamp": datetime.now().isoformat(),
                    "strategy": "reflection_loop",
                }
            )
            provider_breakdown = _provider_breakdown(results)
            provider_name = "multi" if len(provider_breakdown) > 1 else (next(iter(provider_breakdown)) if provider_breakdown else "unknown")
            _emit_event(
                emitter,
                "search",
                {
                    "query": current_query,
                    "provider": provider_name,
                    "provider_breakdown": provider_breakdown,
                    "results": _compact_search_results(results, limit=_event_results_limit()),
                    "count": len(results),
                    "mode": "reflection_loop",
                    "epoch": loops_completed,
                },
            )

            enough, summary_text = _summarize_new_knowledge(
                critic_llm,
                topic,
                summary_notes,
                results[: max(1, per_query_results)],
                config,
            )
            if summary_text:
                summary_notes.append(summary_text)

            diagnostics = _build_quality_diagnostics(topic, have_query, search_runs)
            gate_results = _record_quality_gates(
                diagnostics=diagnostics,
                quality_gate_history=quality_gate_history,
                emitter=emitter,
                epoch=loops_completed,
                stage="reflection",
            )
            gate_missing_topics = _missing_topics_from_gate_payload(gate_results)
            if gate_missing_topics:
                state["missing_topics"] = gate_missing_topics
            _emit_event(
                emitter,
                "quality_update",
                {"epoch": loops_completed, "stage": "reflection", **diagnostics},
            )
            _emit_event(
                emitter,
                "research_node_complete",
                {
                    "node_id": node_id,
                    "summary": summary_text[:1200] if isinstance(summary_text, str) else "",
                    "sources": _compact_search_results(results, limit=_event_results_limit()),
                    "quality": diagnostics,
                    "epoch": loops_completed,
                },
            )
            if enough or loop_idx >= max_loops - 1:
                break
            next_queries = _generate_queries(
                planner_llm,
                topic_for_planning,
                have_query,
                summary_notes,
                1,
                config,
                missing_topics=state.get("missing_topics", []),
            )
            current_query = next_queries[0] if next_queries else topic

        report_sources_limit = int(getattr(settings, "deepsearch_report_sources_limit", 20) or 20)
        all_sources: List[Dict[str, Any]] = []
        try:
            from agent.workflows.evidence_extractor import extract_message_sources

            all_sources = extract_message_sources(search_runs)
        except Exception:
            all_sources = []
        report_sources = all_sources[: max(1, report_sources_limit)]
        sources_block = _format_sources_for_writer(
            report_sources,
            search_runs,
            limit=report_sources_limit,
        )
        final_report = (
            _final_report(writer_llm, topic, summary_notes, config, sources=sources_block)
            if summary_notes
            else "未找到足够资料生成报告。"
        )
        final_report = _append_auto_references(final_report, report_sources, limit=report_sources_limit)
        _emit_event(
            emitter,
            "report_written",
            {
                "mode": "reflection_loop",
                "length": len(final_report),
                "source_count": len(report_sources),
            },
        )

        elapsed = time.time() - start_ts
        diagnostics = _build_quality_diagnostics(topic, have_query, search_runs)
        quality_summary = {
            "epochs_completed": loops_completed,
            "summary_count": len(summary_notes),
            "source_count": len(all_sources),
            "selected_url_count": 0,
            "budget_stop_reason": "",
            "tokens_used": _estimate_tokens_from_text(topic + "\n".join(summary_notes) + final_report),
            "elapsed_seconds": elapsed,
            **diagnostics,
        }
        claims = []
        try:
            from agent.workflows.claim_verifier import ClaimVerifier

            verifier = ClaimVerifier(
                min_overlap_tokens=int(getattr(settings, "deepsearch_claim_verifier_min_overlap_tokens", 2) or 2),
                max_evidence_per_claim=int(getattr(settings, "deepsearch_claim_verifier_max_evidence_per_claim", 3) or 3),
            )
            checks = verifier.verify_report(final_report, search_runs)
            claims = [
                {
                    "claim": c.claim,
                    "status": c.status.value,
                    "evidence_urls": c.evidence_urls,
                    "evidence_passages": c.evidence_passages,
                    "score": c.score,
                    "notes": c.notes,
                }
                for c in checks
            ]
        except Exception:
            claims = []
        quality_summary["claim_verifier_total"] = len(claims)
        quality_summary["claim_verifier_verified"] = sum(1 for c in claims if isinstance(c, dict) and c.get("status") == "verified")
        quality_summary["claim_verifier_unsupported"] = sum(1 for c in claims if isinstance(c, dict) and c.get("status") == "unsupported")
        quality_summary["claim_verifier_contradicted"] = sum(1 for c in claims if isinstance(c, dict) and c.get("status") == "contradicted")

        _record_quality_gates(
            diagnostics=diagnostics,
            quality_summary=quality_summary,
            quality_gate_history=quality_gate_history,
            emitter=emitter,
            epoch=loops_completed,
            stage="final",
        )
        evidence_items = _merge_evidence_item_payloads(
            provider_evidence_items,
            build_evidence_items(search_runs=search_runs, sources=all_sources),
        )
        citation_annotations = build_citation_annotations(
            report=final_report,
            sources=all_sources,
            evidence_items=evidence_items,
        )
        timeline = build_timeline_artifacts(
            search_runs=search_runs,
            sources=all_sources,
            evidence_items=evidence_items,
            quality_gates=quality_gate_history,
        )
        _emit_event(
            emitter,
            "evidence_selected",
            {"mode": "reflection_loop", "count": len(evidence_items)},
        )
        deepsearch_artifacts = {
            "mode": "reflection_loop",
            "research_brief": brief.to_dict(),
            "strategy_decision": strategy_payload,
            "queries": have_query,
            "research_tree": None,
            "quality_summary": quality_summary,
            "quality_gates": quality_gate_history,
            "query_coverage": diagnostics.get("query_coverage", {}),
            "freshness_summary": diagnostics.get("freshness_summary", {}),
            "evidence_items": evidence_items,
            "citation_annotations": citation_annotations,
            "timeline": timeline,
            "fetched_pages": [],
            "passages": [],
            "sources": all_sources,
            "claims": claims,
        }
        return {
            "research_plan": have_query,
            "scraped_content": search_runs,
            "draft_report": final_report,
            "final_report": final_report,
            "quality_summary": quality_summary,
            "sources": all_sources,
            "deepsearch_artifacts": deepsearch_artifacts,
            "deepsearch_mode": "reflection_loop",
            "messages": [AIMessage(content=final_report)],
            "is_complete": False,
            "budget_stop_reason": "",
            "deepsearch_tokens_used": quality_summary["tokens_used"],
            "deepsearch_elapsed_seconds": elapsed,
        }
    except asyncio.CancelledError:
        logger.warning("[deepsearch-reflection] 收到取消信号，停止任务")
        return {
            "is_cancelled": True,
            "is_complete": True,
            "errors": ["DeepSearch was cancelled"],
            "final_report": "任务已被取消",
        }


def run_deepsearch_supervisor_workers(state: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    brief = build_research_brief(state, config)
    state["research_brief"] = brief.to_dict()
    topic = brief.clarified_goal or state.get("input", "")
    _check_cancel(state)

    max_rounds = max(
        1,
        _configurable_int(
            config,
            "deepsearch_supervisor_rounds",
            int(getattr(settings, "deepsearch_supervisor_rounds", 2)),
        ),
    )
    max_workers = max(
        1,
        _configurable_int(
            config,
            "deepsearch_supervisor_max_workers",
            int(getattr(settings, "deepsearch_supervisor_max_workers", 4)),
        ),
    )
    queries_per_worker = max(
        1,
        _configurable_int(
            config,
            "deepsearch_supervisor_queries_per_worker",
            int(getattr(settings, "deepsearch_supervisor_queries_per_worker", 2)),
        ),
    )
    parallel_workers = max(
        1,
        _configurable_int(
            config,
            "deepsearch_supervisor_parallel_workers",
            int(getattr(settings, "deepsearch_supervisor_parallel_workers", 2)),
        ),
    )
    per_query_results = _configurable_int(
        config,
        "deepsearch_results_per_query",
        int(getattr(settings, "deepsearch_results_per_query", 5)),
    )
    max_seconds = max(
        0.0,
        _configurable_float(
            config,
            "deepsearch_max_seconds",
            float(getattr(settings, "deepsearch_max_seconds", 0.0)),
        ),
    )
    max_tokens = max(
        0,
        _configurable_int(
            config,
            "deepsearch_max_tokens",
            int(getattr(settings, "deepsearch_max_tokens", 0)),
        ),
    )

    model_profile = build_deepsearch_model_profile(config, _model_for_task)
    model_map = model_profile.get("models", {})
    planning_model = str(model_map.get("supervisor_model") or _model_for_task("planning", config))
    research_model = str(model_map.get("worker_model") or _model_for_task("research", config))
    search_summary_model = str(model_map.get("search_summary_model") or research_model)
    compression_model = str(model_map.get("compression_model") or search_summary_model)
    writing_model = str(model_map.get("writer_model") or _model_for_task("writing", config))
    verifier_model = str(model_map.get("verifier_model") or research_model)
    context_policy = build_deepsearch_context_policy(
        {
            "planning": planning_model,
            "research": research_model,
            "compression": compression_model,
            "writing": writing_model,
            "verifier": verifier_model,
        }
    )
    critic_llm = _chat_model(search_summary_model, temperature=0.2)
    writer_llm = _chat_model(writing_model, temperature=0.4)

    provider_profile = _resolve_provider_profile(state)
    evidence_providers = build_evidence_providers(
        brief=brief,
        config=config,
        search_func=_search_query,
        provider_profile=provider_profile,
    )
    provider_capabilities = build_provider_capability_artifact(evidence_providers, config)
    emitter = _resolve_event_emitter(state, config)
    strategy_payload = _strategy_payload(
        state,
        fallback_strategy="supervisor_workers",
        fallback_reason="explicit supervisor-workers multi-agent strategy",
    )
    _emit_event(emitter, "brief_created", {"research_brief": brief.to_dict(), "mode": "supervisor_workers"})
    _emit_event(
        emitter,
        "strategy_selected",
        {**strategy_payload, "research_brief": brief.to_dict()},
    )

    start_ts = time.time()
    tokens_used = _estimate_tokens_from_text(topic)
    budget_stop_reason = ""
    have_query: List[str] = []
    summary_notes: List[str] = []
    search_runs: List[Dict[str, Any]] = []
    provider_evidence_items: List[Dict[str, Any]] = []
    quality_gate_history: List[Dict[str, Any]] = []
    worker_runs: List[Dict[str, Any]] = []
    supervisor_decisions: List[Dict[str, Any]] = []
    decision_log: List[Dict[str, Any]] = []
    task_runtime = ResearchTaskRuntime(mode="supervisor_workers", parent_id="deepsearch_supervisor")
    missing_topics = list(state.get("missing_topics", []) or [])
    rounds_completed = 0

    try:
        for round_index in range(1, max_rounds + 1):
            _check_cancel(state)
            rounds_completed = round_index
            budget_stop_reason = _budget_stop_reason(
                start_ts=start_ts,
                tokens_used=tokens_used,
                max_seconds=max_seconds,
                max_tokens=max_tokens,
            )
            if budget_stop_reason:
                break

            tasks = build_worker_tasks(
                brief=brief,
                round_index=round_index,
                max_workers=max_workers,
                queries_per_worker=queries_per_worker,
                historical_queries=have_query,
                missing_topics=missing_topics,
            )
            task_runtime.register_tasks(tasks, metadata={"round": round_index})
            _emit_event(
                emitter,
                "task_create",
                {
                    "mode": "supervisor_workers",
                    "round": round_index,
                    "worker_count": len(tasks),
                    "tasks": [task.to_dict() for task in tasks],
                },
            )

            round_worker_runs: List[Dict[str, Any]] = []
            task_order = {task.worker_id: idx for idx, task in enumerate(tasks)}
            for task in tasks:
                started_subtask = task_runtime.start_task(task.worker_id)
                _emit_event(
                    emitter,
                    "research_node_start",
                    {
                        "node_id": task.worker_id,
                        "topic": task.topic,
                        "depth": 1,
                        "parent_id": "deepsearch_supervisor",
                        "round": round_index,
                        "context_id": task.context_id,
                        "subtask": started_subtask,
                    },
                )

            def _collect_worker_payload(task):
                worker_started_at = datetime.now().isoformat()
                task_results: List[Dict[str, Any]] = []
                task_evidence: List[Dict[str, Any]] = []
                task_search_runs: List[Dict[str, Any]] = []
                errors: List[str] = []
                for query in task.queries:
                    try:
                        provider_outputs = search_with_evidence_providers(
                            providers=evidence_providers,
                            query=query,
                            max_results=per_query_results,
                            config=config,
                        )
                        results = merge_provider_results(provider_outputs)
                        evidence_payload = merge_provider_evidence(provider_outputs)
                    except Exception as exc:
                        results = []
                        evidence_payload = []
                        errors.append(str(exc))
                    task_results.extend(results)
                    task_evidence.extend(evidence_payload)
                    task_search_runs.append(
                        {
                            "query": query,
                            "results": results,
                            "timestamp": datetime.now().isoformat(),
                            "strategy": "supervisor_workers",
                            "round": round_index,
                            "worker_id": task.worker_id,
                            "context_id": task.context_id,
                            "worker_topic": task.topic,
                            "worker_focus": task.focus,
                        }
                    )
                return {
                    "task": task,
                    "started_at": worker_started_at,
                    "results": task_results,
                    "evidence": task_evidence,
                    "search_runs": task_search_runs,
                    "errors": errors,
                }

            if parallel_workers > 1 and len(tasks) > 1:
                with ThreadPoolExecutor(max_workers=min(parallel_workers, len(tasks))) as executor:
                    task_payloads = list(executor.map(_collect_worker_payload, tasks))
            else:
                task_payloads = [_collect_worker_payload(task) for task in tasks]
            task_payloads.sort(key=lambda payload: task_order.get(payload["task"].worker_id, 0))

            for payload in task_payloads:
                _check_cancel(state)
                task = payload["task"]
                task_results = payload["results"]
                task_evidence = payload["evidence"]
                errors = payload["errors"]
                provider_evidence_items.extend(task_evidence)
                for search_run in payload["search_runs"]:
                    query = search_run.get("query", "")
                    if query and query not in have_query:
                        have_query.append(query)
                    results = search_run.get("results", [])
                    tokens_used += _estimate_tokens_from_results(results)
                    search_runs.append(search_run)
                    provider_breakdown = _provider_breakdown(results)
                    provider_name = "multi" if len(provider_breakdown) > 1 else (next(iter(provider_breakdown)) if provider_breakdown else "unknown")
                    _emit_event(
                        emitter,
                        "search",
                        {
                            "query": query,
                            "provider": provider_name,
                            "provider_breakdown": provider_breakdown,
                            "results": _compact_search_results(results, limit=_event_results_limit()),
                            "count": len(results),
                            "mode": "supervisor_workers",
                            "round": round_index,
                            "worker_id": task.worker_id,
                            "context_id": task.context_id,
                        },
                    )

                enough, summary_text = _summarize_new_knowledge(
                    critic_llm,
                    task.topic,
                    summary_notes,
                    task_results[: max(1, per_query_results)],
                    config,
                )
                if summary_text:
                    summary_notes.append(f"{task.focus}: {summary_text}")
                    tokens_used += _estimate_tokens_from_text(summary_text)
                worker_run = build_worker_run(
                    task=task,
                    results=task_results,
                    evidence_items=task_evidence,
                    summary=summary_text,
                    errors=errors,
                    started_at=payload["started_at"],
                ).to_dict()
                completed_subtask = task_runtime.complete_task(
                    task.worker_id,
                    result_count=len(task_results),
                    evidence_count=len(task_evidence),
                    compressed_summary=summary_text,
                    raw_notes=[summary_text] if summary_text else [],
                    errors=errors,
                    completed_at=worker_run.get("completed_at"),
                )
                worker_runs.append(worker_run)
                round_worker_runs.append(worker_run)
                decision_log.append(
                    {
                        "type": "worker_reflection",
                        "round_index": round_index,
                        "worker_id": task.worker_id,
                        "context_id": task.context_id,
                        "focus": task.focus,
                        "topic": task.topic,
                        "result_count": len(task_results),
                        "evidence_count": len(task_evidence),
                        "errors": errors,
                        "summary": summary_text[:1200] if isinstance(summary_text, str) else "",
                        "timestamp": worker_run.get("completed_at"),
                    }
                )
                _emit_event(
                    emitter,
                    "thinking",
                    {
                        "text": f"Worker {task.focus} found {len(task_results)} results and {len(task_evidence)} evidence items.",
                        "node": "supervisor_workers",
                        "type": "worker_reflection",
                        "round": round_index,
                        "worker_id": task.worker_id,
                        "context_id": task.context_id,
                    },
                )
                _emit_event(
                    emitter,
                    "research_node_complete",
                    {
                        "node_id": task.worker_id,
                        "summary": summary_text[:1200] if isinstance(summary_text, str) else "",
                        "sources": _compact_search_results(task_results, limit=_event_results_limit()),
                        "round": round_index,
                        "context_id": task.context_id,
                        "quality": {"enough": bool(enough), "errors": errors},
                        "subtask": completed_subtask,
                    },
                )

            diagnostics = _build_quality_diagnostics(topic, have_query, search_runs)
            gate_payload = _record_quality_gates(
                diagnostics=diagnostics,
                quality_gate_history=quality_gate_history,
                emitter=emitter,
                epoch=round_index,
                stage="supervisor_round",
            )
            decision = decide_supervisor_next_step(
                round_index=round_index,
                max_rounds=max_rounds,
                worker_runs=round_worker_runs,
                gate_payload=gate_payload,
                diagnostics=diagnostics,
            )
            decision_payload = decision.to_dict()
            supervisor_decisions.append(decision_payload)
            decision_log.append(
                {
                    "type": "supervisor_decision",
                    "round_index": round_index,
                    "action": decision.action,
                    "reason": decision.reason,
                    "missing_topics": missing_topics,
                    "failed_gates": decision.failed_gates,
                    "quality_snapshot": decision.quality_snapshot,
                    "timestamp": datetime.now().isoformat(),
                }
            )
            _emit_event(
                emitter,
                "thinking",
                {
                    "text": f"Supervisor decision: {decision.action} because {decision.reason}.",
                    "node": "supervisor_workers",
                    "type": "supervisor_decision",
                    "round": round_index,
                    "action": decision.action,
                },
            )
            missing_topics = decision.missing_topics or []
            state["missing_topics"] = missing_topics
            _emit_event(
                emitter,
                "quality_update",
                {
                    "epoch": round_index,
                    "stage": "supervisor_round",
                    "supervisor_decision": decision_payload,
                    **diagnostics,
                },
            )
            _emit_event(
                emitter,
                "task_update",
                {
                    "mode": "supervisor_workers",
                    "round": round_index,
                    "action": decision.action,
                    "reason": decision.reason,
                    "missing_topics": missing_topics,
                },
            )
            if decision.action == "synthesize":
                break

        if not summary_notes and search_runs:
            flat_results = []
            for run in search_runs:
                results = run.get("results") if isinstance(run, dict) else []
                if isinstance(results, list):
                    flat_results.extend([item for item in results if isinstance(item, dict)])
            formatted = _format_results(flat_results[:10]) if flat_results else ""
            if formatted:
                summary_notes.append(formatted)

        report_sources_limit = _configurable_int(
            config,
            "deepsearch_report_sources_limit",
            int(getattr(settings, "deepsearch_report_sources_limit", 20) or 20),
        )
        fetch_source_limit = _configurable_int(
            config,
            "deepsearch_supervisor_fetch_source_limit",
            int(getattr(settings, "deepsearch_supervisor_fetch_source_limit", 8) or 8),
        )
        all_sources: List[Dict[str, Any]] = []
        try:
            from agent.workflows.evidence_extractor import extract_message_sources

            all_sources = extract_message_sources(search_runs)
        except Exception:
            all_sources = []
        if _configurable_bool(
            config,
            "deepsearch_source_curator_enabled",
            bool(getattr(settings, "deepsearch_source_curator_enabled", False)),
        ):
            all_sources = curate_sources(all_sources, brief=brief)
        report_sources = all_sources[: max(1, report_sources_limit)]
        fetched_pages: List[Dict[str, Any]] = []
        passages: List[Dict[str, Any]] = []
        if _configurable_bool(
            config,
            "deepsearch_supervisor_fetch_passages",
            bool(getattr(settings, "deepsearch_supervisor_fetch_passages", False)),
        ):
            fetched_pages, passages = _build_fetcher_evidence(
                _source_urls_for_fetch(report_sources, limit=fetch_source_limit),
                config,
            )
        evidence_items = _merge_evidence_item_payloads(
            provider_evidence_items,
            build_evidence_items(
                search_runs=search_runs,
                sources=all_sources,
                fetched_pages=fetched_pages,
                passages=passages,
            ),
        )
        claim_ledger: List[Dict[str, Any]] = []
        summary_notes_for_writer = list(summary_notes)
        if _configurable_bool(
            config,
            "deepsearch_enable_claim_ledger",
            bool(getattr(settings, "deepsearch_enable_claim_ledger", False)),
        ):
            claim_ledger = build_claim_ledger(
                summary_notes=summary_notes,
                search_runs=search_runs,
                sources=report_sources,
                evidence_items=evidence_items,
                passages=passages,
                max_claims=_configurable_int(
                    config,
                    "deepsearch_claim_ledger_max_claims",
                    int(getattr(settings, "deepsearch_claim_ledger_max_claims", 24) or 24),
                ),
                min_overlap_tokens=_configurable_int(
                    config,
                    "deepsearch_claim_verifier_min_overlap_tokens",
                    int(getattr(settings, "deepsearch_claim_verifier_min_overlap_tokens", 2) or 2),
                ),
                max_evidence_per_claim=_configurable_int(
                    config,
                    "deepsearch_claim_verifier_max_evidence_per_claim",
                    int(getattr(settings, "deepsearch_claim_verifier_max_evidence_per_claim", 3) or 3),
                ),
            )
            ledger_text = format_claim_ledger_for_writer(claim_ledger)
            if ledger_text:
                summary_notes_for_writer.append(ledger_text)
        sources_block = _format_sources_for_writer(
            report_sources,
            search_runs,
            limit=report_sources_limit,
        )
        draft_report = (
            _final_report(writer_llm, topic, summary_notes_for_writer, config, sources=sources_block)
            if summary_notes_for_writer
            else "未找到足够资料生成报告。"
        )
        try:
            _checks, claims, claim_stats = _verify_report_claims(
                draft_report,
                search_runs,
                passages=passages,
                config=config,
            )
        except Exception:
            claims = []
            claim_stats = {
                "claim_verifier_total": 0,
                "claim_verifier_verified": 0,
                "claim_verifier_unsupported": 0,
                "claim_verifier_contradicted": 0,
            }
        final_revision_count = 0
        if _configurable_bool(
            config,
            "deepsearch_final_verifier_revise",
            bool(getattr(settings, "deepsearch_final_verifier_revise", False)),
        ):
            max_revisions = max(
                0,
                _configurable_int(
                    config,
                    "deepsearch_final_verifier_max_revisions",
                    int(getattr(settings, "deepsearch_final_verifier_max_revisions", 1) or 1),
                ),
            )
            while (
                final_revision_count < max_revisions
                and (
                    int(claim_stats.get("claim_verifier_unsupported") or 0) > 0
                    or int(claim_stats.get("claim_verifier_contradicted") or 0) > 0
                )
            ):
                draft_report = _revise_report_for_claim_failures(
                    writer_llm,
                    topic=topic,
                    report=draft_report,
                    claims=claims,
                    sources=sources_block,
                    config=config,
                )
                final_revision_count += 1
                try:
                    _checks, claims, claim_stats = _verify_report_claims(
                        draft_report,
                        search_runs,
                        passages=passages,
                        config=config,
                    )
                except Exception:
                    break
        final_report = _append_auto_references(draft_report, report_sources, limit=report_sources_limit)
        missing_citation_claims, citation_coverage = _estimate_citation_coverage(draft_report)
        _emit_event(
            emitter,
            "report_written",
            {
                "mode": "supervisor_workers",
                "length": len(final_report),
                "source_count": len(report_sources),
                "claim_ledger_count": len(claim_ledger),
                "final_revision_count": final_revision_count,
            },
        )

        elapsed = time.time() - start_ts
        diagnostics = _build_quality_diagnostics(topic, have_query, search_runs)
        quality_summary = {
            "epochs_completed": rounds_completed,
            "supervisor_rounds_completed": rounds_completed,
            "worker_count": len(worker_runs),
            "subtask_count": task_runtime.to_artifact().get("subtask_count", 0),
            "subtask_status_counts": task_runtime.to_artifact().get("status_counts", {}),
            "decision_log_count": len(decision_log),
            "parallel_workers": parallel_workers,
            "worker_dispatch": "parallel" if parallel_workers > 1 else "sequential",
            "context_policy_stage_count": context_policy.get("stage_count", 0),
            "model_profile_stage_count": model_profile.get("stage_count", 0),
            "evidence_provider_count": provider_capabilities.get("provider_count", 0),
            "summary_count": len(summary_notes),
            "source_count": len(all_sources),
            "selected_source_count": len(report_sources),
            "fetched_page_count": len(fetched_pages),
            "passage_count": len(passages),
            "evidence_item_count": len(evidence_items),
            "claim_ledger_count": len(claim_ledger),
            "final_revision_count": final_revision_count,
            "citation_coverage": citation_coverage,
            "citation_coverage_score": citation_coverage,
            "missing_citation_claims": missing_citation_claims,
            "budget_stop_reason": budget_stop_reason or "",
            "tokens_used": _estimate_tokens_from_text(topic + "\n".join(summary_notes) + final_report) + tokens_used,
            "elapsed_seconds": elapsed,
            **diagnostics,
        }
        quality_summary.update(claim_stats)

        final_gate_payload = _record_quality_gates(
            diagnostics=diagnostics,
            quality_summary=quality_summary,
            quality_gate_history=quality_gate_history,
            emitter=emitter,
            epoch=rounds_completed,
            stage="final",
        )
        continue_requests: List[Dict[str, Any]] = []
        if _configurable_bool(
            config,
            "deepsearch_reflection_gap_queries",
            bool(getattr(settings, "deepsearch_reflection_gap_queries", False)),
        ):
            for idx, query in enumerate(
                gap_queries_from_quality_gates(
                    brief=brief,
                    quality_gates=final_gate_payload,
                    claims=claims,
                    max_queries=4,
                ),
                1,
            ):
                continue_requests.append(
                    {
                        "request_id": f"auto_gap_{rounds_completed}_{idx}",
                        "target_type": "gap",
                        "target_text": query,
                        "strategy": "supervisor_workers",
                    }
                )
        citation_annotations = build_citation_annotations(
            report=final_report,
            sources=all_sources,
            evidence_items=evidence_items,
        )
        timeline = build_timeline_artifacts(
            search_runs=search_runs,
            sources=all_sources,
            evidence_items=evidence_items,
            quality_gates=quality_gate_history,
        )
        intermediate_steps = build_intermediate_steps(
            worker_runs=worker_runs,
            supervisor_decisions=supervisor_decisions,
        )
        report_plan = build_sectioned_report_plan(
            research_brief=brief.to_dict(),
            worker_runs=worker_runs,
            evidence_items=evidence_items,
        )
        sectioned_report = build_sectioned_report_artifact(report_plan, config)
        quality_summary["sectioned_report_enabled"] = bool(sectioned_report.get("enabled"))
        research_pipeline = build_supervisor_workers_pipeline_artifact(
            research_brief=brief.to_dict(),
            task_runtime=task_runtime.to_artifact(),
            worker_runs=worker_runs,
            supervisor_decisions=supervisor_decisions,
            decision_log=decision_log,
            summary_notes=summary_notes,
            evidence_items=evidence_items,
            claim_ledger=claim_ledger,
            quality_summary=quality_summary,
            final_report=final_report,
        )
        _emit_event(
            emitter,
            "evidence_selected",
            {"mode": "supervisor_workers", "count": len(evidence_items)},
        )
        deepsearch_artifacts = {
            "mode": "supervisor_workers",
            "research_brief": brief.to_dict(),
            "strategy_decision": strategy_payload,
            "queries": have_query,
            "research_tree": None,
            "quality_summary": quality_summary,
            "quality_gates": quality_gate_history,
            "query_coverage": diagnostics.get("query_coverage", {}),
            "freshness_summary": diagnostics.get("freshness_summary", {}),
            "evidence_items": evidence_items,
            "citation_annotations": citation_annotations,
            "timeline": timeline,
            "supervisor_decisions": supervisor_decisions,
            "worker_runs": worker_runs,
            "research_task_runtime": task_runtime.to_artifact(),
            "decision_log": decision_log,
            "research_pipeline": research_pipeline,
            "report_plan": report_plan,
            "sectioned_report": sectioned_report,
            "context_policy": context_policy,
            "model_profile": model_profile,
            "provider_capabilities": provider_capabilities,
            "intermediate_steps": intermediate_steps,
            "supervisor_policy": {
                "max_rounds": max_rounds,
                "max_workers": max_workers,
                "queries_per_worker": queries_per_worker,
                "parallel_workers": parallel_workers,
            },
            "continue_requests": continue_requests,
            "claim_ledger": claim_ledger,
            "fetched_pages": fetched_pages,
            "passages": passages,
            "sources": all_sources,
            "claims": claims,
        }
        return {
            "research_plan": have_query,
            "scraped_content": search_runs,
            "draft_report": final_report,
            "final_report": final_report,
            "quality_summary": quality_summary,
            "sources": all_sources,
            "deepsearch_artifacts": deepsearch_artifacts,
            "deepsearch_mode": "supervisor_workers",
            "messages": [AIMessage(content=final_report)],
            "is_complete": False,
            "budget_stop_reason": budget_stop_reason or "",
            "deepsearch_tokens_used": quality_summary["tokens_used"],
            "deepsearch_elapsed_seconds": elapsed,
        }
    except asyncio.CancelledError:
        logger.warning("[deepsearch-supervisor] 收到取消信号，停止任务")
        task_runtime.finish_open_tasks(status="cancelled", error="DeepSearch was cancelled")
        return {
            "is_cancelled": True,
            "is_complete": True,
            "errors": ["DeepSearch was cancelled"],
            "final_report": "任务已被取消",
            "deepsearch_artifacts": {
                "mode": "supervisor_workers",
                "research_task_runtime": task_runtime.to_artifact(),
                "decision_log": decision_log,
            },
        }
    except Exception as exc:
        logger.error(f"[deepsearch-supervisor] Failed: {exc}", exc_info=True)
        return run_deepsearch_optimized(state, config)


def run_deepsearch_auto(state: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Auto-select between tree and linear deep search based on settings.

    Uses tree-based exploration if enabled in settings, otherwise falls back
    to the optimized linear approach.
    """
    brief = build_research_brief(state, config)
    state["research_brief"] = brief.to_dict()
    decision = select_deepsearch_strategy(
        brief=brief,
        config=config,
        settings=settings,
        simple_query_detector=_auto_mode_prefers_linear,
    )
    state["deepsearch_strategy_decision"] = decision.to_dict()
    strategy = decision.strategy

    def _with_event_marker(result: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(result, dict) and not bool(result.get("is_cancelled")):
            result.setdefault("_deepsearch_events_emitted", True)
        return result

    if strategy == "tree":
        logger.info("[deepsearch] Using tree-based exploration mode (override)")
        return _with_event_marker(run_deepsearch_tree(state, config))

    if strategy == "linear":
        logger.info("[deepsearch] Using linear exploration mode (override)")
        return _with_event_marker(run_deepsearch_optimized(state, config))

    if strategy == "reflection_loop":
        logger.info("[deepsearch] Using reflection loop exploration mode")
        return _with_event_marker(run_deepsearch_reflection_loop(state, config))

    if strategy == "supervisor_workers":
        logger.info("[deepsearch] Using supervisor-workers exploration mode")
        return _with_event_marker(run_deepsearch_supervisor_workers(state, config))

    if strategy == "linear_light":
        logger.info("[deepsearch] Auto strategy selected light linear exploration")
        simple_config = dict(config) if isinstance(config, dict) else {"configurable": {}}
        existing_cfg = simple_config.get("configurable")
        simple_cfg = dict(existing_cfg) if isinstance(existing_cfg, dict) else {}
        simple_config["configurable"] = simple_cfg
        for key, value in (decision.parameters or {}).items():
            simple_cfg.setdefault(key, value)
        return _with_event_marker(run_deepsearch_optimized(state, simple_config))

    if strategy == "hybrid_private_web":
        logger.info("[deepsearch] Using hybrid private/web linear exploration")
        return _with_event_marker(run_deepsearch_optimized(state, config))

    logger.info("[deepsearch] Using linear exploration mode")
    return _with_event_marker(run_deepsearch_optimized(state, config))
