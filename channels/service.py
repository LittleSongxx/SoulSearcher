from __future__ import annotations

import logging
from typing import Any

from common.config import settings

from .feishu import FeishuChannel
from .manager import ChannelManager
from .message_bus import MessageBus
from .store import ChannelStore

logger = logging.getLogger(__name__)


class ChannelService:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or build_channel_config_from_settings()
        self.bus = MessageBus()
        self.store = ChannelStore()
        self.manager = ChannelManager(
            bus=self.bus,
            store=self.store,
            base_url=self.config.get("base_url"),
            default_session=self.config.get("session") if isinstance(self.config.get("session"), dict) else None,
        )
        self._channels: dict[str, Any] = {}
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        if not self.config.get("enabled", False):
            logger.info("Channel service disabled")
            return
        await self.manager.start()
        feishu_cfg = self.config.get("feishu") if isinstance(self.config.get("feishu"), dict) else {}
        if feishu_cfg.get("enabled", False):
            channel = FeishuChannel(bus=self.bus, config=feishu_cfg)
            await channel.start()
            if channel.is_running:
                self._channels["feishu"] = channel
        self._running = True
        logger.info("Channel service started: %s", list(self._channels))

    async def stop(self) -> None:
        for channel in list(self._channels.values()):
            await channel.stop()
        self._channels.clear()
        await self.manager.stop()
        self._running = False

    async def restart_channel(self, name: str) -> bool:
        if name in self._channels:
            await self._channels[name].stop()
            self._channels.pop(name, None)
        if name != "feishu":
            return False
        feishu_cfg = self.config.get("feishu") if isinstance(self.config.get("feishu"), dict) else {}
        if not feishu_cfg.get("enabled", False):
            return False
        channel = FeishuChannel(bus=self.bus, config=feishu_cfg)
        await channel.start()
        if channel.is_running:
            self._channels[name] = channel
            return True
        return False

    def get_status(self) -> dict[str, Any]:
        return {
            "service_running": self._running,
            "manager": self.manager.status(),
            "channels": {
                "feishu": {
                    "enabled": bool((self.config.get("feishu") or {}).get("enabled", False)),
                    "running": "feishu" in self._channels,
                    "supports_streaming": True,
                }
            },
        }


def build_channel_config_from_settings() -> dict[str, Any]:
    return {
        "enabled": bool(getattr(settings, "channels_enabled", False)),
        "base_url": getattr(settings, "channels_base_url", "") or f"http://127.0.0.1:{settings.port}",
        "session": {
            "agent_name": getattr(settings, "channel_default_agent_name", "") or None,
            "subagent_enabled": bool(getattr(settings, "channel_default_subagent_enabled", True)),
            "model": getattr(settings, "channel_default_model", "") or settings.primary_model,
            "deepsearch_config": {
                "deepsearch_mode": getattr(settings, "channel_default_deepsearch_mode", "supervisor_workers"),
                "deepsearch_strategy": getattr(settings, "channel_default_deepsearch_mode", "supervisor_workers"),
            },
        },
        "feishu": {
            "enabled": bool(getattr(settings, "feishu_channel_enabled", False)),
            "app_id": getattr(settings, "feishu_app_id", ""),
            "app_secret": getattr(settings, "feishu_app_secret", ""),
            "domain": getattr(settings, "feishu_domain", "https://open.feishu.cn"),
        },
    }


_channel_service: ChannelService | None = None


def get_channel_service() -> ChannelService | None:
    return _channel_service


async def start_channel_service() -> ChannelService:
    global _channel_service
    if _channel_service is None:
        _channel_service = ChannelService()
        await _channel_service.start()
    return _channel_service


async def stop_channel_service() -> None:
    global _channel_service
    if _channel_service is not None:
        await _channel_service.stop()
        _channel_service = None
