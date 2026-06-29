from __future__ import annotations

import json
from typing import Any

from common.research_events import build_research_run_event
from common.sse import format_sse_event


def data_stream_line_to_payload(line: str, *, seq: int) -> dict[str, Any] | None:
    """
    Decode Weaver's internal data-stream envelope (`0:{json}\\n`).

    Internal format: `0:{"type": "...", "data": {...}}\\n`
    """
    if not isinstance(line, str) or not line.startswith("0:"):
        return None

    payload_text = line[2:].strip()
    if not payload_text:
        return None

    try:
        payload: Any = json.loads(payload_text)
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None

    event_type = payload.get("type")
    if not isinstance(event_type, str) or not event_type.strip():
        return None

    payload.setdefault(
        "research_event",
        build_research_run_event(event_type, payload.get("data"), seq=seq),
    )
    return payload


def translate_data_stream_line_to_sse(line: str, *, seq: int) -> str:
    """
    Translate Weaver's internal data-stream envelope (`0:{json}\\n`) into SSE.

    Internal format: `0:{"type": "...", "data": {...}}\\n`
    SSE format:      `id: <seq>\\nevent: <type>\\ndata: <json>\\n\\n`
    """
    payload = data_stream_line_to_payload(line, seq=seq)
    if not payload:
        return ""

    event_type = payload.get("type")
    if not isinstance(event_type, str) or not event_type.strip():
        return ""

    return format_sse_event(event=event_type, data=payload, event_id=seq)
