"""
Lightweight facade for the agent package.

Only the stable, public-facing symbols are exported here.
"""

from __future__ import annotations

import importlib
from typing import Any

_PUBLIC_SYMBOLS = {
    # Core graph/state
    "create_research_graph",
    "create_research_graph_with_checkpointer",
    "create_checkpointer",
    "AgentState",
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
    # Workflow helpers
    "summarize_messages",
}

__all__ = sorted(_PUBLIC_SYMBOLS)

_SYMBOL_TO_MODULE: dict[str, str] = {
    # Core graph/state
    "create_research_graph": "agent.core.graph",
    "create_research_graph_with_checkpointer": "agent.core.graph",
    "create_checkpointer": "agent.core.graph",
    "AgentState": "agent.core.state",
    "QueryState": "agent.core.state",
    "ResearchPlan": "agent.core.state",
    "build_initial_state": "agent.core.state",
    # Events / streaming
    "event_stream_generator": "agent.core.events",
    "get_emitter": "agent.core.events",
    "get_emitter_sync": "agent.core.events",
    "remove_emitter": "agent.core.events",
    "ToolEvent": "agent.core.events",
    "ToolEventType": "agent.core.events",
    # Prompts
    "get_default_agent_prompt": "agent.prompts.agent_prompts",
    "get_agent_prompt": "agent.prompts.system_prompts",
    "get_writer_prompt": "agent.prompts.system_prompts",
    "get_deep_research_prompt": "agent.prompts.system_prompts",
    "PromptManager": "agent.prompts.prompt_manager",
    "get_prompt_manager": "agent.prompts.prompt_manager",
    "set_prompt_manager": "agent.prompts.prompt_manager",
    # Workflow helpers
    "summarize_messages": "agent.core.message_utils",
}


def __getattr__(name: str) -> Any:
    module_path = _SYMBOL_TO_MODULE.get(name)
    if module_path:
        module = importlib.import_module(module_path)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(list(globals().keys()) + list(_PUBLIC_SYMBOLS)))
