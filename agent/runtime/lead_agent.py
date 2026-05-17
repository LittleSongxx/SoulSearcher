"""DeerFlow-aligned lead agent factory with full middleware chain."""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from langchain_core.messages import HumanMessage, SystemMessage

from agent.runtime.context import RuntimeContext
from agent.runtime.events import extract_last_ai_text, legacy_event
from agent.runtime.tool_registry import build_runtime_tools
from agent.workflows.agent_factory import build_tool_agent
from common.config import settings

logger = logging.getLogger(__name__)


def build_lead_agent(
    context: RuntimeContext,
    *,
    agent_name: str | None = None,
    plan_mode: bool = False,
    vision_enabled: bool = False,
):
    """Build Weaver's DeerFlow-style Lead Agent with full middleware chain.

    Args:
        context: Runtime context with thread/user/model info.
        agent_name: Optional custom agent name for per-agent memory/skills.
        plan_mode: Enable TodoList middleware for plan-mode task tracking.
        vision_enabled: Enable ViewImage middleware for vision-capable models.
    """
    model = context.model or settings.primary_model
    tools = build_runtime_tools(context, subagent_enabled=context.subagent_enabled)

    from agent.runtime.middleware import build_lead_middlewares
    from agent.skills.storage import get_or_new_skill_storage
    from agent.skills.tool_policy import filter_tools_by_skill_allowed_tools

    middlewares = build_lead_middlewares(
        agent_name=agent_name,
        thread_id=context.thread_id,
        user_id=context.user_id,
        subagent_enabled=context.subagent_enabled,
        max_concurrent_subagents=getattr(settings, "agent_runtime_max_concurrent_subagents", 3),
        plan_mode=plan_mode,
        vision_enabled=vision_enabled,
        memory_enabled=getattr(settings, "memory_enabled", True),
        summarization_enabled=getattr(settings, "summarization_enabled", True),
        loop_detection_enabled=getattr(settings, "loop_detection_enabled", True),
    )

    # Filter tools by enabled skills
    try:
        storage = get_or_new_skill_storage()
        enabled_skills = storage.load_skills(enabled_only=True)
        tools = filter_tools_by_skill_allowed_tools(tools, enabled_skills)
    except Exception:
        logger.debug("Skill tool filtering skipped", exc_info=True)

    logger.info(
        "Building lead agent thread=%s model=%s tools=%d middlewares=%d subagents=%s",
        context.thread_id, model, len(tools), len(middlewares), context.subagent_enabled,
    )

    return build_tool_agent(
        model=model,
        tools=tools,
        temperature=0.6,
        middlewares=middlewares,
    )


def _system_prompt(context: RuntimeContext) -> str:
    """Build the static system prompt (dynamic parts injected via middleware)."""
    from agent.prompts.system_prompts import get_agent_prompt

    return get_agent_prompt(
        mode="agent",
        context={
            "enabled_tools": [],
            "prompt_pack": "deepsearch",
            "prompt_variant": "full",
            "subagent_enabled": context.subagent_enabled,
        },
    )


async def stream_lead_agent_legacy_events(
    query: str,
    context: RuntimeContext,
) -> AsyncIterator[str]:
    """Run the lead agent and yield Weaver legacy event lines.

    Compatibility stream for callers that expect the current 0:{json} protocol.
    """
    yield legacy_event("status", {
        "thread_id": context.thread_id,
        "message": "Lead agent started",
        "agent_runtime": True,
    })

    agent = build_lead_agent(context)
    messages = [
        SystemMessage(content=_system_prompt(context)),
        HumanMessage(content=query),
    ]

    try:
        result = await asyncio.to_thread(
            agent.invoke,
            {"messages": messages},
            config=context.runnable_config(),
        )
        text = extract_last_ai_text(result)
        yield legacy_event("completion", {
            "thread_id": context.thread_id,
            "content": text,
            "is_final": True,
        })
        yield legacy_event("done", {"thread_id": context.thread_id})
    except Exception as exc:
        logger.exception("Lead agent runtime failed")
        yield legacy_event("error", {
            "thread_id": context.thread_id,
            "message": str(exc),
            "agent_runtime": True,
        })
        yield legacy_event("done", {"thread_id": context.thread_id})
