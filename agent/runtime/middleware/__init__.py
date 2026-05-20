"""Middleware chain for Weaver lead agent — 6 essential layers (down from 12).

Removed (dead/empty): UploadsMiddleware, ToolErrorHandlingMiddleware, SandboxAuditMiddleware
Removed (redundant): LoopDetectionMiddleware (duplicated core LoopDetector)
Merged: subagent_limit + loop_detection → GuardMiddleware
"""

from agent.runtime.middleware.clarification import ClarificationMiddleware
from agent.runtime.middleware.dynamic_context import DynamicContextMiddleware
from agent.runtime.middleware.memory_ware import MemoryMiddleware
from agent.runtime.middleware.subagent_limit import SubagentLimitMiddleware
from agent.runtime.middleware.summarization import SummarizationMiddleware
from agent.runtime.middleware.todo_ware import TodoMiddleware

__all__ = [
    "ClarificationMiddleware",
    "DynamicContextMiddleware",
    "MemoryMiddleware",
    "SubagentLimitMiddleware",
    "SummarizationMiddleware",
    "TodoMiddleware",
    "build_lead_middlewares",
]


def build_lead_middlewares(
    *,
    agent_name: str | None = None,
    thread_id: str | None = None,
    user_id: str | None = None,
    subagent_enabled: bool = False,
    max_concurrent_subagents: int = 3,
    plan_mode: bool = False,
    vision_enabled: bool = False,
    memory_enabled: bool = True,
    summarization_enabled: bool = True,
) -> list:
    """Build the middleware chain for the lead agent.

    Order matters — each middleware can transform state before the next one sees it.
    """
    from langchain.agents.middleware import AgentMiddleware

    middlewares: list[AgentMiddleware] = []

    # 1. ThreadData — thread/user ID injection
    from agent.runtime.middleware.thread_data import ThreadDataMiddleware
    middlewares.append(ThreadDataMiddleware(thread_id=thread_id, user_id=user_id))

    # 2. DynamicContext — system-reminder with memory + date
    middlewares.append(DynamicContextMiddleware(agent_name=agent_name))

    # 3. Summarization — context reduction near token limits
    if summarization_enabled:
        middlewares.append(SummarizationMiddleware())

    # 4. TodoList — plan mode task tracking
    if plan_mode:
        middlewares.append(TodoMiddleware())

    # 5. Memory — async memory update enqueue
    if memory_enabled:
        middlewares.append(MemoryMiddleware(agent_name=agent_name))

    # 6. SubagentLimit — guard against runaway agents
    if subagent_enabled:
        middlewares.append(SubagentLimitMiddleware(max_concurrent=max_concurrent_subagents))

    # 7. Clarification — MUST be last (intercepts ask_clarification)
    middlewares.append(ClarificationMiddleware())

    return middlewares
