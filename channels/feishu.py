"""Feishu/Lark channel — WebSocket long connection, card streaming, reactions.

No public IP required — uses lark-oapi WebSocket client.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

from channels.base import Channel
from channels.commands import KNOWN_CHANNEL_COMMANDS
from channels.message_bus import InboundMessageType, MessageBus, OutboundMessage

logger = logging.getLogger(__name__)


def _is_feishu_command(text: str) -> bool:
    if not text.startswith("/"):
        return False
    return text.split(maxsplit=1)[0].lower() in KNOWN_CHANNEL_COMMANDS


class FeishuChannel(Channel):
    """Feishu/Lark IM channel using lark-oapi WebSocket client.

    Flow:
    1. User sends message → bot adds "OK" emoji reaction
    2. Bot replies in thread: "Working on it..."
    3. Agent processes → streaming card updates (patch same card)
    4. Bot adds "DONE" reaction when complete
    """

    def __init__(self, bus: MessageBus, config: dict[str, Any]) -> None:
        super().__init__(name="feishu", bus=bus, config=config)
        self._thread: threading.Thread | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._api_client = None
        self._lark = None
        self._CreateMessageRequest = None
        self._CreateMessageRequestBody = None
        self._ReplyMessageRequest = None
        self._ReplyMessageRequestBody = None
        self._PatchMessageRequest = None
        self._PatchMessageRequestBody = None
        self._CreateMessageReactionRequest = None
        self._CreateMessageReactionRequestBody = None
        self._Emoji = None
        self._running_cards: dict[str, str] = {}
        self._running_card_tasks: dict[str, asyncio.Task] = {}
        self._background_tasks: set[asyncio.Task] = set()
        self._CreateFileRequest = None
        self._CreateFileRequestBody = None
        self._CreateImageRequest = None
        self._CreateImageRequestBody = None

    @property
    def supports_streaming(self) -> bool:
        return True

    async def start(self) -> None:
        if self._running:
            return
        try:
            import lark_oapi as lark
            from lark_oapi.api.im.v1 import (
                CreateFileRequest, CreateFileRequestBody,
                CreateImageRequest, CreateImageRequestBody,
                CreateMessageRequest, CreateMessageRequestBody,
                CreateMessageReactionRequest, CreateMessageReactionRequestBody,
                Emoji,
                PatchMessageRequest, PatchMessageRequestBody,
                ReplyMessageRequest, ReplyMessageRequestBody,
            )
        except ImportError:
            logger.error("lark-oapi is not installed. Install: uv add lark-oapi")
            return

        app_id = str(self.config.get("app_id", ""))
        app_secret = str(self.config.get("app_secret", ""))
        domain = str(self.config.get("domain", "https://open.feishu.cn"))
        if not app_id or not app_secret:
            logger.error("Feishu channel requires app_id and app_secret")
            return

        self._lark = lark
        self._CreateMessageRequest = CreateMessageRequest
        self._CreateMessageRequestBody = CreateMessageRequestBody
        self._ReplyMessageRequest = ReplyMessageRequest
        self._ReplyMessageRequestBody = ReplyMessageRequestBody
        self._PatchMessageRequest = PatchMessageRequest
        self._PatchMessageRequestBody = PatchMessageRequestBody
        self._CreateMessageReactionRequest = CreateMessageReactionRequest
        self._CreateMessageReactionRequestBody = CreateMessageReactionRequestBody
        self._CreateFileRequest = CreateFileRequest
        self._CreateFileRequestBody = CreateFileRequestBody
        self._CreateImageRequest = CreateImageRequest
        self._CreateImageRequestBody = CreateImageRequestBody
        self._Emoji = Emoji
        self._api_client = lark.Client.builder().app_id(app_id).app_secret(app_secret).domain(domain).build()
        self._main_loop = asyncio.get_event_loop()
        self._running = True
        self.bus.subscribe_outbound(self._on_outbound)
        self._thread = threading.Thread(target=self._run_ws, args=(app_id, app_secret, domain), daemon=True)
        self._thread.start()
        logger.info("Feishu channel started")

    async def stop(self) -> None:
        self._running = False
        self.bus.unsubscribe_outbound(self._on_outbound)
        for task in list(self._background_tasks):
            task.cancel()
        for task in list(self._running_card_tasks.values()):
            task.cancel()
        self._background_tasks.clear()
        self._running_card_tasks.clear()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _run_ws(self, app_id: str, app_secret: str, domain: str) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            import lark_oapi as lark
            import lark_oapi.ws.client as ws_client_mod
            ws_client_mod.loop = loop
            handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(self._on_message).build()
            client = lark.ws.Client(app_id=app_id, app_secret=app_secret, event_handler=handler, log_level=lark.LogLevel.INFO, domain=domain)
            client.start()
        except Exception:
            if self._running:
                logger.exception("Feishu WebSocket client failed")

    async def send(self, msg: OutboundMessage) -> None:
        source_id = msg.thread_ts
        if source_id:
            if not msg.is_final and msg.text:
                card_id = await self._ensure_running_card(source_id, msg.text)
                if card_id:
                    await self._update_card(card_id, msg.text)
                else:
                    await self._reply_card(source_id, msg.text)
            elif msg.is_final:
                self._running_cards.pop(source_id, None)
                await self._reply_card(source_id, msg.text)
                await self._add_reaction(source_id, "DONE")
            return
        await self._create_card(msg.chat_id, msg.text)

    @staticmethod
    def _build_card(text: str) -> str:
        return json.dumps({
            "config": {"wide_screen_mode": True, "update_multi": True},
            "elements": [{"tag": "markdown", "content": text[:30000] or ""}],
        }, ensure_ascii=False)

    async def _reply_card(self, message_id: str, text: str) -> str | None:
        if not self._api_client:
            return None
        body = self._ReplyMessageRequestBody.builder().msg_type("interactive").content(self._build_card(text)).reply_in_thread(True).build()
        req = self._ReplyMessageRequest.builder().message_id(message_id).request_body(body).build()
        resp = await asyncio.to_thread(self._api_client.im.v1.message.reply, req)
        return getattr(getattr(resp, "data", None), "message_id", None)

    async def _create_card(self, chat_id: str, text: str) -> None:
        if not self._api_client:
            return
        body = self._CreateMessageRequestBody.builder().receive_id(chat_id).msg_type("interactive").content(self._build_card(text)).build()
        req = self._CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
        await asyncio.to_thread(self._api_client.im.v1.message.create, req)

    async def _update_card(self, message_id: str, text: str) -> None:
        if not self._api_client:
            return
        body = self._PatchMessageRequestBody.builder().content(self._build_card(text)).build()
        req = self._PatchMessageRequest.builder().message_id(message_id).request_body(body).build()
        await asyncio.to_thread(self._api_client.im.v1.message.patch, req)

    async def _ensure_running_card(self, source_id: str, text: str = "Working on it...") -> str | None:
        existing = self._running_cards.get(source_id)
        if existing:
            return existing
        task = self._running_card_tasks.get(source_id)
        if task:
            return await task
        task = asyncio.create_task(self._create_running_card(source_id, text))
        self._running_card_tasks[source_id] = task
        task.add_done_callback(lambda done, mid=source_id: self._running_card_tasks.pop(mid, None))
        return await task

    async def _create_running_card(self, source_id: str, text: str) -> str | None:
        card_id = await self._reply_card(source_id, text)
        if card_id:
            self._running_cards[source_id] = card_id
        return card_id

    async def _add_reaction(self, message_id: str, emoji_type: str = "OK") -> None:
        if not self._api_client:
            return
        try:
            body = self._CreateMessageReactionRequestBody.builder().reaction_type(self._Emoji.builder().emoji_type(emoji_type).build()).build()
            req = self._CreateMessageReactionRequest.builder().message_id(message_id).request_body(body).build()
            await asyncio.to_thread(self._api_client.im.v1.message_reaction.create, req)
        except Exception:
            logger.debug("Failed to add Feishu reaction", exc_info=True)

    async def _prepare_inbound(self, msg_id: str, inbound) -> None:
        task = asyncio.create_task(self._add_reaction(msg_id, "OK"))
        self._background_tasks.add(task)
        task.add_done_callback(lambda done: self._background_tasks.discard(done))
        await self.bus.publish_inbound(inbound)

    def _on_message(self, event) -> None:
        try:
            message = event.event.message
            chat_id = message.chat_id
            msg_id = message.message_id
            root_id = getattr(message, "root_id", None) or None
            sender_id = event.event.sender.sender_id.open_id
            content = json.loads(message.content)
            text, files = _parse_feishu_content(content)

            if not (text or files):
                return

            cmd = _is_feishu_command(text) if text else False
            msg_type = InboundMessageType.COMMAND if cmd else InboundMessageType.CHAT
            inbound = self._make_inbound(
                chat_id=chat_id,
                user_id=sender_id,
                text=text.strip() if text else "",
                msg_type=msg_type,
                thread_ts=msg_id,
                topic_id=root_id or msg_id,
                files=files,
                metadata={"message_id": msg_id, "root_id": root_id},
            )
            if self._main_loop and self._main_loop.is_running():
                asyncio.run_coroutine_threadsafe(self._prepare_inbound(msg_id, inbound), self._main_loop)
        except Exception:
            logger.exception("Failed to process Feishu message")


def _parse_feishu_content(content: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    files: list[dict[str, Any]] = []
    if "text" in content:
        return str(content.get("text") or ""), files
    if "file_key" in content:
        key = content.get("file_key")
        if isinstance(key, str) and key:
            files.append({"file_key": key, "type": "file"})
        return "[file]", files
    if "image_key" in content:
        key = content.get("image_key")
        if isinstance(key, str) and key:
            files.append({"image_key": key, "type": "image"})
        return "[image]", files
    if isinstance(content.get("content"), list):
        paragraphs: list[str] = []
        for para in content["content"]:
            if not isinstance(para, list):
                continue
            parts: list[str] = []
            for item in para:
                if not isinstance(item, dict):
                    continue
                tag = item.get("tag")
                if tag in ("text", "at") and item.get("text"):
                    parts.append(str(item["text"]))
                elif tag == "img" and item.get("image_key"):
                    files.append({"image_key": item["image_key"], "type": "image"})
                    parts.append("[image]")
                elif tag in ("file", "media") and item.get("file_key"):
                    files.append({"file_key": item["file_key"], "type": "file"})
                    parts.append("[file]")
            if parts:
                paragraphs.append(" ".join(parts))
        return "\n\n".join(paragraphs), files
    return "", files
