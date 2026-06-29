"""Input Gateway: Clarify → ResearchBrief → Complexity Classifier.

First stage of the unified Deep Research pipeline.
Integrates patterns from:
- open_deep_research: clarify_with_user + write_research_brief
- gpt-researcher: adaptive complexity-based routing
- deer-flow: skill context injection

The three nodes form a sequential pipeline that determines:
1. Whether the user's query needs clarification
2. What the structured research brief should be
3. How complex the task is (simple/standard/deep) → determines the execution path
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, get_buffer_string
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from agent.core.configuration import ResearchConfiguration
from agent.core.model_routing import build_model_config, configurable_model
from agent.core.prompts import (
    resolve_prompt,
)
from agent.core.state import (
    AgentState,
    ClarifyWithUser,
    ComplexityAssessment,
    ResearchQuestion,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Node 1: Clarify with User
# =============================================================================

async def clarify_with_user(
    state: AgentState, config: RunnableConfig
) -> Command[Literal["write_research_brief", "__end__"]]:
    """Analyze user messages and ask clarifying questions if scope is unclear.

    If clarification is disabled or not needed, proceeds directly to research brief.
    If clarification is needed, ends the graph with a question for the user.

    Pattern from open_deep_research: clarify_with_user node.
    """
    research_config = ResearchConfiguration.from_runnable_config(config)

    if not research_config.allow_clarification:
        logger.info("[Clarify] Clarification disabled, proceeding to research brief")
        return Command(goto="write_research_brief")

    messages = state.get("messages", [])

    # Use Pydantic structured output for reliable parsing
    model_config = build_model_config(
        model=research_config.smart_llm,
        max_tokens=1024,
        tags=["langsmith:nostream"],
    )

    clarification_model = (
        configurable_model
        .with_structured_output(ClarifyWithUser)
        .with_retry(stop_after_attempt=research_config.max_structured_output_retries)
        .with_config(model_config)
    )

    prompt = resolve_prompt("clarify_with_user",
        messages=get_buffer_string(messages),
        date=datetime.now().strftime("%Y-%m-%d"),
    )

    # === Image Injection (multimodal — deer-flow pattern) ===
    # Inject user-uploaded images so vision-capable models can see them
    user_images = state.get("images", [])
    content = prompt
    if user_images:
        try:
            from agent.workflows.multimodal import build_multimodal_content
            content = build_multimodal_content(prompt, images=user_images)
            if isinstance(content, list):
                logger.info(
                    f"[Clarify] Injected {len(user_images)} user image(s) "
                    f"into LLM context"
                )
        except ImportError:
            logger.debug("[Clarify] Multimodal support not available")

    response = await clarification_model.ainvoke([HumanMessage(content=content)])

    if response.need_clarification:
        logger.info("[Clarify] Clarification needed, ending graph")
        return Command(
            goto="__end__",
            update={
                "messages": [AIMessage(content=response.question)],
                "needs_clarification": True,
            },
        )
    else:
        logger.info("[Clarify] Sufficient info, proceeding to research brief")
        return Command(
            goto="write_research_brief",
            update={
                "messages": [AIMessage(content=response.verification)],
                "needs_clarification": False,
            },
        )


# =============================================================================
# Node 2: Write Research Brief
# =============================================================================

async def write_research_brief(
    state: AgentState, config: RunnableConfig
) -> Command[Literal["classify_complexity"]]:
    """Transform user messages into a structured research brief.

    This brief guides the entire subsequent research process.
    Skill context from deer-flow's skill system is injected here.

    Pattern from open_deep_research: write_research_brief node.
    Enhanced with: skill context injection from deer-flow.
    """
    research_config = ResearchConfiguration.from_runnable_config(config)

    messages = state.get("messages", [])
    complexity = state.get("complexity", "standard")

    # Determine research model based on complexity hint (default to smart_llm)
    research_model_name = research_config.get_model_for_complexity(complexity)

    model_config = build_model_config(
        model=research_model_name,
        max_tokens=research_config.research_model_max_tokens,
        tags=["langsmith:nostream"],
    )

    research_model = (
        configurable_model
        .with_structured_output(ResearchQuestion)
        .with_retry(stop_after_attempt=research_config.max_structured_output_retries)
        .with_config(model_config)
    )

    message_text = get_buffer_string(messages)
    # Build skill context (deer-flow pattern: inject SKILL.md guidance)
    skill_context = _build_skill_context(state, message_text)

    prompt = resolve_prompt("research_brief",
        messages=message_text,
        date=datetime.now().strftime("%Y-%m-%d"),
        skill_context=skill_context,
    )

    response = await research_model.ainvoke([HumanMessage(content=prompt)])

    logger.info(f"[ResearchBrief] Generated brief: {response.research_brief[:200]}...")

    return Command(
        goto="classify_complexity",
        update={
            "research_brief": response.research_brief,
        },
    )


# =============================================================================
# Node 3: Complexity Classifier
# =============================================================================

async def classify_complexity(
    state: AgentState, config: RunnableConfig
) -> Command[Literal["direct_answer", "plan_research"]]:
    """Classify the research task complexity and route accordingly.

    - simple → direct_answer (fast path, single LLM call, no supervisor)
    - standard/deep → plan_research (HITL plan gate before supervisor)
    """
    research_config = ResearchConfiguration.from_runnable_config(config)

    research_brief = state.get("research_brief", "")

    model_config = build_model_config(
        model=research_config.fast_llm,
        max_tokens=512,
        tags=["langsmith:nostream"],
    )

    classifier_model = (
        configurable_model
        .with_structured_output(ComplexityAssessment)
        .with_retry(stop_after_attempt=2)
        .with_config(model_config)
    )

    prompt = resolve_prompt("complexity_classifier", research_brief=research_brief)
    response = await classifier_model.ainvoke([HumanMessage(content=prompt)])

    logger.info(
        f"[Complexity] Classified as '{response.complexity}' "
        f"(depth={response.estimated_depth}, breadth={response.estimated_breadth})"
    )

    if response.complexity == "simple":
        logger.info("[Complexity] Routing to direct_answer (fast path)")
        return Command(
            goto="direct_answer",
            update={
                "complexity": "simple",
                "estimated_depth": 1,
                "estimated_breadth": 1,
            },
        )
    if response.complexity == "standard":
        logger.info("[Complexity] Routing to plan_research (standard → plan gate)")
        return Command(
            goto="plan_research",
            update={
                "complexity": "standard",
                "estimated_depth": max(1, int(response.estimated_depth or 2)),
                "estimated_breadth": max(1, int(response.estimated_breadth or 3)),
            },
        )

    # deep → Orchestrator-Workers pipeline with stronger budget defaults
    logger.info("[Complexity] Routing to plan_research (deep → plan gate)")
    return Command(
        goto="plan_research",
        update={
            "complexity": "deep",
            "estimated_depth": max(3, int(response.estimated_depth or 3)),
            "estimated_breadth": max(4, int(response.estimated_breadth or 4)),
        },
    )


# =============================================================================
# Fast Path: Direct Answer (for simple queries)
# =============================================================================

async def direct_answer(state: AgentState, config: RunnableConfig) -> dict:
    """Answer simple questions directly without the full research pipeline.

    For simple queries (e.g., "What is the capital of France?"), this
    provides a fast, cost-effective answer using the fast_llm.

    Pattern from SoulSearcher's existing direct_answer_node.
    """
    research_config = ResearchConfiguration.from_runnable_config(config)

    # Extract user input from state or messages
    user_input = state.get("input", "")
    if not user_input:
        messages = state.get("messages", [])
        for msg in messages:
            if hasattr(msg, "type") and msg.type == "human":
                user_input = str(getattr(msg, "content", ""))
                break

    model_config = build_model_config(
        model=research_config.fast_llm,
        max_tokens=research_config.final_report_model_max_tokens,
        tags=["langsmith:nostream"],
    )

    prompt = resolve_prompt("direct_answer",
        input=user_input,
        date=datetime.now().strftime("%Y-%m-%d"),
    )

    response = await configurable_model.with_config(model_config).ainvoke([
        HumanMessage(content=prompt)
    ])

    return {
        "final_report": response.content,
        "messages": [response],
    }


# =============================================================================
# Helpers
# =============================================================================

def _build_skill_context(state: AgentState, query: str = "") -> str:
    """Build skill context string from active SKILL.md files for research brief injection."""
    from agent.skills.prompt import build_skill_context
    return build_skill_context(
        state.get("skill_ids", []),
        purpose="research",
        query=query,
        max_chars=2400,
    )
