"""Detect and break repetitive tool-call loops.

When the same tool is called with identical arguments more than N times
in a sliding window, this middleware strips tool_calls and forces a final
text answer.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)

DEFAULT_MAX_REPEATS = 3
DEFAULT_WINDOW_SIZE = 8


def _tool_call_fingerprint(tc: Any) -> str:
    """Create a stable fingerprint for a tool call (name + args)."""
    name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
    args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
    payload = json.dumps({"n": name, "a": args}, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


class LoopDetectionMiddleware(AgentMiddleware):
    """Break tool-call loops by stripping tool_calls when repetition detected."""

    def __init__(self, max_repeats: int = DEFAULT_MAX_REPEATS, window: int = DEFAULT_WINDOW_SIZE):
        super().__init__()
        self.max_repeats = max_repeats
        self.window = window

    def _detect_loop(self, state: dict) -> bool:
        messages = state.get("messages", [])
        if len(messages) < self.window:
            return False

        recent = messages[-self.window:]
        fingerprints: list[str] = []
        for msg in recent:
            if isinstance(msg, AIMessage):
                for tc in (msg.tool_calls or []):
                    fingerprints.append(_tool_call_fingerprint(tc))

        if not fingerprints:
            return False

        from collections import Counter
        counts = Counter(fingerprints)
        return any(c > self.max_repeats for c in counts.values())

    def after_model(self, state, runtime) -> dict | None:
        if not self._detect_loop(state):
            return None

        messages = list(state.get("messages", []))
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and (msg.tool_calls or msg.additional_kwargs.get("tool_calls")):
                logger.warning("[LOOP DETECTED] Stripping tool_calls from looping message")
                return {
                    "messages": [
                        AIMessage(
                            content=(
                                msg.content or ""
                            ) + "\n\n[LOOP DETECTED] I seem to be repeating the same tool calls. "
                            "Let me synthesize what I know instead.",
                            id=msg.id,
                        )
                    ]
                }
        return None

    async def aafter_model(self, state, runtime) -> dict | None:
        return self.after_model(state, runtime)
