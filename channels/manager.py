"""ChannelManager — consumes inbound IM messages and dispatches to Weaver agent.

Bridges IM channels (Feishu, Slack, Telegram, etc.) to Weaver's research SSE API
or LangGraph-compatible agent runtime. Supports both streaming and wait modes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re
import time
from pathlib import Path
from typing import Any

import httpx

from channels.commands import CHANNEL_CAPABILITIES, KNOWN_CHANNEL_COMMANDS
from channels.message_bus import InboundMessage, InboundMessageType, MessageBus, OutboundMessage, ResolvedAttachment
from channels.store import ChannelStore
from agent.runtime.context import new_thread_id
from common.config import settings

logger = logging.getLogger(__name__)

DEFAULT_STREAM_INTERVAL = 0.35
THREAD_BUSY_MSG = "当前会话已有任务在运行，请稍后再试，或发送 /cancel 取消。"


def _extract_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(block.get("text", ""))
        return "".join(parts)
    return ""


def _extract_response_text(result: dict | list) -> str:
    """Extract last AI message text from a run result."""
    if isinstance(result, dict):
        messages = result.get("messages", [])
    elif isinstance(result, list):
        messages = result
    else:
        return ""

    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("type") == "human":
            break
        if msg.get("type") == "ai":
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                return content
            if isinstance(content, list):
                text = "".join(
                    b.get("text", "") if isinstance(b, dict) else str(b)
                    for b in content
                )
                if text:
                    return text
    return ""


class ChannelManager:
    """Core dispatcher bridging IM channels to Weaver agent."""

    def __init__(
        self,
        bus: MessageBus,
        store: ChannelStore,
        *,
        base_url: str | None = None,
        max_concurrency: int = 5,
        default_session: dict[str, Any] | None = None,
    ) -> None:
        self.bus = bus
        self.store = store
        self.base_url = (base_url or f"http://127.0.0.1:{settings.port}").rstrip("/")
        self._max_concurrency = max_concurrency
        self._default_session = default_session or {}
        self._semaphore: asyncio.Semaphore | None = None
        self._running = False
        self._task: asyncio.Task | None = None
        self._active: dict[str, str] = {}

    def _channel_supports_streaming(self, name: str) -> bool:
        from channels.service import get_channel_service
        svc = get_channel_service()
        if svc:
            ch = svc.get_channel(name)
            if ch is not None:
                return ch.supports_streaming
        return CHANNEL_CAPABILITIES.get(name, {}).get("supports_streaming", False)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._semaphore = asyncio.Semaphore(self._max_concurrency)
        self._task = asyncio.create_task(self._dispatch_loop())
        logger.info("ChannelManager started (max_concurrency=%d)", self._max_concurrency)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("ChannelManager stopped")

    async def _dispatch_loop(self) -> None:
        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.get_inbound(), timeout=1.0)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            asyncio.create_task(self._handle_message(msg))

    async def _handle_message(self, msg: InboundMessage) -> None:
        async with self._semaphore:
            try:
                if msg.msg_type == InboundMessageType.COMMAND or msg.text.strip().startswith("/"):
                    await self._handle_command(msg)
                else:
                    await self._handle_chat(msg)
            except Exception:
                logger.exception("Error handling message from %s", msg.channel_name)
                await self._send_error(msg, "处理消息时发生内部错误，请重试。")

    async def _handle_chat(self, msg: InboundMessage) -> None:
        topic_id = msg.topic_id or msg.thread_ts or msg.chat_id
        stream_key = f"{msg.channel_name}:{msg.chat_id}:{topic_id}"

        if stream_key in self._active:
            await self.bus.publish_outbound(OutboundMessage(
                channel_name=msg.channel_name,
                chat_id=msg.chat_id,
                text=THREAD_BUSY_MSG,
                is_final=True,
                thread_ts=msg.thread_ts,
            ))
            return

        thread_id = self.store.get_thread_id(msg.channel_name, msg.chat_id, topic_id)
        if not thread_id:
            thread_id = new_thread_id("channel")
            self.store.set_thread_id(msg.channel_name, msg.chat_id, topic_id, thread_id)

        self._active[stream_key] = thread_id
        try:
            if self._channel_supports_streaming(msg.channel_name):
                await self._handle_streaming(msg, thread_id, stream_key)
            else:
                await self._handle_wait(msg, thread_id, stream_key)
        finally:
            self._active.pop(stream_key, None)

    async def _handle_streaming(self, msg: InboundMessage, thread_id: str, stream_key: str) -> None:
        payload = {
            "query": msg.text,
            "thread_id": thread_id,
            "user_id": f"{msg.channel_name}:{msg.user_id}",
            "model": self._default_session.get("model") or settings.primary_model,
            "subagent_enabled": bool(self._default_session.get("subagent_enabled", True)),
            "search_mode": {"useWebSearch": True, "useAgent": True, "useDeepSearch": True},
            "channel_context": {
                "channel": msg.channel_name,
                "chat_id": msg.chat_id,
                "topic_id": msg.topic_id,
            },
        }

        headers = {"Accept": "text/event-stream", "Content-Type": "application/json"}
        if getattr(settings, "internal_api_key", ""):
            headers["Authorization"] = f"Bearer {settings.internal_api_key}"

        await self.bus.publish_outbound(OutboundMessage(
            channel_name=msg.channel_name, chat_id=msg.chat_id,
            text="Working on it...", thread_ts=msg.thread_ts, stream_id=thread_id,
        ))

        final_text = ""
        snapshot = ""
        last_sent = 0.0

        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("POST", f"{self.base_url}/api/research/sse", json=payload, headers=headers) as resp:
                    resp.raise_for_status()
                    async for event in self._iter_sse(resp):
                        event_type = event.get("event", "")
                        data = event.get("data")
                        if isinstance(data, str):
                            try:
                                data = json.loads(data)
                            except json.JSONDecodeError:
                                data = {}

                        payload_data = data.get("data", {}) if isinstance(data, dict) else {}

                        if event_type in ("completion", "message", "done"):
                            text = _extract_text_content(payload_data)
                            if text:
                                final_text = text
                                snapshot = text
                        elif event_type in ("status", "tool", "process"):
                            text = _extract_text_content(payload_data)
                            if text:
                                snapshot = text

                        now = time.monotonic()
                        if snapshot and now - last_sent >= DEFAULT_STREAM_INTERVAL and event_type != "done":
                            last_sent = now
                            await self.bus.publish_outbound(OutboundMessage(
                                channel_name=msg.channel_name, chat_id=msg.chat_id,
                                text=snapshot[-3500:], is_final=False, thread_ts=msg.thread_ts,
                                stream_id=thread_id,
                            ))
        except Exception as exc:
            logger.exception("Streaming error for thread %s", thread_id)
            final_text = f"请求处理失败: {exc}"

        await self.bus.publish_outbound(OutboundMessage(
            channel_name=msg.channel_name, chat_id=msg.chat_id,
            text=(final_text or snapshot or "任务已完成。")[-12000:],
            is_final=True, thread_ts=msg.thread_ts, stream_id=thread_id,
        ))

    async def _handle_wait(self, msg: InboundMessage, thread_id: str, stream_key: str) -> None:
        payload = {
            "query": msg.text,
            "thread_id": thread_id,
            "user_id": f"{msg.channel_name}:{msg.user_id}",
            "model": self._default_session.get("model") or settings.primary_model,
        }
        headers = {"Content-Type": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(
                    f"{self.base_url}/api/research/sse",
                    json=payload, headers=headers
                )
                # Accumulate full response
                full_text = ""
                async for event in self._iter_sse(resp):
                    data = event.get("data")
                    if isinstance(data, str):
                        try:
                            data = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                    payload_data = data.get("data", {}) if isinstance(data, dict) else {}
                    text = _extract_text_content(payload_data)
                    if text:
                        full_text = text
                reply = full_text or "任务已完成。"
        except Exception as exc:
            logger.exception("Wait error for thread %s", thread_id)
            reply = f"请求处理失败: {exc}"

        await self.bus.publish_outbound(OutboundMessage(
            channel_name=msg.channel_name, chat_id=msg.chat_id,
            text=reply[-4000:], is_final=True, thread_ts=msg.thread_ts,
        ))

    async def _handle_command(self, msg: InboundMessage) -> None:
        text = msg.text.strip()
        parts = text.split(maxsplit=1)
        command = parts[0].lower().lstrip("/")

        topic_id = msg.topic_id or msg.thread_ts or msg.chat_id

        if command == "new":
            new_id = new_thread_id("channel")
            self.store.set_thread_id(msg.channel_name, msg.chat_id, topic_id, new_id)
            reply = f"已开启新的研究线程。"
        elif command == "status":
            tid = self.store.get_thread_id(msg.channel_name, msg.chat_id, topic_id)
            reply = f"当前线程: {tid or '未创建'}"
        elif command == "models":
            reply = f"默认模型: {settings.primary_model}"
        elif command == "memory":
            try:
                from agent.memory import get_memory_service

                status = get_memory_service().status()
                count = int(status.get("record_count") or 0)
                backend = str(status.get("backend") or "unknown")
                available = bool(status.get("available"))
                state = "可用" if available else "不可用"
                reply = f"统一记忆系统: {state}，后端 {backend}，记录 {count} 条。"
            except Exception:
                reply = "无法获取记忆状态。"
        elif command == "help":
            reply = (
                "可用命令:\n"
                "/new — 开启新线程\n"
                "/status — 查看当前线程\n"
                "/models — 查看模型\n"
                "/memory — 查看记忆状态\n"
                "/cancel — 取消当前任务\n"
                "/help — 帮助信息"
            )
        elif command == "cancel":
            tid = self.store.get_thread_id(msg.channel_name, msg.chat_id, topic_id)
            if tid:
                # Try to cancel via internal API
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        await client.post(f"{self.base_url}/api/research/cancel/{tid}")
                    reply = f"已发送取消请求: {tid}"
                except Exception:
                    reply = f"取消失败: {tid}"
            else:
                reply = "当前没有可取消的任务。"
        else:
            available = " | ".join(sorted(KNOWN_CHANNEL_COMMANDS))
            reply = f"未知命令: /{command}。可用命令: {available}"

        await self.bus.publish_outbound(OutboundMessage(
            channel_name=msg.channel_name, chat_id=msg.chat_id,
            text=reply, is_final=True, thread_ts=msg.thread_ts,
        ))

    async def _send_error(self, msg: InboundMessage, error: str) -> None:
        await self.bus.publish_outbound(OutboundMessage(
            channel_name=msg.channel_name, chat_id=msg.chat_id,
            text=error, is_final=True, thread_ts=msg.thread_ts,
        ))

    async def _iter_sse(self, response: httpx.Response):
        buffer = ""
        async for chunk in response.aiter_text():
            buffer += chunk
            while "\n\n" in buffer:
                frame, buffer = buffer.split("\n\n", 1)
                event: dict[str, Any] = {}
                data_lines: list[str] = []
                for line in frame.splitlines():
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        event["event"] = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].strip())
                if data_lines:
                    event["data"] = "\n".join(data_lines)
                if event:
                    yield event


def _format_artifact_text(artifacts: list[str]) -> str:
    import posixpath
    filenames = [posixpath.basename(p) for p in artifacts]
    if len(filenames) == 1:
        return f"Created File: {filenames[0]}"
    return "Created Files: " + ", ".join(filenames)
