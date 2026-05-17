from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class InboundMessageType(str, Enum):
    CHAT = "chat"
    COMMAND = "command"


@dataclass(slots=True)
class InboundMessage:
    channel_name: str
    chat_id: str
    user_id: str
    text: str
    msg_type: InboundMessageType = InboundMessageType.CHAT
    thread_ts: str | None = None
    topic_id: str | None = None
    files: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ResolvedAttachment:
    actual_path: Path
    filename: str
    mime_type: str = "application/octet-stream"
    is_image: bool = False
    size: int = 0


@dataclass(slots=True)
class OutboundMessage:
    channel_name: str
    chat_id: str
    text: str
    thread_ts: str | None = None
    is_final: bool = False
    stream_id: str | None = None
    attachments: list[ResolvedAttachment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


InboundHandler = Callable[[InboundMessage], Awaitable[None]]
OutboundHandler = Callable[[OutboundMessage], Awaitable[None]]


class MessageBus:
    def __init__(self) -> None:
        self._inbound: set[InboundHandler] = set()
        self._outbound: set[OutboundHandler] = set()

    def subscribe_inbound(self, handler: InboundHandler) -> None:
        self._inbound.add(handler)

    def unsubscribe_inbound(self, handler: InboundHandler) -> None:
        self._inbound.discard(handler)

    def subscribe_outbound(self, handler: OutboundHandler) -> None:
        self._outbound.add(handler)

    def unsubscribe_outbound(self, handler: OutboundHandler) -> None:
        self._outbound.discard(handler)

    async def publish_inbound(self, message: InboundMessage) -> None:
        await asyncio.gather(
            *(handler(message) for handler in list(self._inbound)),
            return_exceptions=False,
        )

    async def publish_outbound(self, message: OutboundMessage) -> None:
        await asyncio.gather(
            *(handler(message) for handler in list(self._outbound)),
            return_exceptions=False,
        )
