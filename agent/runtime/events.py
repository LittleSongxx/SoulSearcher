from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage


def legacy_event(event_type: str, data: Any) -> str:
    """Return Weaver's legacy stream line consumed by the SSE translator."""
    return "0:" + json.dumps({"type": event_type, "data": data}, ensure_ascii=False) + "\n"


def extract_last_ai_text(result: Any) -> str:
    """Extract a final assistant text from a LangChain/LangGraph result."""
    messages = []
    if isinstance(result, dict):
        messages = result.get("messages") or []
    elif isinstance(result, list):
        messages = result

    for msg in reversed(messages):
        if isinstance(msg, AIMessage) or getattr(msg, "type", None) == "ai":
            content = getattr(msg, "content", "")
            return _content_to_text(content)

    return _content_to_text(getattr(result, "content", result))


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(part for part in parts if part)
    if content is None:
        return ""
    return str(content)
