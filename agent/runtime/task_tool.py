"""DeerFlow-style task tool — delegates work to subagents with real-time events."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import replace
from typing import Any

from langchain_core.tools import tool
from langgraph.config import get_stream_writer

from agent.runtime.context import RuntimeContext
from agent.runtime.subagents import (
    SubagentConfig,
    SubagentExecutor,
    SubagentStatus,
    cleanup_background_task,
    get_available_subagent_names,
    get_background_task_result,
    get_subagent_config,
    request_cancel_background_task,
)

logger = logging.getLogger(__name__)


def _resolve_subagent_model(config: SubagentConfig, parent_model: str | None) -> str:
    if config.model != "inherit":
        return config.model
    if parent_model:
        return parent_model
    from common.config import settings
    return settings.primary_model


@tool("task", parse_docstring=True)
async def task_tool(
    description: str,
    prompt: str,
    subagent_type: str = "general-purpose",
) -> str:
    """Delegate a task to a specialized subagent with its own context.

    Subagents help preserve context by keeping exploration and implementation separate.
    Use for complex multi-step tasks, parallel research, or code exploration.

    Built-in types: general-purpose, researcher, coder.

    When to use:
    - Complex tasks requiring multiple steps
    - Tasks that produce verbose output
    - Isolating context from the main conversation
    - Parallel research tasks

    When NOT to use:
    - Simple single-step operations (use tools directly)
    - Tasks requiring user interaction

    Args:
        description: Short description for logging (3-5 words).
        prompt: Detailed task instructions for the subagent.
        subagent_type: Type of subagent — general-purpose, researcher, or coder.
    """
    config = get_subagent_config(subagent_type)
    if config is None:
        available = ", ".join(get_available_subagent_names())
        return f"Error: Unknown subagent type '{subagent_type}'. Available: {available}"

    # Get available tools (excluding task to prevent recursive nesting)
    from common.config import settings
    from agent.workflows.agent_tools import build_agent_tools
    from agent.runtime.tool_registry import build_runtime_tools

    context = RuntimeContext(subagent_enabled=False)

    tools = build_runtime_tools(context, subagent_enabled=False)

    model_name = _resolve_subagent_model(config, settings.primary_model)

    executor = SubagentExecutor(
        config=config,
        tools=tools,
        parent_model=model_name,
        runtime_context=context,
        trace_id=uuid.uuid4().hex[:8],
    )

    task_id = executor.execute_async(prompt)

    polls = max(1, (config.timeout_seconds + 60) // 5)
    logger.info("Started subagent task %s (%s): %s", task_id, subagent_type, description)

    try:
        writer = get_stream_writer()
        writer({"type": "task_started", "task_id": task_id, "description": description})
    except Exception:
        writer = None

    try:
        for i in range(polls):
            result = get_background_task_result(task_id)
            if result is None:
                cleanup_background_task(task_id)
                return f"Error: Task {task_id} disappeared from background"

            if writer and result.ai_messages:
                for idx, msg in enumerate(result.ai_messages):
                    writer({
                        "type": "task_running",
                        "task_id": task_id,
                        "message": msg,
                        "message_index": idx + 1,
                    })

            if result.status == SubagentStatus.COMPLETED:
                msg = f"Task Succeeded. Result: {result.result or ''}"
                if writer:
                    writer({"type": "task_completed", "task_id": task_id})
                cleanup_background_task(task_id)
                return msg

            if result.status in {SubagentStatus.FAILED, SubagentStatus.CANCELLED, SubagentStatus.TIMED_OUT}:
                msg = f"Task {result.status.value}. Error: {result.error or 'unknown'}"
                if writer:
                    writer({"type": f"task_{result.status.value}", "task_id": task_id, "error": result.error})
                cleanup_background_task(task_id)
                return msg

            await asyncio.sleep(5)

        # Polling timeout
        result = get_background_task_result(task_id)
        cleanup_background_task(task_id)
        return f"Task timed out after {config.timeout_seconds}s polling"
    except asyncio.CancelledError:
        request_cancel_background_task(task_id)
        cleanup_background_task(task_id)
        raise
