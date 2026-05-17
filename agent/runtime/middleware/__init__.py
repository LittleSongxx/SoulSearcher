"""DeerFlow-aligned middleware chain for Weaver lead agent."""

from agent.runtime.middleware.dynamic_context import DynamicContextMiddleware
from agent.runtime.middleware.tool_error import ToolErrorHandlingMiddleware
from agent.runtime.middleware.sandbox_audit import SandboxAuditMiddleware
from agent.runtime.middleware.loop_detection import LoopDetectionMiddleware
from agent.runtime.middleware.clarification import ClarificationMiddleware
from agent.runtime.middleware.summarization import SummarizationMiddleware
from agent.runtime.middleware.memory_ware import MemoryMiddleware
from agent.runtime.middleware.subagent_limit import SubagentLimitMiddleware
from agent.runtime.middleware.todo_ware import TodoMiddleware
from agent.runtime.middleware.title_ware import TitleMiddleware
from agent.runtime.middleware.uploads_ware import UploadsMiddleware

__all__ = [
    "DynamicContextMiddleware",
    "ToolErrorHandlingMiddleware",
    "SandboxAuditMiddleware",
    "LoopDetectionMiddleware",
    "ClarificationMiddleware",
    "SummarizationMiddleware",
    "MemoryMiddleware",
    "SubagentLimitMiddleware",
    "TodoMiddleware",
    "TitleMiddleware",
    "UploadsMiddleware",
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
    loop_detection_enabled: bool = True,
) -> list:
    """Build the full middleware chain in DeerFlow order.

    Order matters — each middleware can transform state before the next one sees it.
    """
    from langchain.agents.middleware import AgentMiddleware

    middlewares: list[AgentMiddleware] = []

    # 1. ThreadData — ensures thread_id available early
    from agent.runtime.middleware.thread_data import ThreadDataMiddleware
    middlewares.append(ThreadDataMiddleware(thread_id=thread_id, user_id=user_id))

    # 2. Uploads — inject uploaded file context
    middlewares.append(UploadsMiddleware())

    # 3. DynamicContext — inject <system-reminder> with memory + date
    middlewares.append(DynamicContextMiddleware(agent_name=agent_name))

    # 4. Summarization — context reduction when approaching token limits
    if summarization_enabled:
        middlewares.append(SummarizationMiddleware())

    # 5. TodoList — plan mode task tracking
    if plan_mode:
        middlewares.append(TodoMiddleware())

    # 6. Title — auto-generate thread title
    middlewares.append(TitleMiddleware())

    # 7. Memory — queue conversation for async memory update
    if memory_enabled:
        middlewares.append(MemoryMiddleware(agent_name=agent_name))

    # 8. SubagentLimit — truncate excess task calls
    if subagent_enabled:
        middlewares.append(SubagentLimitMiddleware(max_concurrent=max_concurrent_subagents))

    # 9. LoopDetection — break tool-call loops
    if loop_detection_enabled:
        middlewares.append(LoopDetectionMiddleware())

    # 10. ToolError — convert exceptions to ToolMessages
    middlewares.append(ToolErrorHandlingMiddleware())

    # 11. SandboxAudit — security logging
    middlewares.append(SandboxAuditMiddleware())

    # 12. Clarification — MUST be last
    middlewares.append(ClarificationMiddleware())

    return middlewares
