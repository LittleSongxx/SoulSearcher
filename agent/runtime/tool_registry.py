from __future__ import annotations

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

from agent.runtime.context import RuntimeContext
from agent.runtime.sandbox_policy import should_expose_host_bash
from agent.workflows.agent_tools import build_agent_tools

logger = logging.getLogger(__name__)


def _tool_name(tool: Any) -> str:
    return str(getattr(tool, "name", "") or tool.__class__.__name__)


def _without_host_bash(tools: list[BaseTool]) -> list[BaseTool]:
    if should_expose_host_bash():
        return tools
    denied = {"safe_bash", "bash"}
    filtered = [tool for tool in tools if _tool_name(tool) not in denied]
    if len(filtered) != len(tools):
        logger.info("Host bash tool hidden by sandbox policy")
    return filtered


def _dedupe(tools: list[BaseTool]) -> list[BaseTool]:
    seen: set[str] = set()
    out: list[BaseTool] = []
    for tool in tools:
        name = _tool_name(tool)
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(tool)
    return out


def build_runtime_tools(
    context: RuntimeContext | RunnableConfig | dict[str, Any] | None = None,
    *,
    subagent_enabled: bool | None = None,
) -> list[BaseTool]:
    """Build the unified runtime tool list.

    Tools are assembled from agent profiles and extended with the task()
    delegation surface when subagents are enabled. Deep research is now
    skill-driven — the deep-research skill guides methodology using
    standard tools (web_search, web_fetch, etc.).
    """
    if isinstance(context, RuntimeContext):
        runtime_context = context
        config = runtime_context.runnable_config()
    elif isinstance(context, dict):
        cfg = context.get("configurable") if "configurable" in context else context
        runtime_context = RuntimeContext.from_configurable(cfg if isinstance(cfg, dict) else {})
        config = context if "configurable" in context else runtime_context.runnable_config()
    else:
        runtime_context = RuntimeContext()
        config = runtime_context.runnable_config()

    tools = build_agent_tools(config)
    tools = _without_host_bash(tools)

    enabled = runtime_context.subagent_enabled if subagent_enabled is None else subagent_enabled
    if enabled:
        from agent.runtime.task_tool import task_tool

        tools.append(task_tool)

    return _dedupe(tools)
