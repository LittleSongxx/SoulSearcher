"""Shared graph-middleware functions — Anthropic Agent SDK hooks pattern.

Instead of duplicating cross-cutting concerns across graph nodes (supervisor,
researcher, etc.), each concern lives as a standalone function that both the
LangGraph graph nodes AND the deer-flow middleware chain can import.

This follows Anthropic's Agent SDK hooks model:
  - PreToolUse / PostToolUse  →  before_tool / after_tool
  - SessionStart / SessionEnd  →  on_session_start / on_session_end
  - Stop                       →  on_loop_iteration
"""

from __future__ import annotations

import logging
import time
from typing import Any

from agent.core.configuration import ResearchConfiguration

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Loop Safety — prevents infinite ReAct loops
# ---------------------------------------------------------------------------

def check_loop(messages: list, max_repetitions: int = 3) -> tuple[bool, str]:
    """Check whether the last few LLM responses indicate a loop.

    Returns (is_looping, hint_message).  Call this near the top of every
    tool-calling loop iteration (supervisor, researcher, etc.).
    """
    from agent.core.middleware import get_loop_detector

    detector = get_loop_detector()
    recent_content = "\n".join([
        str(getattr(m, "content", ""))[:200]
        for m in messages[-5:]
        if hasattr(m, "content") and getattr(m, "content", None)
    ])
    if detector.check(recent_content):
        logger.warning("[SharedMiddleware] Loop detected")
        return True, detector.get_hint()
    return False, ""


# ---------------------------------------------------------------------------
# Token Usage — per-phase cost tracking
# ---------------------------------------------------------------------------

def record_token_usage(
    phase: str,
    response: Any,
) -> None:
    """Record token usage from an LLM response for cost attribution."""
    from agent.core.middleware import get_token_tracker

    usage = getattr(response, "usage_metadata", None) or {}
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    if input_tokens or output_tokens:
        get_token_tracker().record(phase, input_tokens, output_tokens)


def get_token_summary() -> dict[str, Any]:
    """Get current token usage summary across all phases."""
    from agent.core.middleware import get_token_tracker

    return get_token_tracker().get_summary()


# ---------------------------------------------------------------------------
# Context Budget — summarization-aware trimming
# ---------------------------------------------------------------------------

def enforce_context_budget(
    messages: list,
    *,
    max_messages: int = 30,
    max_chars_per_tool_result: int = 8000,
    tool_name_patterns: tuple[str, ...] = ("ConductResearch",),
) -> list:
    """Trim a message list to stay within context budget.

    Similar to the Claude Code sub-agent principle: only the summary of
    sub-agent work enters the main conversation.
    """
    result: list = []
    for msg in messages:
        name = getattr(msg, "name", "")
        if name in tool_name_patterns:
            content = getattr(msg, "content", "") or ""
            if len(content) > max_chars_per_tool_result:
                from langchain_core.messages import ToolMessage

                truncated = content[:max_chars_per_tool_result] + (
                    f"\n\n... [trimmed from {len(content)} to "
                    f"{max_chars_per_tool_result} chars]"
                )
                result.append(ToolMessage(
                    content=truncated,
                    name=name,
                    tool_call_id=getattr(msg, "tool_call_id", ""),
                ))
                continue
        result.append(msg)

    if len(result) > max_messages:
        # Keep first (system/context) and most recent
        result = [result[0]] + result[-(max_messages - 1):]
        logger.info("[ContextBudget] Trimmed to %d messages", len(result))

    return result


# ---------------------------------------------------------------------------
# Memory Update — fire-and-forget after research completes
# ---------------------------------------------------------------------------

async def record_research_to_memory(
    user_id: str,
    query: str,
    findings: str,
    facts: list[str] | None = None,
) -> None:
    """Record completed research to long-term memory (non-blocking)."""
    try:
        from agent.runtime.memory import get_memory_system

        await get_memory_system().record_research(
            user_id=user_id,
            query=query,
            findings=findings[:5000],
            facts=facts or [],
        )
    except Exception as e:
        logger.debug("[SharedMiddleware] Memory update skipped: %s", e)


# ---------------------------------------------------------------------------
# Dynamic Context Injection — date + memory as <system-reminder>
# ---------------------------------------------------------------------------

def build_dynamic_context_reminder(
    agent_name: str | None = None,
    user_id: str = "default",
) -> str:
    """Build a <system-reminder> block with current date and relevant memories.

    Follows the Anthropic pattern of keeping the system prompt static for
    prefix-cache reuse while injecting dynamic content as hidden messages.
    """
    from datetime import datetime

    lines = ["<system-reminder>"]
    current_date = datetime.now().strftime("%Y-%m-%d, %A")
    lines.append(f"<current_date>{current_date}</current_date>")

    try:
        from agent.runtime.memory import format_memory_for_injection, get_memory_data
        data = get_memory_data(agent_name, user_id=user_id)
        memory_text = format_memory_for_injection(data, max_tokens=2000)
        if memory_text:
            lines.append("")
            lines.append(memory_text.strip())
    except Exception:
        pass

    lines.append("</system-reminder>")
    return "\n".join(lines)


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
