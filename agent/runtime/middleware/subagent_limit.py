"""Truncates excess parallel task calls to enforce MAX_CONCURRENT_SUBAGENTS."""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)


class SubagentLimitMiddleware(AgentMiddleware):
    """Enforce max concurrent subagent limit by truncating excess task calls."""

    def __init__(self, max_concurrent: int = 3):
        super().__init__()
        self.max_concurrent = max_concurrent

    def _enforce(self, state: dict) -> dict | None:
        messages = list(state.get("messages", []))
        if not messages:
            return None

        last = messages[-1]
        if not isinstance(last, AIMessage):
            return None

        tool_calls = list(last.tool_calls or [])
        task_calls = [tc for tc in tool_calls if (tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")) == "task"]

        if len(task_calls) <= self.max_concurrent:
            return None

        logger.warning(
            "SubagentLimit: truncating %d task calls to %d",
            len(task_calls),
            self.max_concurrent,
        )

        truncated = [tc for tc in tool_calls if (tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")) != "task"]
        truncated += task_calls[: self.max_concurrent]

        return {
            "messages": [
                AIMessage(
                    content=last.content,
                    tool_calls=truncated,
                    id=last.id,
                    additional_kwargs=last.additional_kwargs,
                )
            ]
        }

    def after_model(self, state, runtime) -> dict | None:
        return self._enforce(state)

    async def aafter_model(self, state, runtime) -> dict | None:
        return self._enforce(state)
