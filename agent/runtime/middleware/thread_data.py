"""Creates per-thread isolation directories and ensures thread_id in state."""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware

logger = logging.getLogger(__name__)


class ThreadDataMiddleware(AgentMiddleware):
    """Ensures thread_id and user_id are available in state."""

    def __init__(self, thread_id: str | None = None, user_id: str | None = None):
        super().__init__()
        self._thread_id = thread_id
        self._user_id = user_id

    def before_agent(self, state, runtime) -> dict | None:
        updates: dict[str, Any] = {}
        if self._thread_id and not state.get("thread_id"):
            updates["thread_id"] = self._thread_id
        if self._user_id and not state.get("user_id"):
            updates["user_id"] = self._user_id
        updates["thread_data"] = state.get("thread_data", {})
        return updates if updates else None

    async def abefore_agent(self, state, runtime) -> dict | None:
        return self.before_agent(state, runtime)
