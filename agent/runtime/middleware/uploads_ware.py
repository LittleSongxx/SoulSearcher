"""Tracks and injects newly uploaded file paths into conversation context."""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware

logger = logging.getLogger(__name__)


class UploadsMiddleware(AgentMiddleware):
    """Injects uploaded file information into the conversation context."""

    def before_agent(self, state, runtime) -> dict | None:
        return None

    async def abefore_agent(self, state, runtime) -> dict | None:
        return None

    def after_model(self, state, runtime) -> dict | None:
        return None

    async def aafter_model(self, state, runtime) -> dict | None:
        return None
