"""
Public API surface for `agent.workflows`.

This module is intentionally **lazy** to prevent circular import chains
between workflows and tools.
"""

from __future__ import annotations

import importlib
from typing import Any

__all__ = [
    "ContinuationHandler",
    "ContinuationState",
    "QueryDeduplicator",
    "ResponseHandler",
    "ResultAggregator",
    "SearchCache",
    "ToolResultInjector",
    "build_agent_tools",
    "build_tool_agent",
    "build_writer_agent",
    "check_cancellation",
    "get_deep_agent_prompt",
    "get_search_cache",
    "initialize_enhanced_tools",
]

_SYMBOL_TO_MODULE: dict[str, str] = {
    # Graph node helpers
    "initialize_enhanced_tools": "agent.workflows.agent_tools",
    "check_cancellation": "common.cancellation",
    # Deep research helpers
    "get_deep_agent_prompt": "agent.workflows.agent_tools",
    # Agent factories/tools
    "build_tool_agent": "agent.workflows.agent_factory",
    "build_writer_agent": "agent.workflows.agent_factory",
    "build_agent_tools": "agent.workflows.agent_tools",
    # Response/aggregation
    "ResponseHandler": "agent.workflows.response_handler",
    "ResultAggregator": "agent.workflows.result_aggregator",
    # Continuation helpers
    "ContinuationHandler": "agent.workflows.continuation",
    "ContinuationState": "agent.workflows.continuation",
    "ToolResultInjector": "agent.workflows.continuation",
    # Search cache (workflow-local)
    "QueryDeduplicator": "agent.workflows.search_cache",
    "SearchCache": "agent.workflows.search_cache",
    "get_search_cache": "agent.workflows.search_cache",
}


def __getattr__(name: str) -> Any:
    module_path = _SYMBOL_TO_MODULE.get(name)
    if not module_path:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(module_path)
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(set(list(globals().keys()) + list(_SYMBOL_TO_MODULE.keys())))
