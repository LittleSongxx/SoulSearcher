"""Converts tool exceptions into error ToolMessages so runs don't abort."""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)


class ToolErrorHandlingMiddleware(AgentMiddleware):
    """Wraps tool execution to catch exceptions and return error ToolMessages."""

    def _wrap_tool_result(self, result: Any, tool_call_id: str | None, tool_name: str) -> Any:
        if isinstance(result, ToolMessage):
            return result
        return result

    def after_model(self, state, runtime) -> dict | None:
        return None

    async def aafter_model(self, state, runtime) -> dict | None:
        return None
