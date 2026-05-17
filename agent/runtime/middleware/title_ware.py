"""Auto-generates thread title after the first complete exchange."""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage

logger = logging.getLogger(__name__)

MAX_TITLE_WORDS = 8


class TitleMiddleware(AgentMiddleware):
    """Generate a thread title from the first user message + AI response."""

    def __init__(self, max_words: int = MAX_TITLE_WORDS):
        super().__init__()
        self.max_words = max_words

    def _maybe_generate(self, state: dict) -> dict | None:
        if state.get("title"):
            return None

        messages = state.get("messages", [])
        user_texts = [m.content for m in messages if isinstance(m, HumanMessage)
                      and not m.additional_kwargs.get("hide_from_ui")
                      and isinstance(m.content, str)]
        if not user_texts:
            return None

        first_query = user_texts[0].strip()
        words = first_query.split()
        if len(words) <= self.max_words:
            title = first_query[:120]
        else:
            title = " ".join(words[: self.max_words]) + "..."

        logger.info("TitleMiddleware: generated title %r", title)
        return {"title": title}

    def after_model(self, state, runtime) -> dict | None:
        return self._maybe_generate(state)

    async def aafter_model(self, state, runtime) -> dict | None:
        return self._maybe_generate(state)
