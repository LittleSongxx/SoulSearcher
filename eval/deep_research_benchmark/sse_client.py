from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, Optional

from eval.deep_research_benchmark.schemas import SSEEvent


def parse_sse_frame(frame: str) -> Optional[SSEEvent]:
    if not isinstance(frame, str) or not frame.strip():
        return None

    event_name = ""
    event_id = None
    data_lines: list[str] = []
    for raw_line in frame.split("\n"):
        line = raw_line.rstrip("\r")
        if not line or line.startswith(":"):
            continue
        if line.startswith("id:"):
            event_id = line[len("id:") :].strip() or None
            continue
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())

    if not event_name:
        return None

    raw_data = "\n".join(data_lines).strip()
    data: Any = None
    if raw_data:
        try:
            data = json.loads(raw_data)
        except json.JSONDecodeError:
            data = raw_data

    if isinstance(data, dict) and "type" in data and "data" in data:
        nested_type = data.get("type")
        if isinstance(nested_type, str) and nested_type.strip():
            event_name = nested_type.strip()
        data = data.get("data")

    return SSEEvent(event=event_name, data=data, event_id=event_id)


async def iter_sse_events(
    text_stream: AsyncIterator[str],
    *,
    chunk_timeout_s: float = 120.0,
) -> AsyncIterator[SSEEvent]:
    buffer = ""
    iterator = text_stream.__aiter__()
    while True:
        try:
            chunk = await asyncio.wait_for(iterator.__anext__(), timeout=chunk_timeout_s)
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError:
            yield SSEEvent(event="stream_timeout", data={"timeout_s": chunk_timeout_s})
            break

        if not chunk:
            continue
        buffer += chunk
        buffer = buffer.replace("\r\n", "\n")

        while "\n\n" in buffer:
            frame, buffer = buffer.split("\n\n", 1)
            event = parse_sse_frame(frame)
            if event is not None:
                yield event

    tail = buffer.strip()
    if tail:
        event = parse_sse_frame(tail)
        if event is not None:
            yield event
