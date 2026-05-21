"""Supervisor Subgraph: Research orchestration and delegation.

The supervisor manages the research process:
1. Analyzes the research brief and plans the research strategy
2. Delegates tasks to parallel researcher subgraphs via ConductResearch
3. Reflects on progress via enhanced think_tool (structured gaps/confidence/strategy)
4. Curates sources via SourceCurate when enough info is gathered
5. Signals completion via ResearchComplete

Key integrations from the unified design:
- open_deep_research: supervisor ⇄ supervisor_tools subgraph pattern
- gpt-researcher: structured reflection with actionable data
- Unified design: SourceCurate integration + enhanced think_tool

Phase 2: ConductResearch.very_thorough for breadth×depth recursion (integrated).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Literal

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    get_buffer_string,
)
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from agent.core.configuration import ResearchConfiguration
from agent.core.model_routing import configurable_model
from agent.core.prompts import resolve_prompt
from agent.core.state import (
    ConductResearch,
    ResearchComplete,
    SourceCurate,
    SupervisorState,
    ThinkTool,
)

logger = logging.getLogger(__name__)

# Import researcher subgraph (lazy to avoid circular imports)
_researcher_subgraph = None


def _get_researcher_subgraph():
    """Lazy-import researcher subgraph to avoid circular dependencies."""
    global _researcher_subgraph
    if _researcher_subgraph is None:
        from agent.workflows.researcher import build_researcher_subgraph
        _researcher_subgraph = build_researcher_subgraph()
    return _researcher_subgraph


# =============================================================================
# Supervisor Node
# =============================================================================

async def supervisor(
    state: SupervisorState, config: RunnableConfig
) -> Command[Literal["supervisor_tools"]]:
    """Lead research supervisor: plans strategy and delegates to researchers.

    The supervisor uses LLM reasoning to:
    1. Analyze the research brief
    2. Decide which topics to research
    3. Call ConductResearch to delegate, think_tool to reflect,
       SourceCurate to filter, or ResearchComplete to finish

    Pattern from open_deep_research: supervisor node.
    Enhanced with: SourceCurate tool, complexity-aware model selection.
    """
    research_config = ResearchConfiguration.from_runnable_config(config)

    complexity = state.get("complexity", "standard")
    model_name = research_config.get_supervisor_model(complexity)

    model_config = {
        "model": model_name,
        "max_tokens": research_config.research_model_max_tokens,
        "tags": ["langsmith:nostream"],
    }

    # Available supervisor tools.
    #
    # Two delegation options (Claude Code sub-agent model):
    #   task()          — lightweight, fast model, limited tools → quick fact-check
    #   ConductResearch — full Researcher subgraph, smart model → deep investigation
    #
    # The LLM learns to use task() for simple lookups ("what is X?") and
    # ConductResearch for multi-step analysis ("compare X vs Y across dimensions A,B,C").
    # This follows Anthropic's Orchestrator-Workers pattern and Claude Code's
    # Explore (Haiku, read-only) / general-purpose (Sonnet, all tools) split.
    supervisor_tools = [ConductResearch, ThinkTool, SourceCurate, ResearchComplete]

    # Add the lightweight task() sub-agent tool (deer-flow SubagentExecutor).
    # This merges the previously separate Lead Agent + Subagent runtime into the
    # Supervisor-Worker graph, giving the supervisor a fast/cheap option for
    # simple fact-checking.
    try:
        from agent.runtime.task_tool import task_tool
        supervisor_tools.append(task_tool)
        logger.debug("[Supervisor] task() sub-agent tool available")
    except ImportError:
        logger.debug("[Supervisor] task() sub-agent tool not available")

    research_model = (
        configurable_model
        .bind_tools(supervisor_tools)
        .with_retry(stop_after_attempt=research_config.max_structured_output_retries)
        .with_config(model_config)
    )

    # Always build context prefix (system prompt + research brief)
    # so the LLM has full task context on every iteration.
    # Only the conversation history (AI responses + tool messages) is stored in state.
    system_prompt = resolve_prompt("lead_researcher",
        date=datetime.now().strftime("%Y-%m-%d"),
        max_concurrent_research_units=research_config.max_concurrent_research_units,
        max_researcher_iterations=research_config.max_researcher_iterations,
    )
    research_brief = state.get("research_brief", "")
    context_messages: list = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=research_brief),
    ]

    # === Image Injection (multimodal — deer-flow pattern) ===
    configurable = config.get("configurable") or {}
    viewed_images = configurable.get("viewed_images", {})
    if viewed_images:
        try:
            from agent.workflows.multimodal import build_image_injection_message
            injection_msg = build_image_injection_message(viewed_images)
            if injection_msg:
                context_messages.append(injection_msg)
                logger.info(
                    f"[Supervisor] Injected {len(viewed_images)} image(s) "
                    f"into LLM context"
                )
                configurable["viewed_images"] = {}
                config["configurable"] = configurable
        except ImportError:
            logger.debug("[Supervisor] Multimodal support not available")

    # Combine context prefix with conversation history from state
    supervisor_messages = context_messages + list(
        state.get("supervisor_messages", [])
    )

    # === Loop Detection (via shared middleware — Anthropic Agent SDK hooks pattern) ===
    from agent.runtime.middleware.shared import check_loop
    is_looping, _hint = check_loop(supervisor_messages)
    if is_looping:
        logger.warning("[Supervisor] Loop detected, forcing completion")
        return Command(
            goto="supervisor_tools",
            update={
                "supervisor_messages": [supervisor_messages[-1]],
                "research_iterations": state.get("research_iterations", 0) + 1,
            },
        )

    response = await research_model.ainvoke(supervisor_messages)

    # === Token Usage Tracking ===
    from agent.core.middleware import get_token_tracker
    tracker = get_token_tracker()
    usage = getattr(response, "usage_metadata", None) or {}
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    if input_tokens or output_tokens:
        tracker.record("supervisor", input_tokens, output_tokens)

    return Command(
        goto="supervisor_tools",
        update={
            "supervisor_messages": [response],
            "research_iterations": state.get("research_iterations", 0) + 1,
        },
    )


# =============================================================================
# Supervisor Tools Node
# =============================================================================

async def supervisor_tools(
    state: SupervisorState, config: RunnableConfig
) -> Command[Literal["supervisor", "__end__"]]:
    """Execute tools called by the supervisor.

    Handles five types of supervisor tool calls:
    1. ThinkTool → Record structured reflection, continue loop
    2. ConductResearch → Spawn parallel researcher subgraphs
    3. ConductResearch (very_thorough) → Breadth × depth recursive research
    4. SourceCurate → Rank and filter collected sources
    5. ResearchComplete → End supervisor loop, proceed to report

    Pattern from open_deep_research: supervisor_tools node.
    Enhanced with: structured think_tool processing, source curation.
    """
    research_config = ResearchConfiguration.from_runnable_config(config)
    supervisor_messages = state.get("supervisor_messages", [])
    research_iterations = state.get("research_iterations", 0)
    most_recent_message = supervisor_messages[-1]

    # === Exit conditions ===
    exceeded_iterations = research_iterations > research_config.max_researcher_iterations
    no_tool_calls = not most_recent_message.tool_calls
    research_complete_called = any(
        tc["name"] == "ResearchComplete"
        for tc in (most_recent_message.tool_calls or [])
    )

    if exceeded_iterations or no_tool_calls or research_complete_called:
        reason = (
            "max_iterations" if exceeded_iterations else
            "research_complete" if research_complete_called else
            "no_tool_calls"
        )
        logger.info(f"[SupervisorTools] Ending supervisor loop ({reason})")
        return Command(
            goto="__end__",
            update={
                "notes": _extract_notes_from_messages(supervisor_messages),
            },
        )

    # === Process tool calls ===
    all_tool_messages = []
    update_payload = {"supervisor_messages": []}
    tool_calls = most_recent_message.tool_calls

    # --- ThinkTool calls ---
    think_calls = [tc for tc in tool_calls if tc["name"] == "ThinkTool"]
    for tc in think_calls:
        reflection = tc["args"].get("reflection", "")
        gaps = tc["args"].get("gaps_identified", [])
        confidence = tc["args"].get("confidence_level", "medium")
        next_strategy = tc["args"].get("next_strategy", "search_more")

        think_summary = (
            f"Reflection recorded:\n"
            f"- Gaps identified: {', '.join(gaps) if gaps else 'none'}\n"
            f"- Confidence: {confidence}\n"
            f"- Next strategy: {next_strategy}\n"
            f"- Details: {reflection[:500]}"
        )
        all_tool_messages.append(ToolMessage(
            content=think_summary,
            name="ThinkTool",
            tool_call_id=tc["id"],
        ))

    # --- SourceCurate calls ---
    curate_calls = [tc for tc in tool_calls if tc["name"] == "SourceCurate"]
    if curate_calls:
        collected_urls = _collect_source_urls(state)
        update_payload["curated_sources"] = collected_urls
        for tc in curate_calls:
            all_tool_messages.append(ToolMessage(
                content=f"Sources collected. {len(collected_urls)} unique URLs will be curated during report generation.",
                name="SourceCurate",
                tool_call_id=tc["id"],
            ))

    # --- ConductResearch calls (parallel subgraph invocation) ---
    # Follows Anthropic's Orchestrator-Workers pattern and Claude Code's
    # sub-agent thoroughness levels (quick / medium / very_thorough).
    # Each call spawns an independent Researcher subgraph with configurable
    # depth×breadth derived from the thoroughness parameter.
    conduct_calls = [tc for tc in tool_calls if tc["name"] == "ConductResearch"]
    if conduct_calls:
        max_concurrent = research_config.max_concurrent_research_units
        allowed_calls = conduct_calls[:max_concurrent]
        overflow_calls = conduct_calls[max_concurrent:]

        # Map ACI thoroughness levels → depth × breadth (Claude Code model)
        _THOROUGHNESS_MAP = {
            "quick":          (1, 2),
            "medium":         (1, 4),
            "very_thorough":  (2, 4),
        }

        # Execute researcher subgraphs in parallel (open_deep_research pattern)
        research_tasks = [
            _get_researcher_subgraph().ainvoke(
                {
                    "researcher_messages": [
                        HumanMessage(
                            content=(
                                f"Research topic: {tc['args'].get('topic', tc['args'].get('research_topic', ''))}\n"
                                f"Context: {tc['args'].get('context', '')}"
                            )
                        )
                    ],
                    "research_topic": tc["args"].get("topic", tc["args"].get("research_topic", "")),
                    "thoroughness": tc["args"].get("thoroughness", "medium"),
                    "tool_call_iterations": 0,
                },
                config,
            )
            for tc in allowed_calls
        ]

        try:
            tool_results = await asyncio.gather(*research_tasks)

            for obs, tc in zip(tool_results, allowed_calls):
                compressed = obs.get(
                    "compressed_research",
                    "Error: Research synthesis failed."
                )
                all_tool_messages.append(ToolMessage(
                    content=compressed,
                    name="ConductResearch",
                    tool_call_id=tc["id"],
                ))

            # Aggregate raw notes from all parallel researchers
            raw_notes_concat = "\n".join([
                obs.get("raw_notes", [None])[0] if obs.get("raw_notes") else ""
                for obs in tool_results
            ])
            if raw_notes_concat.strip():
                update_payload["raw_notes"] = [raw_notes_concat]

            # Handle overflow — tell supervisor to retry with fewer
            for over_tc in overflow_calls:
                all_tool_messages.append(ToolMessage(
                    content=(
                        f"Too many parallel research calls ({len(conduct_calls)}) — "
                        f"only {max_concurrent} can run at once. "
                        f"Please retry the overflow topics in the next iteration."
                    ),
                    name="ConductResearch",
                    tool_call_id=over_tc["id"],
                ))

        except Exception as e:
            logger.error(f"[SupervisorTools] Research execution error: {e}")
            all_tool_messages.append(ToolMessage(
                content=f"Error during research execution: {str(e)}",
                name="ConductResearch",
                tool_call_id=conduct_calls[0]["id"] if conduct_calls else "unknown",
            ))

    # === Return to supervisor loop (with context budget enforcement) ===
    # Follows Claude Code's sub-agent principle: "the subagent does that work
    # in its own context and returns only the summary."  Old supervisor_messages
    # are trimmed to keep the LLM context focused on recent, high-signal results.
    update_payload["supervisor_messages"] = _enforce_context_budget(
        supervisor_messages + all_tool_messages,
        max_tool_message_chars=research_config.compression_small_threshold,
    )
    return Command(
        goto="supervisor",
        update=update_payload,
    )


# =============================================================================
# Supervisor Subgraph Construction
# =============================================================================

def build_supervisor_subgraph() -> StateGraph:
    """Build the supervisor subgraph for research orchestration.

    Returns a compiled subgraph that takes supervisor_messages and research_brief
    as input, manages the research delegation loop, and outputs notes.

    Graph: START → supervisor ⇄ supervisor_tools → END
    """
    builder = StateGraph(SupervisorState, config_schema=ResearchConfiguration)

    builder.add_node("supervisor", supervisor)
    builder.add_node("supervisor_tools", supervisor_tools)

    builder.add_edge(START, "supervisor")

    return builder.compile()


# =============================================================================
# Helpers
# =============================================================================

def _extract_notes_from_messages(messages: list) -> list[str]:
    """Extract key findings from supervisor messages for final report.

    Collects content from ConductResearch ToolMessage results.
    Pattern from open_deep_research: get_notes_from_tool_calls.
    """
    notes = []
    for msg in messages:
        if isinstance(msg, ToolMessage) and msg.name == "ConductResearch":
            content = msg.content or ""
            if content and "Error" not in content[:50]:
                notes.append(content)
    return notes


def _collect_source_urls(state: SupervisorState) -> list[dict]:
    """Collect source URLs from research notes for downstream curation.

    Actual quality ranking happens during report generation (report.py::curate_sources).
    This just extracts and deduplicates URLs from the supervisor's collected research.
    """
    import re

    notes = _extract_notes_from_messages(state.get("supervisor_messages", []))
    if not notes:
        return []

    url_pattern = re.compile(r'https?://[^\s)\]]+')
    seen = set()
    unique_urls = []
    for note in notes:
        for url in url_pattern.findall(note):
            if url not in seen:
                seen.add(url)
                unique_urls.append({"url": url, "title": url.split("/")[-1] or url})

    return unique_urls


# ---------------------------------------------------------------------------
# Context Budget Enforcement (Claude Code sub-agent isolation model)
# ---------------------------------------------------------------------------
# Principle from Claude Code: "the subagent does that work in its own
# context and returns only the summary."  We enforce a per-ToolMessage
# character cap and an overall message count budget so the supervisor's
# LLM context stays focused on high-signal recent results rather than
# accumulating raw research dumps across iterations.
# ---------------------------------------------------------------------------

_SUPERVISOR_MAX_MESSAGES = 40
"""Drop oldest messages beyond this count to keep the supervisor context lean."""


def _enforce_context_budget(
    messages: list,
    max_tool_message_chars: int = 8000,
) -> list:
    """Trim and compress supervisor messages to stay within context budget.

    1. Cap each ConductResearch ToolMessage at max_tool_message_chars characters
       (the Researcher already returns compressed output; this is a safety net).
    2. Drop oldest messages if total exceeds SUPERVISOR_MAX_MESSAGES, preserving
       the most recent ThinkTool reflections and the system prefix.

    Returns a new list — the caller should use this as the updated
    supervisor_messages.
    """
    capped: list = []
    for msg in messages:
        if isinstance(msg, ToolMessage) and msg.name == "ConductResearch":
            content = msg.content or ""
            if len(content) > max_tool_message_chars:
                truncated = content[:max_tool_message_chars] + (
                    f"\n\n... [truncated from {len(content)} to "
                    f"{max_tool_message_chars} chars for context budget]"
                )
                capped.append(ToolMessage(
                    content=truncated,
                    name=msg.name,
                    tool_call_id=msg.tool_call_id,
                ))
            else:
                capped.append(msg)
        else:
            capped.append(msg)

    if len(capped) <= _SUPERVISOR_MAX_MESSAGES:
        return capped

    # Keep the first message (system prompt / research brief) and the
    # most recent messages.  ThinkTool reflections are preferentially kept
    # because they carry high-signal structural information.
    keep_recent = _SUPERVISOR_MAX_MESSAGES - 1
    think_msgs = [m for m in capped[1:] if isinstance(m, ToolMessage) and m.name == "ThinkTool"]
    other_msgs = [m for m in capped[1:] if not (isinstance(m, ToolMessage) and m.name == "ThinkTool")]

    # Always keep the last few think reflections
    kept_think = think_msgs[-3:] if len(think_msgs) > 3 else think_msgs
    # Fill the rest from the most recent other messages
    remaining_slots = keep_recent - len(kept_think)
    kept_other = other_msgs[-remaining_slots:] if remaining_slots > 0 else []

    result = [capped[0]] + kept_other + kept_think
    logger.info(
        "[ContextBudget] Trimmed %d supervisor messages → %d "
        "(kept %d think reflections, %d other)",
        len(capped), len(result), len(kept_think), len(kept_other),
    )
    return result
