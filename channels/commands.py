"""Shared command definitions for all channel implementations."""

from __future__ import annotations

KNOWN_CHANNEL_COMMANDS: frozenset[str] = frozenset({
    "/bootstrap",
    "/new",
    "/status",
    "/models",
    "/memory",
    "/help",
    "/cancel",
})

CHANNEL_CAPABILITIES: dict[str, dict[str, bool]] = {
    "feishu": {"supports_streaming": True},
    "slack": {"supports_streaming": False},
    "telegram": {"supports_streaming": False},
    "dingtalk": {"supports_streaming": True},
    "wecom": {"supports_streaming": True},
}
