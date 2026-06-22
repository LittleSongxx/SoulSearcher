from __future__ import annotations

import json
from typing import Any

from common.research_events import build_research_run_event
from common.sse import format_sse_event


def translate_data_stream_line_to_sse(line: str, *, seq: int) -> str:
    """
    Translate Weaver's internal data-stream envelope (`0:{json}\\n`) into SSE.

    Internal format: `0:{"type": "...", "data": {...}}\\n`
    SSE format:      `id: <seq>\\nevent: <type>\\ndata: <json>\\n\\n`
    """
    if not isinstance(line, str) or not line.startswith("0:"):
        return ""

    payload_text = line[2:].strip()
    if not payload_text:
        return ""

    try:
        payload: Any = json.loads(payload_text)
    except json.JSONDecodeError:
        return ""

    if not isinstance(payload, dict):
        return ""

    event_type = payload.get("type")
    if not isinstance(event_type, str) or not event_type.strip():
        return ""

    payload.setdefault(
        "research_event",
        build_research_run_event(event_type, payload.get("data"), seq=seq),
    )

    return format_sse_event(event=event_type, data=payload, event_id=seq)
