"""
Public API surface for the agent package.

Keep this list small and stable; everything else should be imported from the
specific submodules (agent.core.*, agent.workflows.*, agent.prompts.*, etc.).
"""

from agent.core import (
    AgentState,
    AgentStateV2,
    QueryState,
    ResearchPlan,
    ToolEvent,
    ToolEventType,
    create_checkpointer,
    create_research_graph,
    create_research_graph_with_checkpointer,
    event_stream_generator,
    get_emitter,
    get_emitter_sync,
    remove_emitter,
    build_initial_state,
)
from agent.core.message_utils import summarize_messages
from agent.prompts import (
    PromptManager,
    get_agent_prompt,
    get_deep_research_prompt,
    get_default_agent_prompt,
    get_prompt_manager,
    get_writer_prompt,
    set_prompt_manager,
)
from agent.workflows import (
    build_agent_tools,
    build_tool_agent,
    build_writer_agent,
    get_deep_agent_prompt,
    initialize_enhanced_tools,
)

__all__ = [
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
]
