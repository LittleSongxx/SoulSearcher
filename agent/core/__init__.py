"""
Public API surface for `agent.core`.

This module is intentionally **lazy** to avoid circular import chains.
"""

from __future__ import annotations

import importlib
from typing import Any

__all__ = [
    "AgentState",
    "AgentInputState",
    "Event",
    "EventEmitter",
    "SearchCache",
    "ToolEvent",
    "ToolEventType",
    "create_checkpointer",
    "create_research_graph",
    "event_stream_generator",
    "get_emitter",
    "get_emitter_sync",
    "remove_emitter",
    "build_initial_state",
    "create_research_graph_with_checkpointer",
]

_SYMBOL_TO_MODULE: dict[str, str] = {
    # Graph/state
    "create_research_graph": "agent.core.graph",
    "create_research_graph_with_checkpointer": "agent.core.graph",
    "create_checkpointer": "agent.core.graph",
    "AgentState": "agent.core.state",
    "AgentInputState": "agent.core.state",
    "build_initial_state": "agent.core.state",
    # Events / streaming
    "Event": "agent.core.events",
    "EventEmitter": "agent.core.events",
    "ToolEvent": "agent.core.events",
    "ToolEventType": "agent.core.events",
    "event_stream_generator": "agent.core.events",
    "get_emitter": "agent.core.events",
    "get_emitter_sync": "agent.core.events",
    "remove_emitter": "agent.core.events",
    # Config
    "AgentProcessorConfig": "agent.core.processor_config",
    # Cache
    "SearchCache": "agent.core.search_cache",
}


def __getattr__(name: str) -> Any:
    module_path = _SYMBOL_TO_MODULE.get(name)
    if not module_path:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(module_path)
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(set(list(globals().keys()) + list(_SYMBOL_TO_MODULE.keys())))
