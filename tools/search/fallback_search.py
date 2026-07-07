"""
Fallback search tool with multi-engine sequencing.

This is the "API-based" web search entrypoint used by agent mode when multiple
engines are configured via `SEARCH_ENGINES` in `.env`.

Why this exists:
- Playwright-based browsing of public search engines frequently triggers anti-bot
  challenges (captcha / interstitials).
- API search providers (Tavily/Serper/SerpAPI/Bing/Exa/Firecrawl/Google CSE) are
  far more stable and deterministic.
"""

import logging
from typing import Any, Optional

from langchain_core.tools import tool

from common.config import settings
from tools.search.providers import (
    bing_search,
    bocha_search,
    exa_search,
    firecrawl_search,
    google_cse_search,
    serpapi_search,
    serper_search,
)
from tools.search.search import tavily_search

logger = logging.getLogger(__name__)


def _tavily(query: str, max_results: int) -> list[dict[str, Any]]:
    return tavily_search.invoke({"query": query, "max_results": max_results})


def _duckduckgo(query: str, max_results: int) -> list[dict[str, Any]]:
    from tools.search.multi_search import DuckDuckGoProvider

    provider = DuckDuckGoProvider()
    if not provider.is_available():
        return []
    return [
        {
            "title": result.title,
            "url": result.url,
            "snippet": result.snippet,
            "summary": result.snippet,
            "raw_excerpt": result.content,
            "score": result.score,
            "source": result.provider,
        }
        for result in provider.search(query, max_results=max_results)
    ]


def _normalize_multi_search_results(
    query: str, max_results: int
) -> list[dict[str, Any]]:
    from tools.search.multi_search import get_search_orchestrator

    results = get_search_orchestrator().search(query=query, max_results=max_results)
    return [
        {
            "title": result.title,
            "url": result.url,
            "snippet": result.snippet,
            "summary": result.snippet,
            "raw_excerpt": result.content,
            "score": result.score,
            "source": result.provider,
            "published_date": result.published_date,
        }
        for result in results
    ]


# Map engine key -> handler
_ENGINE_HANDLERS = {
    "tavily": _tavily,
    "bocha": lambda query, max_results: bocha_search(
        query=query, max_results=max_results
    ),
    "duckduckgo": _duckduckgo,
    "serper": lambda query, max_results: serper_search(
        query=query, max_results=max_results
    ),
    "serpapi": lambda query, max_results: serpapi_search(
        query=query, max_results=max_results
    ),
    "bing": lambda query, max_results: bing_search(
        query=query, max_results=max_results
    ),
    "google_cse": lambda query, max_results: google_cse_search(
        query=query, max_results=max_results
    ),
    "exa": lambda query, max_results: exa_search(query=query, max_results=max_results),
    "firecrawl": lambda query, max_results: firecrawl_search(
        query=query, max_results=max_results
    ),
}

# Friendly aliases (align with Shannon/OpenManus naming)
_ENGINE_ALIASES = {
    "bochaai": "bocha",
    "google": "google_cse",
    "googlecse": "google_cse",
    "google_custom_search": "google_cse",
}


def run_fallback_search(
    *,
    query: str,
    max_results: int = 5,
    engines: Optional[list[str]] = None,
) -> tuple[Optional[str], list[dict[str, Any]]]:
    """
    Run a multi-engine API search and return (engine_used, results).

    This helper reports which engine actually produced the results.
    """
    engine_list = (
        engines or getattr(settings, "search_engines_list", None) or ["tavily"]
    )
    for eng in engine_list:
        key = (eng or "").strip().lower()
        if not key:
            continue
        key = _ENGINE_ALIASES.get(key, key)
        handler = _ENGINE_HANDLERS.get(key)
        if not handler:
            logger.warning(f"Unknown search engine '{eng}', skipping")
            continue
        try:
            results = handler(query=query, max_results=max_results)
            if results:
                return key, results
        except Exception as e:
            logger.warning(f"Engine {eng} failed: {e}")
            continue
    try:
        results = _normalize_multi_search_results(query=query, max_results=max_results)
        if results:
            return "multi_search", results
    except Exception as e:
        logger.warning(f"Multi-search fallback failed: {e}")
    return None, []


@tool
def fallback_search(
    query: str,
    max_results: int = 5,
    engines: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """
    Multi-engine search with fallback.

    Args:
        query: search query string
        max_results: number of results to return
        engines: ordered list of engines; defaults to config.search_engines or ["tavily"]

    Returns:
        List of search result dicts from the first successful engine.
    """
    _, results = run_fallback_search(
        query=query, max_results=max_results, engines=engines
    )
    return results
