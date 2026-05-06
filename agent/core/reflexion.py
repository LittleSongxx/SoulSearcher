"""
Agent Reflexion Module.

Implements self-reflection after tool-calling rounds, inspired by the
Reflexion paper (Shinn et al., 2023). After the agent completes a round
of tool calls, this module evaluates the results and generates verbal
feedback to guide the next iteration.

Key design choices:
- Lightweight prompt (~200 tokens) to avoid large overhead
- Uses the same LLM as the agent (no extra model required)
- Generates structured feedback: {achieved, gaps, next_action}
- Feedback is injected as a SystemMessage into the next round
"""

import logging
import re
from typing import Any, Optional

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate

from common.config import settings

logger = logging.getLogger(__name__)

REFLEXION_PROMPT = """You are a self-reflection assistant. Review the current progress snapshot and evaluate progress toward the user's goal.

## User Goal
{user_goal}

## Current Progress Snapshot
{progress_summary}

## Instructions
Provide a brief self-reflection (3-5 sentences max) using this format:
ACHIEVED: ...
GAPS: ...
NEXT_ACTION: ...

Be concise and actionable. If the goal appears fully achieved, reply exactly "Goal achieved."
"""


def _truncate_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _message_fingerprint(message: BaseMessage) -> tuple:
    return (
        type(message).__name__,
        getattr(message, "content", "") or "",
        str(getattr(message, "name", "") or ""),
        repr(getattr(message, "tool_calls", None)),
        str(getattr(message, "tool_call_id", "") or ""),
    )


def merge_reflexion_context(
    existing_messages: Optional[list[BaseMessage]],
    new_messages: Optional[list[BaseMessage]],
) -> list[BaseMessage]:
    merged = list(existing_messages or [])
    incoming = list(new_messages or [])
    if not incoming:
        return merged
    if not merged:
        return incoming

    existing_fp = [_message_fingerprint(msg) for msg in merged]
    incoming_fp = [_message_fingerprint(msg) for msg in incoming]
    overlap = 0
    max_overlap = min(len(existing_fp), len(incoming_fp))
    for size in range(max_overlap, 0, -1):
        if existing_fp[-size:] == incoming_fp[:size]:
            overlap = size
            break

    return merged + incoming[overlap:]


def summarize_reflexion_messages(last_messages: list[BaseMessage]) -> str:
    summary_parts = []
    for msg in list(last_messages or [])[-8:]:
        role = type(msg).__name__
        content = _truncate_text(getattr(msg, "content", "") or "", 320)
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            content = f"{content}\nTOOL_CALLS: {_truncate_text(repr(tool_calls), 320)}".strip()
        summary_parts.append(f"[{role}] {content}".strip())
    return "\n".join(part for part in summary_parts if part)


def generate_reflexion_feedback(
    user_goal: str,
    progress_summary: str,
    llm: Any,
    config: Optional[dict] = None,
) -> Optional[str]:
    if not settings.agent_reflexion_enabled:
        return None

    progress_summary = str(progress_summary or "").strip()
    if not progress_summary:
        return None

    prompt = ChatPromptTemplate.from_messages(
        [
            ("user", REFLEXION_PROMPT),
        ]
    )

    try:
        msg = prompt.format_messages(
            user_goal=_truncate_text(user_goal, 700),
            progress_summary=_truncate_text(progress_summary, 2200),
        )
        response = llm.invoke(msg, config=config or {})
        feedback = str(getattr(response, "content", "") or "").strip()
        if not feedback:
            return None
        if feedback.lower().startswith("goal achieved"):
            logger.debug("[reflexion] Goal achieved, skipping feedback injection")
            return None
        logger.info(f"[reflexion] Generated feedback ({len(feedback)} chars)")
        return feedback
    except Exception as e:
        logger.warning(f"[reflexion] Failed to generate reflection: {e}")
        return None


def extract_reflexion_focus(feedback: str, limit: int = 3) -> list[str]:
    text = str(feedback or "").strip()
    if not text:
        return []

    lines = []
    for raw in text.splitlines():
        line = raw.strip().strip("-*• ")
        if not line:
            continue
        lowered = line.lower()
        if lowered.startswith("achieved:"):
            continue
        if ":" in line:
            _, line = line.split(":", 1)
        elif "：" in line:
            _, line = line.split("：", 1)
        line = line.strip()
        if line:
            lines.append(line)

    if not lines:
        lines = [text]

    focus: list[str] = []
    seen = set()
    for line in lines:
        parts = [
            chunk.strip(" -*•")
            for chunk in re.split(r"[;；\n]+|\s+(?:and|then)\s+|[，,]+", line)
        ]
        for part in parts:
            normalized = re.sub(r"\s+", " ", part).strip()
            if len(normalized) < 4:
                continue
            if normalized.lower() in {"gaps", "next_action", "next action"}:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            focus.append(normalized[:120])
            if len(focus) >= limit:
                return focus
    return focus


def build_reflexion_message(
    user_goal: str,
    last_messages: list,
    llm: Any,
    config: Optional[dict] = None,
) -> Optional[SystemMessage]:
    """
    Generate a reflexion message based on the last round of tool calls.

    Args:
        user_goal: The user's original query/goal
        last_messages: Recent messages from the last tool-calling round
        llm: The LLM instance to use for reflection
        config: Optional LangChain config

    Returns:
        A SystemMessage with reflection feedback, or None if reflection
        is not needed (e.g., goal already achieved).
    """
    if not settings.agent_reflexion_enabled:
        return None

    if not last_messages:
        return None

    feedback = generate_reflexion_feedback(
        user_goal=user_goal,
        progress_summary=summarize_reflexion_messages(last_messages),
        llm=llm,
        config=config,
    )
    if feedback is None:
        return None

    return SystemMessage(content=f"[Self-Reflection] {feedback}")


def should_reflect(round_num: int, total_messages: int) -> bool:
    """
    Determine whether reflection should run for this round.

    Reflection is skipped for:
    - The first round (not enough context yet)
    - Rounds beyond max_rounds
    - When reflexion is disabled
    """
    if not settings.agent_reflexion_enabled:
        return False
    max_rounds = int(getattr(settings, "agent_reflexion_max_rounds", 2))
    # Reflect after at least 1 round, up to max_rounds
    if round_num < 1:
        return False
    if round_num > max_rounds:
        return False
    # Only reflect if there's meaningful history
    if total_messages < 4:
        return False
    return True
