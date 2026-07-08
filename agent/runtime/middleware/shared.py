"""Shared graph-middleware functions — Anthropic Agent SDK hooks pattern.

Instead of duplicating cross-cutting concerns across fixed-role graph nodes, each concern lives as a standalone function that both the
LangGraph graph nodes AND the deer-flow middleware chain can import.

This follows Anthropic's Agent SDK hooks model:
  - PreToolUse / PostToolUse  →  before_tool / after_tool
  - SessionStart / SessionEnd  →  on_session_start / on_session_end
  - Stop                       →  on_loop_iteration
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Loop Safety — prevents infinite ReAct loops
# ---------------------------------------------------------------------------

def check_loop(messages: list, max_repetitions: int = 3) -> tuple[bool, str]:
    """Check whether the last few LLM responses indicate a loop.

    Returns (is_looping, hint_message).  Call this near the top of every
    tool-calling or review loop iteration.
    """
    contents = [
        str(getattr(m, "content", "")).strip()
        for m in messages[-max(5, max_repetitions + 2):]
        if hasattr(m, "content") and getattr(m, "content", None)
    ]
    contents = [content for content in contents if content]
    if len(contents) < max_repetitions:
        return False, ""

    recent = contents[-max_repetitions:]
    exact_duplicate = len(set(recent)) == 1
    prefix_counts: dict[str, int] = {}
    for content in contents:
        prefix = content[:100]
        prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1

    if exact_duplicate or any(count >= max_repetitions + 1 for count in prefix_counts.values()):
        logger.warning("[SharedMiddleware] Loop detected")
        return True, (
            "\n[System Notice: You appear to be repeating yourself. "
            "Please try a different approach or conclude your research if you're stuck.]"
        )
    return False, ""


# ---------------------------------------------------------------------------
# Token Usage — per-phase cost tracking
# ---------------------------------------------------------------------------

def record_token_usage(
    phase: str,
    response: Any,
    config: Any | None = None,
) -> None:
    """Record token usage from an LLM response for cost attribution."""
    from agent.core.middleware import get_token_tracker

    usage = getattr(response, "usage_metadata", None) or {}
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    if input_tokens or output_tokens:
        get_token_tracker(config).record(phase, input_tokens, output_tokens)


def get_token_summary(config: Any | None = None) -> dict[str, Any]:
    """Get current token usage summary across all phases."""
    from agent.core.middleware import get_token_tracker

    return get_token_tracker(config).get_summary()


# ---------------------------------------------------------------------------
# Context Budget — summarization-aware trimming
# ---------------------------------------------------------------------------

def _message_text(msg: Any) -> str:
    if hasattr(msg, "content"):
        content = getattr(msg, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("text"):
                    parts.append(str(item["text"]))
            return "\n".join(parts)
    return str(msg or "")


def _priority_for_message(msg: Any, *, tool_name_patterns: tuple[str, ...]) -> int:
    name = str(getattr(msg, "name", "") or "")
    content = _message_text(msg)
    if getattr(msg, "type", "") in {"system", "human"}:
        return 100
    if name in tool_name_patterns:
        text = content.lower()
        if "<research-todo-list>" in text or "<memory_context>" in text:
            return 95
        if "thinktool" in name.lower():
            return 90
        return 80
    if getattr(msg, "type", "") in {"tool"}:
        return 75
    if getattr(msg, "type", "") in {"ai"}:
        return 70
    return 50


def _truncate_tool_message(msg: Any, max_chars: int):
    from langchain_core.messages import ToolMessage

    content = _message_text(msg)
    if len(content) <= max_chars:
        return msg
    return ToolMessage(
        content=content[:max_chars] + (
            f"\n\n... [trimmed from {len(content)} to {max_chars} chars]"
        ),
        name=getattr(msg, "name", ""),
        tool_call_id=getattr(msg, "tool_call_id", ""),
    )


def enforce_context_budget(
    messages: list,
    *,
    max_messages: int = 30,
    max_chars_per_tool_result: int = 8000,
    tool_name_patterns: tuple[str, ...] = ("SourceScout", "EvidenceCurator", "ClaimVerifier"),
) -> list:
    """Trim a message list to stay within context budget.

    Preserves high-signal messages first, then recent messages, and prefers
    evidence / todo / memory / system context over raw tool dumps.
    """
    if not messages:
        return []

    trimmed = [_truncate_tool_message(msg, max_chars_per_tool_result) for msg in messages]
    if len(trimmed) <= max_messages:
        return trimmed

    first = trimmed[0]
    scored = [
        (_priority_for_message(msg, tool_name_patterns=tool_name_patterns), idx, msg)
        for idx, msg in enumerate(trimmed[1:], start=1)
    ]
    scored.sort(key=lambda item: (item[0], item[1]))

    keep_slots = max(0, max_messages - 1)
    keep_recent = scored[-keep_slots:] if keep_slots else []
    keep_indices = {idx for _score, idx, _msg in keep_recent}
    result = [first] + [msg for idx, msg in enumerate(trimmed[1:], start=1) if idx in keep_indices]
    logger.info("[ContextBudget] Trimmed to %d messages", len(result))
    return result


# ---------------------------------------------------------------------------
# Tool Execution Safety — wraps tool calls with error handling
# ---------------------------------------------------------------------------

async def safe_execute_tool(
    tool_name: str,
    tool_args: dict[str, Any],
    tools_by_name: dict,
    config: Any,
) -> tuple[str, bool]:
    """Execute a tool safely, returning (result_or_error, success).

    Prevents a single tool failure from crashing the research pipeline.
    """
    try:
        tool = tools_by_name.get(tool_name)
        if tool is None:
            return (
                f"Tool '{tool_name}' not found. Available: {list(tools_by_name.keys())}",
                False,
            )
        result = await tool.ainvoke(tool_args, config)
        return (str(result), True)
    except Exception as exc:
        logger.warning("[SharedMiddleware] Tool %s failed: %s", tool_name, exc)
        return (
            f"Tool '{tool_name}' error: {exc}. Try a different approach.",
            False,
        )
