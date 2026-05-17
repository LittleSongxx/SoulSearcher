"""Intercepts ask_clarification tool calls and interrupts execution.

Must be the LAST middleware in the chain so it runs after all other
processing is complete.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelResponse
from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)


class ClarificationMiddleware(AgentMiddleware):
    """Intercept ask_clarification calls and interrupt via Command(goto=END)."""

    def after_model(self, state, runtime) -> dict | None:
        messages = list(state.get("messages", []))
        if not messages:
            return None

        last = messages[-1]
        tool_calls = getattr(last, "tool_calls", None) or []
        additional = (getattr(last, "additional_kwargs", {}) or {}).get("tool_calls", [])
        all_calls = list(tool_calls) + list(additional)

        for tc in all_calls:
            name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
            if name == "ask_clarification":
                logger.info("ClarificationMiddleware: intercepting ask_clarification, interrupting")
                try:
                    from langgraph.types import Command
                    return Command(goto="__end__")
                except ImportError:
                    return {"is_complete": True, "status": "paused"}

        return None

    async def aafter_model(self, state, runtime) -> dict | None:
        return self.after_model(state, runtime)
