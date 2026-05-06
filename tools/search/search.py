import logging
import textwrap
from typing import Any, Optional

from langchain.tools import tool
from langchain_core.prompts import ChatPromptTemplate

from agent.core.llm_factory import create_chat_model
from common.config import settings
from tools.search.tavily_key_pool import get_tavily_key_pool

logger = logging.getLogger(__name__)

_SUMMARY_TOP_RESULTS = 1


def _trim_text(text: str, max_len: int = 4000) -> str:
    """Truncate long text to avoid token blow-ups."""
    if not text:
        return ""
    return text[:max_len]


def _summarize_content(raw_content: str) -> Optional[str]:
    """
    Summarize raw content to keep writer context small.
    Returns None on failure so callers can fallback gracefully.
    """
    if not raw_content:
        return None

    try:
        llm = create_chat_model(settings.primary_model, temperature=0.3)
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a concise analyst. Summarize the page into 3-5 bullet points. "
                    "Keep key facts, avoid filler, cite numbers if present.",
                ),
                ("human", "{content}"),
            ]
        )
        response = llm.invoke(
            prompt.format_messages(content=_trim_text(raw_content, 3500))
        )
        content = getattr(response, "content", None) or ""
        return textwrap.dedent(content).strip() or None
    except Exception as e:
        logger.warning(f"Summarization failed: {e}")
        return None


@tool
def tavily_search(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """
    Perform a deep search using Tavily API.

    Args:
        query: The search query
        max_results: Maximum number of results to return

    Returns:
        List of search results with content
    """
    try:
        try:
            from tavily import TavilyClient  # type: ignore
        except Exception:
            logger.error(
                "Missing dependency: tavily-python. Install with `pip install tavily-python`."
            )
            return []

        pool = get_tavily_key_pool()
        api_key = pool.get_key()
        if not api_key:
            logger.warning("No Tavily API key available; returning empty results.")
            return []

        # Retry with key rotation on quota exhaustion
        response = None
        for _attempt in range(pool.available_count):
            try:
                client = TavilyClient(api_key=api_key)
                response = client.search(
                    query=query,
                    search_depth="advanced",
                    max_results=max_results,
                    include_answer=True,
                    include_raw_content=True,
                )
                break  # Success
            except Exception as key_err:
                if pool.is_quota_error(key_err):
                    logger.warning(f"Tavily key quota exhausted: {key_err}")
                    api_key = pool.mark_exhausted(api_key)
                    if not api_key:
                        logger.error("All Tavily keys exhausted.")
                        return []
                    continue
                raise  # Non-quota error, propagate

        if response is None:
            logger.error("Tavily search failed: all keys exhausted.")
            return []

        results = []
        seen_urls = set()

        # Sort results by score descending if score exists
        sorted_results = sorted(
            response.get("results", []), key=lambda r: r.get("score", 0), reverse=True
        )

        for result in sorted_results:
            url = result.get("url", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            raw_content = result.get("raw_content", "") or result.get("content", "")
            summary = (
                _summarize_content(raw_content)
                if len(results) < _SUMMARY_TOP_RESULTS
                else None
            )

            results.append(
                {
                    "title": result.get("title", ""),
                    "url": url,
                    "summary": summary or _trim_text(result.get("content", ""), 600),
                    "snippet": _trim_text(result.get("content", ""), 600),
                    "raw_excerpt": _trim_text(raw_content, 1200),
                    "score": result.get("score", 0),
                }
            )

            if len(results) >= max_results:
                break

        logger.info(f"Tavily search for '{query}' returned {len(results)} results")
        return results

    except Exception as e:
        logger.error(f"Tavily search error: {e!s}")
        # Return empty list to let upstream fallback gracefully
        return []


def search_multiple_queries(
    queries: list[str], max_results_per_query: int = 5
) -> list[dict[str, Any]]:
    """
    Execute multiple search queries in parallel.

    Args:
        queries: List of search queries
        max_results_per_query: Max results per query

    Returns:
        Combined search results
    """
    all_results = []

    for query in queries:
        results = tavily_search(query, max_results=max_results_per_query)
        all_results.extend(results)

    return all_results
