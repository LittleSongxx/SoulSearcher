from __future__ import annotations

import contextlib
import json
from typing import Any

from .types import SseEvent


def parse_sse_frame(frame: str) -> SseEvent | None:
    text = str(frame or "")
    if not text.strip():
        return None

    event_name = ""
    id_text = ""
    data_lines: list[str] = []

    for raw_line in text.split("\n"):
        line = raw_line.rstrip("\n").rstrip("\r")
        if not line:
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
            continue
        if line.startswith("id:"):
            id_text = line[len("id:") :].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())
            continue

    if not data_lines:
        return None

    data_text = "\n".join(data_lines)
    try:
        parsed: Any = json.loads(data_text)
    except Exception:
        return None

    out: SseEvent = {"data": parsed}
    if event_name:
        out["event"] = event_name
    if id_text:
        with contextlib.suppress(Exception):
            out["id"] = int(id_text)
    return out


def iter_sse_events_from_text(text: str) -> list[SseEvent]:
    """
    Best-effort parse SSE events from an accumulated text buffer.

    This is mostly useful for unit tests and small payloads. Streaming parsing
    should be done incrementally (see WeaverClient.research_sse()).
    """
    buf = (text or "").replace("\r\n", "\n")
    frames = buf.split("\n\n")
    events: list[SseEvent] = []
    for frame in frames:
        parsed = parse_sse_frame(frame)
        if parsed:
            events.append(parsed)
    return events
