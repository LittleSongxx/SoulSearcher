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

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, get_buffer_string
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from agent.core.configuration import ResearchConfiguration
from agent.core.model_routing import configurable_model
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
    model_config = {
        "model": research_config.smart_llm,
        "max_tokens": 1024,
        "tags": ["langsmith:nostream"],
    }

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

    model_config = {
        "model": research_model_name,
        "max_tokens": research_config.research_model_max_tokens,
        "tags": ["langsmith:nostream"],
    }

    research_model = (
        configurable_model
        .with_structured_output(ResearchQuestion)
        .with_retry(stop_after_attempt=research_config.max_structured_output_retries)
        .with_config(model_config)
    )

    # Build skill context (deer-flow pattern: inject SKILL.md guidance)
    skill_context = _build_skill_context(state)

    prompt = resolve_prompt("research_brief",
        messages=get_buffer_string(messages),
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
    - deep → research_supervisor (Orchestrator-Workers: supervisor + N researchers)
    """
    research_config = ResearchConfiguration.from_runnable_config(config)

    research_brief = state.get("research_brief", "")

    model_config = {
        "model": research_config.fast_llm,  # Fast model for simple classification
        "max_tokens": 512,
        "tags": ["langsmith:nostream"],
    }

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
    else:
        # deep → Orchestrator-Workers pipeline
        # Route through the plan gate (HITL: plan → approve → execute)
        logger.info("[Complexity] Routing to plan_research (deep → Orchestrator-Workers)")
        return Command(
            goto="plan_research",
            update={
                "complexity": "deep",
                "estimated_depth": 2,
                "estimated_breadth": 4,
            },
        )


# =============================================================================
# Fast Path: Direct Answer (for simple queries)
# =============================================================================

async def direct_answer(state: AgentState, config: RunnableConfig) -> dict:
    """Answer simple questions directly without the full research pipeline.

    For simple queries (e.g., "What is the capital of France?"), this
    provides a fast, cost-effective answer using the fast_llm.

    Pattern from Weaver's existing direct_answer_node.
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

    model_config = {
        "model": research_config.fast_llm,
        "max_tokens": research_config.final_report_model_max_tokens,
        "tags": ["langsmith:nostream"],
    }

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

def _build_skill_context(state: AgentState) -> str:
    """Build skill context string from active SKILL.md files (deer-flow pattern).

    Loads real SKILL.md files from the skills directory and extracts structured
    guidance (name, description, methodology) for injection into the research
    brief prompt. Falls back to skill_ids-only context if parsing fails.
    """
    skill_ids = state.get("skill_ids", [])
    if not skill_ids:
        return ""

    parts = ["\n\n<Skill Guidance>\nThe following research methodology skills are active:\n"]
    skills_loaded = 0

    # === Try loading from real SKILL.md files via the parser ===
    try:
        from agent.skills.parser import parse_skill_file
        from agent.skills.types import SkillCategory

        # Determine skills root directory
        import os
        skills_base = os.environ.get(
            "WEAVER_SKILLS_PATH",
            os.path.join(os.path.dirname(__file__), "..", "..", "skills", "public"),
        )
        skills_base = os.path.abspath(skills_base)

        if os.path.isdir(skills_base):
            for entry in sorted(os.listdir(skills_base)):
                entry_path = os.path.join(skills_base, entry)
                if not os.path.isdir(entry_path):
                    continue

                skill_file = os.path.join(entry_path, "SKILL.md")
                if not os.path.isfile(skill_file):
                    continue

                # Check if this skill is in the user's skill_ids
                skill_name = entry
                if skill_ids and skill_name not in skill_ids:
                    continue

                try:
                    from pathlib import Path
                    skill = parse_skill_file(
                        Path(skill_file),
                        SkillCategory.PUBLIC,
                        Path(entry),
                    )
                    if skill:
                        parts.append(f"- **{skill.name}**: {skill.description}")
                        skills_loaded += 1

                        # Also extract key sections (overview, methodology, when to use)
                        content = open(skill_file, "r", encoding="utf-8").read()
                        for section in ["## When to Use", "## Core Principle", "## Research Methodology"]:
                            section_start = content.find(section)
                            if section_start >= 0:
                                # Extract until next ## heading
                                next_heading = content.find("\n## ", section_start + len(section) + 1)
                                section_text = (
                                    content[section_start:next_heading]
                                    if next_heading > 0
                                    else content[section_start:2000]
                                )
                                if len(section_text) > 20:
                                    parts.append(f"  {section_text[:500]}...\n")
                                    break
                except Exception:
                    parts.append(f"- Skill: {skill_name}")
    except ImportError:
        logger.debug("[SkillContext] Skill parser not available, using skill_ids only")
    except Exception:
        logger.warning("[SkillContext] Failed to parse skills, using skill_ids only", exc_info=True)

    # === Fallback: list skill_ids if no files loaded ===
    if skills_loaded == 0:
        for skill_id in skill_ids:
            parts.append(f"- Skill: {skill_id}")

    parts.append("\nApply the methodology from these skills during research.\n")
    parts.append("</Skill Guidance>\n")
    return "\n".join(parts)
