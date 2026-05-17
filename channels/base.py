from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from .message_bus import InboundMessage, InboundMessageType, MessageBus, OutboundMessage, ResolvedAttachment

logger = logging.getLogger(__name__)


class Channel(ABC):
    def __init__(self, name: str, bus: MessageBus, config: dict[str, Any]) -> None:
        self.name = name
        self.bus = bus
        self.config = config
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def supports_streaming(self) -> bool:
        return False

    @abstractmethod
    async def start(self) -> None:
        pass

    @abstractmethod
    async def stop(self) -> None:
        pass

    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None:
        pass

    async def send_file(self, msg: OutboundMessage, attachment: ResolvedAttachment) -> bool:
        return False

    def _make_inbound(
        self,
        chat_id: str,
        user_id: str,
        text: str,
        *,
        msg_type: InboundMessageType = InboundMessageType.CHAT,
        thread_ts: str | None = None,
        topic_id: str | None = None,
        files: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> InboundMessage:
        return InboundMessage(
            channel_name=self.name,
            chat_id=chat_id,
            user_id=user_id,
            text=text,
            msg_type=msg_type,
            thread_ts=thread_ts,
            topic_id=topic_id,
            files=files or [],
            metadata=metadata or {},
        )

    async def _on_outbound(self, msg: OutboundMessage) -> None:
        if msg.channel_name != self.name:
            return
        try:
            await self.send(msg)
        except Exception:
            logger.exception("Failed to send outbound message on %s", self.name)
            return
        for attachment in msg.attachments:
            try:
                await self.send_file(msg, attachment)
            except Exception:
                logger.exception("[%s] failed to send attachment %s", self.name, attachment.filename)
