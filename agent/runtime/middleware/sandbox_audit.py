"""Audits sandbox shell/file operations for security logging."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from langchain.agents.middleware import AgentMiddleware

logger = logging.getLogger(__name__)

AUDITED_TOOLS = {"bash", "safe_bash", "sandbox_execute_command", "sandbox_write_file",
                  "sandbox_update_file", "sandbox_create_file", "file_write", "file_edit"}


class SandboxAuditMiddleware(AgentMiddleware):
    """Logs sandboxed operations for security auditing."""

    def _audit_tool_calls(self, state: dict) -> None:
        messages = state.get("messages", [])
        if not messages:
            return
        last = messages[-1]
        tool_calls = getattr(last, "tool_calls", None) or []
        if not tool_calls:
            additional = getattr(last, "additional_kwargs", {}) or {}
            tool_calls = additional.get("tool_calls", [])

        for tc in tool_calls:
            name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
            if name in AUDITED_TOOLS:
                logger.info(
                    "[sandbox_audit] tool=%s thread=%s time=%s",
                    name,
                    state.get("thread_id", "unknown"),
                    datetime.utcnow().isoformat(),
                )

    def after_model(self, state, runtime) -> dict | None:
        self._audit_tool_calls(state)
        return None

    async def aafter_model(self, state, runtime) -> dict | None:
        self._audit_tool_calls(state)
        return None
