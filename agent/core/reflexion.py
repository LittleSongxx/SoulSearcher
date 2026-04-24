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
from typing import Any, Dict, Optional

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate

from common.config import settings

logger = logging.getLogger(__name__)

REFLEXION_PROMPT = """You are a self-reflection assistant. Review the agent's last action round and evaluate progress toward the user's goal.

## User Goal
{user_goal}

## Last Round Results
{last_round_summary}

## Instructions
Provide a brief self-reflection (3-5 sentences max):
1. What was achieved in this round?
2. What gaps remain toward the user's goal?
3. What should the agent do next?

Be concise and actionable. If the goal appears fully achieved, say "Goal achieved."
"""


def build_reflexion_message(
    user_goal: str,
    last_messages: list,
    llm: Any,
    config: Optional[Dict] = None,
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

    # Build a compact summary of the last round
    summary_parts = []
    for msg in last_messages[-6:]:  # Last 6 messages max
        role = type(msg).__name__
        content = getattr(msg, "content", "") or ""
        if len(content) > 300:
            content = content[:300] + "..."
        summary_parts.append(f"[{role}] {content}")

    last_round_summary = "\n".join(summary_parts)

    prompt = ChatPromptTemplate.from_messages([
        ("user", REFLEXION_PROMPT),
    ])

    try:
        msg = prompt.format_messages(
            user_goal=user_goal[:500],
            last_round_summary=last_round_summary[:1500],
        )
        response = llm.invoke(msg, config=config or {})
        feedback = getattr(response, "content", "") or ""

        if not feedback.strip():
            return None

        # If goal is achieved, no need for further reflection
        if "goal achieved" in feedback.lower()[:50]:
            logger.debug("[reflexion] Goal achieved, skipping feedback injection")
            return None

        logger.info(f"[reflexion] Generated feedback ({len(feedback)} chars)")
        return SystemMessage(
            content=f"[Self-Reflection] {feedback.strip()}"
        )

    except Exception as e:
        logger.warning(f"[reflexion] Failed to generate reflection: {e}")
        return None


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
