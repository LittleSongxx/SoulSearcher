from __future__ import annotations

import uuid
from typing import Any, Optional

from langchain_core.tools import tool

from common.config import settings


def _extract_report(result: dict[str, Any]) -> str:
    for key in ("final_report", "draft_report", "summary", "answer"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    messages = result.get("messages") or []
    if messages:
        last = messages[-1]
        content = getattr(last, "content", None)
        if isinstance(content, str) and content.strip():
            return content.strip()
    return "Deep research completed, but no report text was returned."


@tool("deep_research", parse_docstring=True)
def deep_research_tool(
    query: str,
    strategy: Optional[str] = None,
    max_seconds: Optional[float] = None,
) -> str:
    """Run Weaver's DeepSearch pipeline for a research question.

    Args:
        query: The research question or task.
        strategy: Optional DeepSearch strategy, usually supervisor_workers or tree.
        max_seconds: Optional runtime budget in seconds.
    """
    topic = str(query or "").strip()
    if not topic:
        return "Error: query is required."
    if not (settings.openai_api_key or "").strip():
        return "Error: OPENAI_API_KEY is not configured."

    from agent.workflows.deepsearch_optimized import run_deepsearch_auto

    thread_id = f"deep_research_tool_{uuid.uuid4().hex[:12]}"
    deep_cfg: dict[str, Any] = {
        "thread_id": thread_id,
        "model": settings.primary_model,
        "search_mode": {
            "mode": "deep",
            "use_web": True,
            "use_agent": True,
            "use_deep": True,
            "use_deep_prompt": True,
        },
        "user_id": settings.memory_user_id,
    }
    if strategy:
        deep_cfg["deepsearch_strategy"] = strategy
        deep_cfg["deepsearch_mode"] = strategy
    if max_seconds is not None:
        deep_cfg["deepsearch_max_seconds"] = max_seconds

    result = run_deepsearch_auto(
        {"input": topic, "user_id": settings.memory_user_id},
        {"configurable": deep_cfg, "recursion_limit": 80},
    )
    report = _extract_report(result if isinstance(result, dict) else {})
    artifacts = result.get("deepsearch_artifacts", {}) if isinstance(result, dict) else {}
    sources = artifacts.get("sources") or artifacts.get("all_sources") or []
    if sources:
        return f"{report}\n\nSources found: {len(sources)}"
    return report
