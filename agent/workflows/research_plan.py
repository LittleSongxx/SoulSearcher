"""Research Plan node — HITL plan approval before supervisor execution.

Follows Google Gemini Deep Research's "plan first, user approves, then execute"
pattern and Anthropic's Prompt Chaining with gates between steps.

When the task is classified as standard or deep, this node generates a structured
research plan and pauses the graph (via LangGraph interrupt) for user review.
The user can approve, revise, or cancel before the expensive supervisor loop starts.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command, interrupt

from agent.core.configuration import ResearchConfiguration
from agent.core.model_routing import configurable_model
from agent.core.state import AgentState

logger = logging.getLogger(__name__)

RESEARCH_PLAN_PROMPT = """You are a senior research strategist.  Based on the research
brief below, create a concrete, actionable research plan.

<Research Brief>
{research_brief}
</Research Brief>

Today's date: {date}
Estimated complexity: {complexity}
Estimated depth: {estimated_depth}, breadth: {estimated_breadth}

Produce a research plan with these sections:

## Sub-topics
List 2–5 specific sub-topics that need to be investigated.  Each sub-topic
should be a well-scoped question or investigation angle.

## Search Strategy
For each sub-topic, suggest 2–3 specific search queries.  Prefer queries that
are likely to surface authoritative, primary sources.

## Source Preferences
What kinds of sources to prioritise (academic papers, official documentation,
news reports, industry analyses, etc.) and any domains to prefer or avoid.

## Expected Output
What the final report should contain — key sections, types of evidence expected,
and any specific format requirements.

## Confidence Assessment
Your confidence that this plan covers the research brief adequately (low/medium/high)
and any areas where user input would be especially valuable.

Format the plan in clear Markdown so it can be shown directly to the user for review."""


async def plan_research(
    state: AgentState, config: RunnableConfig
) -> Command[Literal["research_supervisor", "__end__"]]:
    """Generate research plan and pause for user approval (HITL).

    This follows:
    - Google Gemini Deep Research: plan → user revises/approves → execute
    - Anthropic Prompt Chaining: gate between planning and execution steps
    - open_deep_research: HITL interrupt for plan approval
    """
    research_config = ResearchConfiguration.from_runnable_config(config)
    research_brief = state.get("research_brief", "")
    complexity = state.get("complexity", "standard")
    depth = state.get("estimated_depth", 1)
    breadth = state.get("estimated_breadth", 2)

    # Use strategic model for plan generation — this is a high-leverage decision
    model_name = research_config.get_model_for_task("strategic_decision")
    model_config = {
        "model": model_name,
        "max_tokens": 2048,
        "tags": ["langsmith:nostream"],
    }

    prompt = RESEARCH_PLAN_PROMPT.format(
        research_brief=research_brief,
        date=datetime.now().strftime("%Y-%m-%d"),
        complexity=complexity,
        estimated_depth=depth,
        estimated_breadth=breadth,
    )

    llm = configurable_model.with_config(model_config)
    response = await llm.ainvoke([
        SystemMessage(content="You are a research strategist. Produce detailed, actionable plans."),
        HumanMessage(content=prompt),
    ])

    plan_content = response.content if hasattr(response, "content") else str(response)
    logger.info(f"[ResearchPlan] Generated plan ({len(plan_content)} chars)")

    # Pause for user approval via LangGraph interrupt (Google Gemini pattern).
    # The plan is shown to the user, who can approve, revise, or cancel.
    approval = interrupt({
        "type": "research_plan",
        "plan": plan_content,
        "research_brief": research_brief,
        "complexity": complexity,
        "estimated_depth": depth,
        "estimated_breadth": breadth,
        "message": (
            "I've created a research plan.  Please review it and choose: "
            "'approve' to proceed, 'revise' with a modified plan, or 'cancel' "
            "to stop the research."
        ),
    })

    if not isinstance(approval, dict):
        logger.warning("[ResearchPlan] Invalid approval payload, defaulting to approve")
        approval = {"action": "approve"}

    action = str(approval.get("action", "approve")).strip().lower()

    if action == "cancel":
        logger.info("[ResearchPlan] User cancelled research")
        return Command(
            goto="__end__",
            update={
                "messages": [
                    AIMessage(content="Research cancelled by user at plan review stage.")
                ],
            },
        )

    if action == "revise":
        revised_plan = str(approval.get("revised_plan", "") or "")
        if not revised_plan:
            # User provided feedback instead of a full revised plan — adjust the plan
            feedback = str(approval.get("feedback", "") or "")
            revised = await _revise_plan_with_feedback(
                plan_content, feedback, research_config
            )
            plan_content = revised
        else:
            plan_content = revised_plan
        logger.info("[ResearchPlan] Plan revised by user")

    # Proceed to supervisor with the approved plan injected
    return Command(
        goto="research_supervisor",
        update={
            "messages": [
                AIMessage(content=f"Research plan approved.\n\n{plan_content}")
            ],
            # Inject plan as additional context for the supervisor
            "research_brief": (
                f"{research_brief}\n\n"
                f"[Approved Research Plan]\n{plan_content}"
            ),
        },
    )


async def _revise_plan_with_feedback(
    plan: str, feedback: str, config: ResearchConfiguration
) -> str:
    """Revise the plan based on user feedback using fast model."""
    model_config = {
        "model": config.fast_llm,
        "max_tokens": 2048,
        "tags": ["langsmith:nostream"],
    }
    prompt = (
        f"Original plan:\n{plan}\n\n"
        f"User feedback:\n{feedback}\n\n"
        f"Revise the plan incorporating the user's feedback. "
        f"Keep the same structure and level of detail."
    )
    response = await configurable_model.with_config(model_config).ainvoke([
        HumanMessage(content=prompt),
    ])
    return response.content if hasattr(response, "content") else str(response)
