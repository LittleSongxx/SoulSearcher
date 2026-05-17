"""Middleware to inject dynamic context (memory, current date) as a <system-reminder>.

The system prompt stays static for prefix-cache reuse. Memory and current date
are injected once per conversation as a hidden HumanMessage before the first user
message. Midnight crossing triggers a lightweight date-update reminder.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"<current_date>([^<]+)</current_date>")
_DYNAMIC_CONTEXT_KEY = "dynamic_context_reminder"


def is_dynamic_context_reminder(message: Any) -> bool:
    return isinstance(message, HumanMessage) and bool(
        getattr(message, "additional_kwargs", {}).get(_DYNAMIC_CONTEXT_KEY)
    )


def _last_injected_date(messages: list) -> str | None:
    for msg in reversed(messages):
        if is_dynamic_context_reminder(msg):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            m = _DATE_RE.search(content)
            if m:
                return m.group(1)
    return None


def _is_user_target(message: Any) -> bool:
    return (
        isinstance(message, HumanMessage)
        and not is_dynamic_context_reminder(message)
        and getattr(message, "name", None) != "summary"
    )


class DynamicContextMiddleware(AgentMiddleware):
    """Inject memory and current date as <system-reminder> HumanMessages."""

    def __init__(self, agent_name: str | None = None):
        super().__init__()
        self._agent_name = agent_name

    def _build_full_reminder(self) -> str:
        import json

        from common.config import settings

        memory_context = ""
        if getattr(settings, "memory_injection_enabled", True):
            memory_context = _get_memory_context(self._agent_name)

        current_date = datetime.now().strftime("%Y-%m-%d, %A")

        lines = ["<system-reminder>"]
        if memory_context:
            lines.append(memory_context.strip())
            lines.append("")
        lines.append(f"<current_date>{current_date}</current_date>")
        lines.append("</system-reminder>")
        return "\n".join(lines)

    def _build_date_update(self) -> str:
        current_date = datetime.now().strftime("%Y-%m-%d, %A")
        return "\n".join([
            "<system-reminder>",
            f"<current_date>{current_date}</current_date>",
            "</system-reminder>",
        ])

    @staticmethod
    def _make_reminder_pair(original: HumanMessage, reminder_content: str) -> tuple[HumanMessage, HumanMessage]:
        stable_id = original.id or str(uuid.uuid4())
        reminder = HumanMessage(
            content=reminder_content,
            id=stable_id,
            additional_kwargs={"hide_from_ui": True, _DYNAMIC_CONTEXT_KEY: True},
        )
        user_msg = HumanMessage(
            content=original.content,
            id=f"{stable_id}__user",
            name=original.name,
            additional_kwargs=original.additional_kwargs,
        )
        return reminder, user_msg

    def _inject(self, state: dict) -> dict | None:
        messages = list(state.get("messages", []))
        if not messages:
            return None

        current_date = datetime.now().strftime("%Y-%m-%d, %A")
        last_date = _last_injected_date(messages)

        if last_date is None:
            first_idx = next((i for i, m in enumerate(messages) if _is_user_target(m)), None)
            if first_idx is None:
                return None
            full = self._build_full_reminder()
            logger.info("DynamicContext: injecting full reminder into first HumanMessage")
            reminder_msg, user_msg = self._make_reminder_pair(messages[first_idx], full)
            return {"messages": [reminder_msg, user_msg]}

        if last_date == current_date:
            return None

        last_idx = next((i for i in reversed(range(len(messages))) if _is_user_target(messages[i])), None)
        if last_idx is None:
            return None
        reminder_msg, user_msg = self._make_reminder_pair(messages[last_idx], self._build_date_update())
        logger.info("DynamicContext: midnight crossing — injecting date update")
        return {"messages": [reminder_msg, user_msg]}

    def before_agent(self, state, runtime) -> dict | None:
        return self._inject(state)

    async def abefore_agent(self, state, runtime) -> dict | None:
        return self._inject(state)


def _get_memory_context(agent_name: str | None = None) -> str:
    """Load memory context for injection. Returns empty string on any failure."""
    try:
        from agent.runtime.memory import format_memory_for_injection, get_memory_data
        from agent.runtime.user_context import get_effective_user_id

        data = get_memory_data(agent_name, user_id=get_effective_user_id())
        return format_memory_for_injection(data, max_tokens=2000)
    except Exception:
        logger.debug("Failed to load memory context", exc_info=True)
        return ""
