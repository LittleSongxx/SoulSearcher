"""Queues conversation turns for async memory update."""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage

logger = logging.getLogger(__name__)


class MemoryMiddleware(AgentMiddleware):
    """After each complete exchange, enqueue the conversation for memory update."""

    def __init__(self, agent_name: str | None = None):
        super().__init__()
        self._agent_name = agent_name

    def _enqueue(self, state: dict) -> None:
        messages = state.get("messages", [])
        if len(messages) < 2:
            return

        thread_id = state.get("thread_id", "unknown")

        # Filter to relevant messages: user inputs + final AI responses
        relevant: list = []
        for msg in messages[-20:]:
            if isinstance(msg, HumanMessage) and not msg.additional_kwargs.get("hide_from_ui"):
                relevant.append(msg)
            elif isinstance(msg, AIMessage) and not (msg.tool_calls or msg.additional_kwargs.get("tool_calls")):
                if msg.content:
                    relevant.append(msg)

        if len(relevant) < 2:
            return

        try:
            from agent.runtime.memory.queue import get_memory_queue
            from agent.runtime.user_context import get_effective_user_id

            queue = get_memory_queue()
            queue.add(
                thread_id=thread_id,
                messages=relevant,
                agent_name=self._agent_name,
                user_id=get_effective_user_id(),
            )
            logger.debug("MemoryMiddleware: queued conversation for thread %s", thread_id)
        except Exception:
            logger.debug("MemoryMiddleware: failed to enqueue", exc_info=True)

    def after_model(self, state, runtime) -> dict | None:
        self._enqueue(state)
        return None

    async def aafter_model(self, state, runtime) -> dict | None:
        self._enqueue(state)
        return None
