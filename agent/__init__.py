"""
Lightweight facade for the agent package.

Only the stable, public-facing symbols are exported here.
"""

from __future__ import annotations

from typing import Any

_PUBLIC_SYMBOLS = {
    # Core graph/state
    "create_research_graph",
    "create_research_graph_with_checkpointer",
    "create_checkpointer",
    "AgentState",
    "AgentStateV2",
    "QueryState",
    "ResearchPlan",
    "build_initial_state",
    # Events / streaming
    "event_stream_generator",
    "get_emitter",
    "get_emitter_sync",
    "remove_emitter",
    "ToolEvent",
    "ToolEventType",
    # Prompts
    "get_default_agent_prompt",
    "get_agent_prompt",
    "get_writer_prompt",
    "get_deep_research_prompt",
    "PromptManager",
    "get_prompt_manager",
    "set_prompt_manager",
    # Workflows & tools
    "get_deep_agent_prompt",
    "build_writer_agent",
    "build_tool_agent",
    "build_agent_tools",
    "initialize_enhanced_tools",
    "summarize_messages",
}

__all__ = sorted(_PUBLIC_SYMBOLS)


def __getattr__(name: str) -> Any:
    if name in _PUBLIC_SYMBOLS:
        from agent import api as _api

        return getattr(_api, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(list(globals().keys()) + list(_PUBLIC_SYMBOLS)))
