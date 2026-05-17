"""Context summarization middleware.

When the conversation approaches token limits, summarizes older messages
while preserving the most recent ones.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware

logger = logging.getLogger(__name__)

# Approximate: 4 chars per token for English, adjust as needed
CHARS_PER_TOKEN = 4
DEFAULT_MAX_INPUT_TOKENS = 64000
DEFAULT_TRIGGER_FRACTION = 0.75
DEFAULT_KEEP_LAST_MESSAGES = 15


class SummarizationMiddleware(AgentMiddleware):
    """Summarize older messages when approaching token limits."""

    def __init__(
        self,
        max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS,
        trigger_fraction: float = DEFAULT_TRIGGER_FRACTION,
        keep_last: int = DEFAULT_KEEP_LAST_MESSAGES,
    ):
        super().__init__()
        self.max_input_tokens = max_input_tokens
        self.trigger_fraction = trigger_fraction
        self.keep_last = keep_last

    def _estimate_tokens(self, messages: list) -> int:
        total = 0
        for msg in messages:
            content = getattr(msg, "content", "")
            if isinstance(content, str):
                total += max(1, len(content) // CHARS_PER_TOKEN)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, str):
                        total += max(1, len(block) // CHARS_PER_TOKEN)
                    elif isinstance(block, dict):
                        text = block.get("text", "") or ""
                        total += max(1, len(text) // CHARS_PER_TOKEN)
        return total

    def _should_summarize(self, state: dict) -> bool:
        messages = state.get("messages", [])
        if len(messages) <= self.keep_last + 5:
            return False
        estimated = self._estimate_tokens(messages)
        return estimated > self.max_input_tokens * self.trigger_fraction

    def before_agent(self, state, runtime) -> dict | None:
        if not self._should_summarize(state):
            return None

        messages = list(state.get("messages", []))
        to_summarize = messages[: -self.keep_last]
        recent = messages[-self.keep_last:]

        logger.info("SummarizationMiddleware: summarizing %d messages, keeping %d", len(to_summarize), len(recent))

        from agent.core.message_utils import summarize_messages
        summary = summarize_messages(to_summarize)
        return {"messages": [summary] + recent}

    async def abefore_agent(self, state, runtime) -> dict | None:
        return self.before_agent(state, runtime)
