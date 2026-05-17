"""Pre-tool-call authorization via pluggable guardrail providers.

Supports built-in allowlist and custom OAP policy providers.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)


class GuardrailProvider(ABC):
    """Protocol for tool-call authorization checks."""

    @abstractmethod
    def check(self, tool_name: str, tool_args: dict[str, Any], thread_id: str) -> tuple[bool, str]:
        """Return (allowed, reason)."""
        ...


class AllowlistProvider(GuardrailProvider):
    """Simple allowlist/denylist guardrail provider."""

    def __init__(
        self,
        allowlist: set[str] | None = None,
        denylist: set[str] | None = None,
    ):
        self.allowlist = allowlist
        self.denylist = denylist or set()

    def check(self, tool_name: str, tool_args: dict[str, Any], thread_id: str) -> tuple[bool, str]:
        if tool_name in self.denylist:
            return False, f"Tool '{tool_name}' is denied by policy"
        if self.allowlist is not None and tool_name not in self.allowlist:
            return False, f"Tool '{tool_name}' is not in the allowed list"
        return True, "allowed"


class GuardrailMiddleware(AgentMiddleware):
    """Pre-tool-call authorization middleware."""

    def __init__(self, provider: GuardrailProvider | None = None):
        super().__init__()
        self._provider = provider

    def _check_tool_calls(self, state: dict) -> dict | None:
        if self._provider is None:
            return None

        messages = state.get("messages", [])
        if not messages:
            return None

        last = messages[-1]
        tool_calls = getattr(last, "tool_calls", None) or []
        additional = (getattr(last, "additional_kwargs", {}) or {}).get("tool_calls", [])
        all_calls = list(tool_calls) + list(additional)

        thread_id = state.get("thread_id", "unknown")
        error_messages = []

        for tc in all_calls:
            name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
            args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
            allowed, reason = self._provider.check(name, args, thread_id)
            if not allowed:
                logger.warning("[guardrail] Denied %s: %s", name, reason)
                tc_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                error_messages.append(ToolMessage(
                    content=f"Tool '{name}' denied: {reason}",
                    tool_call_id=tc_id,
                ))

        if error_messages:
            return {"messages": error_messages}
        return None

    def after_model(self, state, runtime) -> dict | None:
        return self._check_tool_calls(state)

    async def aafter_model(self, state, runtime) -> dict | None:
        return self._check_tool_calls(state)
