from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.core.middleware import TokenUsageTracker


def new_thread_id(prefix: str = "thread") -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


@dataclass(slots=True)
class RuntimeContext:
    """Request-scoped context shared by the lead agent, tools, and channels."""

    thread_id: str = field(default_factory=new_thread_id)
    run_id: str = field(default_factory=lambda: new_thread_id("run"))
    user_id: str = "default_user"
    model: str = ""
    search_mode: dict[str, Any] = field(default_factory=dict)
    agent_name: Optional[str] = None
    subagent_enabled: bool = True
    max_concurrent_subagents: int = 3
    deepsearch_config: dict[str, Any] = field(default_factory=dict)
    channel_context: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    token_tracker: TokenUsageTracker = field(default_factory=TokenUsageTracker)
    viewed_images: dict[str, dict[str, str]] = field(default_factory=dict)
    promoted_tools: dict[str, Any] = field(default_factory=dict)
    workspace_path: str = ""

    @classmethod
    def from_configurable(cls, configurable: dict[str, Any] | None) -> RuntimeContext:
        cfg = configurable or {}
        search_mode = cfg.get("search_mode") if isinstance(cfg.get("search_mode"), dict) else {}
        return cls(
            thread_id=str(cfg.get("thread_id") or new_thread_id()),
            run_id=str(cfg.get("run_id") or new_thread_id("run")),
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
            viewed_images=dict(cfg.get("viewed_images") or {}),
            promoted_tools=dict(cfg.get("promoted_tools") or {}),
            workspace_path=str(cfg.get("workspace_path") or ""),
        )

    def to_configurable(self) -> dict[str, Any]:
        data = {
            "thread_id": self.thread_id,
            "run_id": self.run_id,
            "user_id": self.user_id,
            "model": self.model,
            "search_mode": self.search_mode,
            "agent_name": self.agent_name,
            "subagent_enabled": self.subagent_enabled,
            "max_concurrent_subagents": self.max_concurrent_subagents,
            "channel_context": self.channel_context,
            "metadata": self.metadata,
            "viewed_images": self.viewed_images,
            "promoted_tools": self.promoted_tools,
            "workspace_path": self.workspace_path,
        }
        data.update(self.deepsearch_config)
        return data

    def runnable_config(self) -> dict[str, Any]:
        return {
            "configurable": self.to_configurable(),
            "recursion_limit": 80,
            "metadata": {
                "thread_id": self.thread_id,
                "run_id": self.run_id,
                "user_id": self.user_id,
                "agent_name": self.agent_name or "default",
                "subagent_enabled": self.subagent_enabled,
            },
        }


def get_runtime_context(config: dict[str, Any] | None) -> RuntimeContext | None:
    cfg = (config or {}).get("configurable") if isinstance(config, dict) else {}
    if not isinstance(cfg, dict):
        return None
    context = cfg.get("runtime_context")
    return context if isinstance(context, RuntimeContext) else None


def ensure_runtime_context(config: dict[str, Any]) -> RuntimeContext:
    cfg = config.setdefault("configurable", {})
    if not isinstance(cfg, dict):
        cfg = {}
        config["configurable"] = cfg
    context = cfg.get("runtime_context")
    if isinstance(context, RuntimeContext):
        return context
    context = RuntimeContext.from_configurable(cfg)
    cfg["runtime_context"] = context
    return context


def get_viewed_images(config: dict[str, Any] | None) -> dict[str, dict[str, str]]:
    context = get_runtime_context(config)
    if context is not None:
        return context.viewed_images
    cfg = (config or {}).get("configurable") if isinstance(config, dict) else {}
    return dict(cfg.get("viewed_images") or {}) if isinstance(cfg, dict) else {}


@dataclass(slots=True)
class SoulSearcherRuntimeContext:
    """Application-scoped runtime objects shared by HTTP, A2A, and background paths."""

    settings: Any
    research_graph: Any
    checkpointer: Any
    checkpointer_type: str
    background_run_manager: Any
    run_manager: Any
    mcp_enabled: bool
    mcp_servers_config: Any
    research_execution_service: Any | None = None


def merge_viewed_images(config: dict[str, Any], images: dict[str, dict[str, str]]) -> None:
    if not images:
        return
    context = ensure_runtime_context(config)
    context.viewed_images.update(images)
    cfg = config.setdefault("configurable", {})
    if isinstance(cfg, dict):
        existing = cfg.get("viewed_images")
        if not isinstance(existing, dict):
            existing = {}
        existing.update(images)
        cfg["viewed_images"] = existing


def clear_viewed_images(config: dict[str, Any]) -> None:
    context = get_runtime_context(config)
    if context is not None:
        context.viewed_images.clear()
    cfg = config.get("configurable") if isinstance(config, dict) else {}
    if isinstance(cfg, dict):
        cfg["viewed_images"] = {}


def get_runtime_token_tracker(config: dict[str, Any] | None) -> TokenUsageTracker | None:
    context = get_runtime_context(config)
    return context.token_tracker if context is not None else None
