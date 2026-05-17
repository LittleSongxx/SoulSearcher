from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


def new_thread_id(prefix: str = "thread") -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


@dataclass(slots=True)
class RuntimeContext:
    """Request-scoped context shared by the lead agent, tools, and channels."""

    thread_id: str = field(default_factory=new_thread_id)
    user_id: str = "default_user"
    model: str = ""
    search_mode: dict[str, Any] = field(default_factory=dict)
    agent_name: Optional[str] = None
    subagent_enabled: bool = True
    max_concurrent_subagents: int = 3
    deepsearch_config: dict[str, Any] = field(default_factory=dict)
    channel_context: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_configurable(cls, configurable: dict[str, Any] | None) -> "RuntimeContext":
        cfg = configurable or {}
        search_mode = cfg.get("search_mode") if isinstance(cfg.get("search_mode"), dict) else {}
        return cls(
            thread_id=str(cfg.get("thread_id") or new_thread_id()),
            user_id=str(cfg.get("user_id") or "default_user"),
            model=str(cfg.get("model") or cfg.get("model_name") or ""),
            search_mode=dict(search_mode),
            agent_name=cfg.get("agent_name"),
            subagent_enabled=bool(cfg.get("subagent_enabled", True)),
            max_concurrent_subagents=int(cfg.get("max_concurrent_subagents", 3) or 3),
            deepsearch_config={
                key: value
                for key, value in cfg.items()
                if isinstance(key, str) and key.startswith("deepsearch_")
            },
            channel_context=dict(cfg.get("channel_context") or {}),
            metadata=dict(cfg.get("metadata") or {}),
        )

    def to_configurable(self) -> dict[str, Any]:
        data = {
            "thread_id": self.thread_id,
            "user_id": self.user_id,
            "model": self.model,
            "search_mode": self.search_mode,
            "agent_name": self.agent_name,
            "subagent_enabled": self.subagent_enabled,
            "max_concurrent_subagents": self.max_concurrent_subagents,
            "channel_context": self.channel_context,
            "metadata": self.metadata,
        }
        data.update(self.deepsearch_config)
        return data

    def runnable_config(self) -> dict[str, Any]:
        return {
            "configurable": self.to_configurable(),
            "recursion_limit": 80,
            "metadata": {
                "thread_id": self.thread_id,
                "user_id": self.user_id,
                "agent_name": self.agent_name or "default",
                "subagent_enabled": self.subagent_enabled,
            },
        }
