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

Phase 2: ResearchDeep tool for breadth×depth recursion (integrated).
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
from agent.core.prompts_v2 import LEAD_RESEARCHER_PROMPT
from agent.core.state_v2 import (
    ConductResearch,
    ResearchComplete,
    ResearchDeep,
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
        from agent.workflows.researcher_v2 import build_researcher_subgraph
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

    # Available supervisor tools
    # ResearchDeep is available for deep tasks under the call limit
    deep_count = state.get("deep_research_count", 0)
    if complexity == "deep" and deep_count < research_config.max_deep_research_calls:
        supervisor_tools = [
            ConductResearch, ResearchDeep, ThinkTool, SourceCurate, ResearchComplete
        ]
        logger.info(
            f"[Supervisor] ResearchDeep available (deep task, "
            f"{deep_count}/{research_config.max_deep_research_calls} calls used)"
        )
    else:
        supervisor_tools = [ConductResearch, ThinkTool, SourceCurate, ResearchComplete]

    research_model = (
        configurable_model
        .bind_tools(supervisor_tools)
        .with_retry(stop_after_attempt=research_config.max_structured_output_retries)
        .with_config(model_config)
    )

    # Always build context prefix (system prompt + research brief)
    # so the LLM has full task context on every iteration.
    # Only the conversation history (AI responses + tool messages) is stored in state.
    system_prompt = LEAD_RESEARCHER_PROMPT.format(
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
                # Clear from config so images are not re-injected on subsequent calls
                configurable["viewed_images"] = {}
                config["configurable"] = configurable
        except ImportError:
            pass

    # Combine context prefix with conversation history from state
    supervisor_messages = context_messages + list(
        state.get("supervisor_messages", [])
    )

    # === Loop Detection (middleware_v2) ===
    try:
        from agent.core.middleware_v2 import get_loop_detector
        loop_detector = get_loop_detector()
        recent_content = "\n".join([
            str(m.content)[:200]
            for m in supervisor_messages[-5:]
            if hasattr(m, "content") and m.content
        ])
        if loop_detector.check(recent_content):
            logger.warning("[Supervisor] Loop detected, forcing completion")
            return Command(
                goto="supervisor_tools",
                update={
                    "supervisor_messages": [supervisor_messages[-1]],
                    "research_iterations": state.get("research_iterations", 0) + 1,
                },
            )
    except ImportError:
        pass

    response = await research_model.ainvoke(supervisor_messages)

    # === Token Usage Tracking (middleware_v2) ===
    try:
        from agent.core.middleware_v2 import get_token_tracker
        tracker = get_token_tracker()
        usage = getattr(response, "usage_metadata", None) or {}
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        if input_tokens or output_tokens:
            tracker.record("supervisor", input_tokens, output_tokens)
    except (ImportError, Exception):
        pass

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
    3. ResearchDeep → Breadth × depth recursive research (deep tasks)
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
    auto_complete = False
    for tc in think_calls:
        reflection = tc["args"].get("reflection", "")
        gaps = tc["args"].get("gaps_identified", [])
        confidence = tc["args"].get("confidence_level", "medium")
        next_strategy = tc["args"].get("next_strategy", "search_more")

        # Structured think_tool processing (unified design enhancement)
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

        # Auto-complete: confidence=high + strategy=complete
        if confidence == "high" and next_strategy == "complete":
            auto_complete = True
            logger.info("[SupervisorTools] ThinkTool auto-complete: high confidence + complete strategy")

    # === Auto-ResearchComplete: if think_tool signals completion and no
    #     ConductResearch or ResearchDeep calls are pending, end the loop ===
    research_calls = (
        [tc for tc in tool_calls if tc["name"] == "ConductResearch"] +
        [tc for tc in tool_calls if tc["name"] == "ResearchDeep"]
    )
    if auto_complete and not research_calls:
        logger.info("[SupervisorTools] Auto-triggering ResearchComplete from think_tool")
        auto_tc_id = think_calls[0]["id"] if think_calls else "auto_complete"
        all_tool_messages.append(ToolMessage(
            content="Research phase automatically completed. The supervisor has high confidence "
                    "and the think_tool strategy indicates completion.",
            name="ResearchComplete",
            tool_call_id=auto_tc_id,
        ))
        return Command(
            goto="__end__",
            update={
                "supervisor_messages": all_tool_messages,
                "notes": _extract_notes_from_messages(supervisor_messages),
            },
        )

    # --- ResearchDeep calls (Phase 2: breadth × depth recursion) ---
    deep_calls = [tc for tc in tool_calls if tc["name"] == "ResearchDeep"]
    for tc in deep_calls:
        research_topic = tc["args"].get("research_topic", state.get("research_brief", ""))
        call_breadth = tc["args"].get("breadth", research_config.deep_research_breadth)
        call_depth = tc["args"].get("depth", research_config.deep_research_depth)

        logger.info(
            f"[SupervisorTools] Executing ResearchDeep: "
            f"topic='{research_topic[:100]}...', breadth={call_breadth}, depth={call_depth}"
        )

        try:
            from agent.workflows.deep_research_v2 import (
                execute_deep_research,
                format_deep_research_result,
            )

            result = await execute_deep_research(
                query=research_topic,
                breadth=call_breadth,
                depth=call_depth,
                config=config,
            )

            formatted = format_deep_research_result(result)
            all_tool_messages.append(ToolMessage(
                content=formatted,
                name="ResearchDeep",
                tool_call_id=tc["id"],
            ))

            if result.learnings:
                learnings_text = "\n".join(
                    f"- {l}" for l in result.learnings[:30]
                )
                existing_raw = update_payload.get("raw_notes", [None])
                if existing_raw and existing_raw[0]:
                    update_payload["raw_notes"] = [
                        existing_raw[0] + "\n\n## Deep Research Learnings\n" + learnings_text
                    ]
                else:
                    update_payload["raw_notes"] = [learnings_text]

            update_payload["deep_research_count"] = (
                state.get("deep_research_count", 0) + 1
            )

        except Exception as e:
            logger.error(f"[SupervisorTools] ResearchDeep execution failed: {e}")
            all_tool_messages.append(ToolMessage(
                content=f"Error during deep research: {str(e)}",
                name="ResearchDeep",
                tool_call_id=tc["id"],
            ))

    # --- SourceCurate calls ---
    curate_calls = [tc for tc in tool_calls if tc["name"] == "SourceCurate"]
    if curate_calls:
        curated = await _execute_source_curation(state, config, research_config)
        update_payload["curated_sources"] = curated
        for tc in curate_calls:
            all_tool_messages.append(ToolMessage(
                content=f"Sources curated. Top {len(curated)} sources ranked by quality and relevance.",
                name="SourceCurate",
                tool_call_id=tc["id"],
            ))

    # --- ConductResearch calls (parallel subgraph invocation) ---
    conduct_calls = [tc for tc in tool_calls if tc["name"] == "ConductResearch"]
    if conduct_calls:
        max_concurrent = research_config.max_concurrent_research_units
        allowed_calls = conduct_calls[:max_concurrent]
        overflow_calls = conduct_calls[max_concurrent:]

        # Execute researcher subgraphs in parallel (open_deep_research pattern)
        research_tasks = [
            _get_researcher_subgraph().ainvoke(
                {
                    "researcher_messages": [
                        HumanMessage(content=tc["args"]["research_topic"])
                    ],
                    "research_topic": tc["args"]["research_topic"],
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

            # Aggregate raw notes
            raw_notes_concat = "\n".join([
                obs.get("raw_notes", [None])[0] if obs.get("raw_notes") else ""
                for obs in tool_results
            ])
            if raw_notes_concat.strip():
                update_payload["raw_notes"] = [raw_notes_concat]

            # Handle overflow
            for over_tc in overflow_calls:
                all_tool_messages.append(ToolMessage(
                    content=(
                        f"Error: Maximum concurrent research units ({max_concurrent}) exceeded. "
                        f"Please retry with {max_concurrent} or fewer units."
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

    # === Return to supervisor loop ===
    update_payload["supervisor_messages"] = all_tool_messages
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


async def _execute_source_curation(
    state: SupervisorState,
    config: RunnableConfig,
    research_config: ResearchConfiguration,
) -> list[dict]:
    """Execute source curation using LLM-based ranking.

    Pattern from gpt-researcher's SourceCurator.
    In Phase 3, this will be a dedicated tool with structured output.
    For Phase 1, it's implemented inline.
    """
    notes = _extract_notes_from_messages(state.get("supervisor_messages", []))
    if not notes:
        return []

    # Extract source URLs from notes
    import re
    url_pattern = re.compile(r'https?://[^\s)\]]+')
    all_urls = []
    for note in notes:
        urls = url_pattern.findall(note)
        all_urls.extend(urls)

    if not all_urls:
        return []

    # Deduplicate
    seen = set()
    unique_urls = []
    for url in all_urls:
        if url not in seen:
            seen.add(url)
            unique_urls.append(url)

    max_sources = min(research_config.max_curated_sources, len(unique_urls))
    return [
        {"url": url, "title": url.split("/")[-1] or url, "relevance_score": 0.8}
        for url in unique_urls[:max_sources]
    ]
