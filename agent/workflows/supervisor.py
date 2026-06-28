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

Phase 2: ConductResearch exhaustive effort for breadth×depth recursion (integrated).
"""

from __future__ import annotations

import asyncio
import logging
from copy import copy
from datetime import datetime
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import START, StateGraph
from langgraph.types import Command

from agent.core.configuration import ResearchConfiguration
from agent.core.model_routing import build_model_config, configurable_model
from agent.core.prompts import resolve_prompt
from agent.core.state import (
    ConductResearch,
    ResearchComplete,
    SourceCurate,
    SupervisorState,
    ThinkTool,
)
from agent.workflows.research_todo import (
    append_gap_todos,
    emit_todo_updates,
    ensure_todo_for_topic,
    format_todo_context,
    mark_todo_blocked,
    mark_todo_completed,
    mark_todo_running,
    summarize_todos,
)
from agent.runtime.context import clear_viewed_images, get_viewed_images, merge_viewed_images
from agent.runtime.middleware.shared import enforce_context_budget

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

    model_config = build_model_config(
        model=model_name,
        max_tokens=research_config.research_model_max_tokens,
        tags=["langsmith:nostream"],
    )

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
    system_prompt += (
        "\n\n<Research Effort Guidance>\n"
        "- quick: narrow fact-check or one source family.\n"
        "- normal: default multi-source synthesis.\n"
        "- thorough: complex comparison or multiple evidence families.\n"
        "- exhaustive: rare, high-stakes, broad, or deeply multi-dimensional work.\n"
        "Prefer the ConductResearch research_effort field. Legacy thoroughness "
        "values are accepted but should not be used for new calls. Use exhaustive "
        "only when the overall task is deep and the classifier estimated high "
        "depth/breadth.\n"
        "</Research Effort Guidance>"
    )
    research_brief = state.get("research_brief", "")
    context_messages: list = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=research_brief),
    ]
    todo_context = format_todo_context(state.get("research_todos", []))
    if todo_context:
        context_messages.append(SystemMessage(content=todo_context))

    # === Image Injection (multimodal — deer-flow pattern) ===
    viewed_images = get_viewed_images(config)
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
                clear_viewed_images(config)
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
    tracker = get_token_tracker(config)
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
    3. ConductResearch (exhaustive effort) → Breadth × depth recursive research
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

    if exceeded_iterations or no_tool_calls:
        reason = (
            "max_iterations" if exceeded_iterations else
            "no_tool_calls"
        )
        logger.info(f"[SupervisorTools] Ending supervisor loop ({reason})")
        update = {
            "notes": _extract_notes_from_messages(supervisor_messages),
        }
        return Command(
            goto="__end__",
            update=update,
        )

    # === Process tool calls ===
    all_tool_messages = []
    update_payload = {"supervisor_messages": []}
    tool_calls = most_recent_message.tool_calls
    current_todos = list(state.get("research_todos", []) or [])
    previous_todos = list(current_todos)
    thread_id = str(config.get("configurable", {}).get("thread_id", "default"))

    if research_complete_called:
        guard = _research_completion_guard(state, supervisor_messages)
        for tc in tool_calls or []:
            if tc["name"] != "ThinkTool":
                continue
            gaps = tc.get("args", {}).get("gaps_identified", [])
            current_todos = append_gap_todos(
                current_todos,
                gaps if isinstance(gaps, list) else [],
            )

        if guard["allowed"]:
            logger.info("[SupervisorTools] Ending supervisor loop (research_complete)")
            update = {
                "notes": _extract_notes_from_messages(supervisor_messages),
            }
            if current_todos:
                await emit_todo_updates(thread_id, current_todos, previous_todos)
                update["research_todos"] = {
                    "type": "override",
                    "value": current_todos,
                }
                update["todo_summary"] = summarize_todos(current_todos)
            return Command(
                goto="__end__",
                update=update,
            )

        current_todos = append_gap_todos(current_todos, guard["gaps"])
        await emit_todo_updates(thread_id, current_todos, previous_todos)
        all_tool_messages.extend(
            _completion_guard_messages(tool_calls, guard)
        )
        update_payload["research_todos"] = {
            "type": "override",
            "value": current_todos,
        }
        update_payload["todo_summary"] = summarize_todos(current_todos)
        update_payload["supervisor_messages"] = enforce_context_budget(
            supervisor_messages + all_tool_messages,
            max_messages=_SUPERVISOR_MAX_MESSAGES,
            max_chars_per_tool_result=research_config.compression_small_threshold,
        )
        logger.info(
            "[SupervisorTools] ResearchComplete blocked by deterministic guard: %s",
            "; ".join(guard["reasons"]),
        )
        return Command(
            goto="supervisor",
            update=update_payload,
        )

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
        current_todos = append_gap_todos(
            current_todos,
            gaps if isinstance(gaps, list) else [],
        )

    # --- SourceCurate calls ---
    curate_calls = [tc for tc in tool_calls if tc["name"] == "SourceCurate"]
    if curate_calls:
        collected_urls = _collect_source_urls(state)
        max_sources = max(
            int(tc.get("args", {}).get("max_sources") or 0)
            for tc in curate_calls
        ) or research_config.max_curated_sources
        try:
            from agent.workflows.report import curate_sources
            curated_sources = await curate_sources(
                research_topic=state.get("research_brief", ""),
                sources=collected_urls,
                config=config,
                max_sources=max_sources,
            )
        except Exception as e:
            logger.warning("[SupervisorTools] Source curation failed: %s", e)
            curated_sources = collected_urls[:max_sources]
        update_payload["curated_sources"] = curated_sources
        for tc in curate_calls:
            all_tool_messages.append(ToolMessage(
                content=(
                    f"Source curation complete. Ranked {len(curated_sources)} "
                    f"of {len(collected_urls)} collected unique URLs."
                ),
                name="SourceCurate",
                tool_call_id=tc["id"],
            ))

    # --- ConductResearch calls (parallel subgraph invocation) ---
    # Follows Anthropic's Orchestrator-Workers pattern and Claude Code's
    # sub-agent effort levels (quick / normal / thorough / exhaustive).
    # Each call spawns an independent Researcher subgraph with configurable
    # depth×breadth derived from the research_effort parameter.
    conduct_calls = [tc for tc in tool_calls if tc["name"] == "ConductResearch"]
    if conduct_calls:
        requested_breadth = _safe_int(state.get("estimated_breadth"), default=2)
        max_concurrent = max(
            1,
            min(
                research_config.max_concurrent_research_units,
                max(requested_breadth, 1),
            ),
        )
        allowed_calls = conduct_calls[:max_concurrent]
        overflow_calls = conduct_calls[max_concurrent:]

        todo_ids_by_call: list[str | None] = []
        for tc in allowed_calls:
            topic = _conduct_topic(tc)
            current_todos, todo_id = ensure_todo_for_topic(
                current_todos,
                topic,
                source="supervisor",
            )
            if todo_id:
                current_todos = mark_todo_running(current_todos, todo_id)
            todo_ids_by_call.append(todo_id)
        await emit_todo_updates(thread_id, current_todos, previous_todos)
        previous_todos = list(current_todos)

        # Emit research tree update — all tasks starting
        try:
            from agent.core.events import get_emitter

            emitter = await get_emitter(thread_id)
            research_topic = state.get("research_brief", "")[:200]
            tree_root = {
                "id": "root",
                "name": research_topic or "Research",
                "status": "running",
                "children": [
                    _research_tree_child(i, tc, state, research_config, status="running")
                    for i, tc in enumerate(allowed_calls)
                ],
            }
            await emitter.emit_research_tree_update(tree_root)
        except Exception:
            pass

        # Execute researcher subgraphs in parallel (open_deep_research pattern)
        research_inputs_and_configs = []
        for tc in allowed_calls:
            budget = _research_budget_for_call(tc, state, research_config)
            task_config = _isolated_researcher_config(config)
            task_config.setdefault("configurable", {})
            task_config["configurable"].update(
                {
                    "max_react_tool_calls": budget["max_tool_calls"],
                    "research_depth": budget["depth"],
                    "research_breadth": budget["breadth"],
                    "research_effort": budget["research_effort"],
                    "thoroughness": budget["thoroughness"],
                }
            )
            research_inputs_and_configs.append(
                (
                    {
                        "researcher_messages": [
                            HumanMessage(
                                content=(
                                    f"Research topic: {_conduct_topic(tc)}\n"
                                    f"Context: {tc['args'].get('context', '')}"
                                )
                            )
                        ],
                        "research_topic": _conduct_topic(tc),
                        "research_effort": budget["research_effort"],
                        "thoroughness": budget["thoroughness"],
                        "research_depth": budget["depth"],
                        "research_breadth": budget["breadth"],
                        "tool_call_iterations": 0,
                        "evidence_items": [],
                    },
                    task_config,
                )
            )
        research_tasks = [
            _get_researcher_subgraph().ainvoke(research_input, task_config)
            for research_input, task_config in research_inputs_and_configs
        ]

        try:
            tool_results = await asyncio.gather(*research_tasks)
            _merge_researcher_viewed_images(
                config,
                [
                    task_config
                    for _research_input, task_config in research_inputs_and_configs
                ],
            )

            for obs, tc, todo_id in zip(tool_results, allowed_calls, todo_ids_by_call):
                compressed = obs.get(
                    "compressed_research",
                    "Error: Research synthesis failed."
                )
                if str(compressed).lstrip().lower().startswith("error"):
                    current_todos = mark_todo_blocked(
                        current_todos,
                        todo_id,
                        result_preview=compressed,
                    )
                else:
                    current_todos = mark_todo_completed(
                        current_todos,
                        todo_id,
                        result_preview=compressed,
                    )
                all_tool_messages.append(ToolMessage(
                    content=compressed,
                    name="ConductResearch",
                    tool_call_id=tc["id"],
                ))

            # Emit research tree update — all tasks completed
            try:
                research_topic = state.get("research_brief", "")[:200]
                tree_root = {
                    "id": "root",
                    "name": research_topic or "Research",
                    "status": "completed",
                    "children": [
                        {
                            **_research_tree_child(
                                i,
                                tc,
                                state,
                                research_config,
                                status="completed",
                            ),
                            "status": "completed",
                            "result_preview": obs.get("compressed_research", "")[:200],
                        }
                        for i, (tc, obs) in enumerate(zip(allowed_calls, tool_results))
                    ],
                }
                await emitter.emit_research_tree_update(tree_root)
            except Exception:
                pass

            # Aggregate raw notes from all parallel researchers
            raw_notes_concat = "\n".join([
                obs.get("raw_notes", [None])[0] if obs.get("raw_notes") else ""
                for obs in tool_results
            ])
            if raw_notes_concat.strip():
                update_payload["raw_notes"] = [raw_notes_concat]
            evidence_items = [
                item
                for obs in tool_results
                for item in (obs.get("evidence_items") or [])
                if isinstance(item, dict)
            ]
            if evidence_items:
                update_payload["evidence_items"] = evidence_items

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
            for todo_id in todo_ids_by_call:
                current_todos = mark_todo_blocked(
                    current_todos,
                    todo_id,
                    result_preview=str(e),
                )
            all_tool_messages.append(ToolMessage(
                content=f"Error during research execution: {e!s}",
                name="ConductResearch",
                tool_call_id=conduct_calls[0]["id"] if conduct_calls else "unknown",
            ))

    await emit_todo_updates(thread_id, current_todos, previous_todos)
    update_payload["research_todos"] = {"type": "override", "value": current_todos}
    update_payload["todo_summary"] = summarize_todos(current_todos)

    # === Return to supervisor loop (with context budget enforcement) ===
    update_payload["supervisor_messages"] = enforce_context_budget(
        supervisor_messages + all_tool_messages,
        max_messages=_SUPERVISOR_MAX_MESSAGES,
        max_chars_per_tool_result=research_config.compression_small_threshold,
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


def _research_completion_guard(
    state: SupervisorState,
    supervisor_messages: list,
) -> dict[str, object]:
    """Deterministically decide whether ResearchComplete may end the loop.

    The LLM still chooses when it thinks research is complete, but this guard
    prevents the common early-exit failure mode where a completion signal arrives
    before the current checklist has been covered or any current-run research
    artifact exists.
    """
    todos = list(state.get("research_todos", []) or [])
    summary = summarize_todos(todos)
    reasons: list[str] = []
    gaps: list[str] = []

    pending_or_running = int(summary.get("pending", 0)) + int(summary.get("running", 0))
    if pending_or_running:
        open_titles = [
            title for title in (summary.get("open_titles", []) or [])
            if str(title).strip()
        ]
        reasons.append(
            f"{pending_or_running} research task(s) are still pending or running"
        )
        gaps.extend(open_titles or ["Complete the remaining research tasks"])

    message_notes = _extract_notes_from_messages(supervisor_messages)
    state_notes = [
        str(note).strip()
        for note in (state.get("notes", []) or [])
        if str(note).strip()
    ]
    raw_notes = [
        str(note).strip()
        for note in (state.get("raw_notes", []) or [])
        if str(note).strip()
    ]
    evidence_items = [
        item for item in (state.get("evidence_items", []) or [])
        if isinstance(item, dict)
    ]
    has_research_artifact = bool(message_notes or state_notes or raw_notes or evidence_items)
    if not has_research_artifact and str(state.get("complexity") or "standard") != "simple":
        reasons.append("no current-run notes or evidence have been collected")
        gaps.append("Collect source-backed evidence before completion")

    if summary.get("total") and not int(summary.get("completed", 0)) and not has_research_artifact:
        reasons.append("the research checklist has not produced any completed work")

    deduped_gaps = list(dict.fromkeys(gaps))
    return {
        "allowed": not reasons,
        "reasons": reasons,
        "gaps": deduped_gaps[:5],
        "todo_summary": summary,
        "evidence_count": len(evidence_items),
        "note_count": len(message_notes) + len(state_notes) + len(raw_notes),
    }


def _completion_guard_messages(
    tool_calls: list[dict],
    guard: dict[str, object],
) -> list[ToolMessage]:
    """Return protocol-complete ToolMessages when completion is blocked."""
    reasons = guard.get("reasons", [])
    gaps = guard.get("gaps", [])
    reason_text = "; ".join(str(reason) for reason in reasons) or "not ready"
    gap_text = "; ".join(str(gap) for gap in gaps) or "continue the next best research step"
    messages: list[ToolMessage] = []
    for tc in tool_calls or []:
        name = str(tc.get("name") or "tool")
        if name == "ResearchComplete":
            content = (
                "ResearchComplete blocked by deterministic completion guard.\n"
                f"Reasons: {reason_text}\n"
                f"Next gaps: {gap_text}\n"
                "Continue research, resolve the open checklist items, then call "
                "ResearchComplete again."
            )
        elif name == "ThinkTool":
            content = (
                "Reflection recorded, but completion is not yet allowed.\n"
                f"Reasons: {reason_text}"
            )
        else:
            content = (
                f"{name} was not executed because the same supervisor turn also "
                "requested ResearchComplete, and completion was blocked. "
                "Call the needed research or curation tool again in the next turn."
            )
        messages.append(ToolMessage(
            content=content,
            name=name,
            tool_call_id=str(tc.get("id") or name),
        ))
    return messages


def _collect_source_urls(state: SupervisorState) -> list[dict]:
    """Collect source URLs from research notes for downstream curation.

    SourceCurate uses these normalized candidates for immediate quality ranking.
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


def _conduct_topic(tool_call: dict, default: str = "") -> str:
    args = tool_call.get("args", {}) if isinstance(tool_call, dict) else {}
    if not isinstance(args, dict):
        return default
    return str(args.get("topic") or args.get("research_topic") or default)


def _research_tree_child(
    index: int,
    tool_call: dict,
    state: SupervisorState,
    research_config: ResearchConfiguration,
    *,
    status: str,
) -> dict[str, object]:
    budget = _research_budget_for_call(tool_call, state, research_config)
    return {
        "id": f"task_{index}",
        "name": _conduct_topic(tool_call, f"Task {index + 1}"),
        "status": status,
        "research_effort": budget["research_effort"],
        "thoroughness": budget["thoroughness"],
        "budget": budget,
    }


def _safe_int(value: object, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


_EFFORT_ALIASES = {
    "quick": "quick",
    "medium": "normal",
    "normal": "normal",
    "deep": "thorough",
    "thorough": "thorough",
    "very_thorough": "exhaustive",
    "very-thorough": "exhaustive",
    "exhaustive": "exhaustive",
}
_EFFORT_ORDER = {
    "quick": 0,
    "normal": 1,
    "thorough": 2,
    "exhaustive": 3,
}


def _effort_to_legacy(effort: str) -> str:
    return {
        "quick": "quick",
        "normal": "medium",
        "thorough": "deep",
        "exhaustive": "very_thorough",
    }.get(effort, "medium")


def _max_effort_for_state(state: SupervisorState) -> str:
    complexity = str(state.get("complexity") or "standard").lower()
    depth = _safe_int(state.get("estimated_depth"), default=1)
    breadth = _safe_int(state.get("estimated_breadth"), default=2)
    if complexity == "simple":
        return "quick"
    if complexity == "standard":
        return "thorough" if depth >= 2 or breadth >= 4 else "normal"
    if complexity == "deep":
        return "exhaustive" if depth >= 3 or breadth >= 5 else "thorough"
    return "normal"


def _cap_effort(effort: str, state: SupervisorState) -> str:
    max_effort = _max_effort_for_state(state)
    if _EFFORT_ORDER.get(effort, 1) > _EFFORT_ORDER.get(max_effort, 1):
        return max_effort
    return effort


def _conduct_effort(tool_call: dict, state: SupervisorState) -> str:
    args = tool_call.get("args", {}) if isinstance(tool_call, dict) else {}
    raw = ""
    if isinstance(args, dict):
        raw = args.get("research_effort") or args.get("effort") or args.get("thoroughness") or ""
    value = str(raw or "").strip().lower()
    if value in _EFFORT_ALIASES:
        return _cap_effort(_EFFORT_ALIASES[value], state)
    if str(state.get("complexity") or "").lower() == "deep":
        depth = _safe_int(state.get("estimated_depth"), default=2)
        breadth = _safe_int(state.get("estimated_breadth"), default=4)
        if depth >= 3 or breadth >= 5:
            return "exhaustive"
        return "thorough"
    if str(state.get("complexity") or "").lower() == "simple":
        return "quick"
    return "normal"


def _conduct_thoroughness(tool_call: dict, state: SupervisorState) -> str:
    """Deprecated compatibility wrapper returning legacy labels."""
    return _effort_to_legacy(_conduct_effort(tool_call, state))


def _research_budget_for_call(
    tool_call: dict,
    state: SupervisorState,
    research_config: ResearchConfiguration,
) -> dict[str, int | str]:
    research_effort = _conduct_effort(tool_call, state)
    estimated_depth = max(1, _safe_int(state.get("estimated_depth"), default=1))
    estimated_breadth = max(1, _safe_int(state.get("estimated_breadth"), default=2))
    effort_map = {
        "quick": (1, 2, 4),
        "normal": (1, 4, 8),
        "thorough": (max(2, estimated_depth), max(4, estimated_breadth), 12),
        "exhaustive": (max(3, estimated_depth), max(6, estimated_breadth), 16),
    }
    depth, breadth, max_tool_calls = effort_map.get(research_effort, effort_map["normal"])
    return {
        "research_effort": research_effort,
        "thoroughness": _effort_to_legacy(research_effort),
        "depth": min(depth, 4),
        "breadth": min(breadth, research_config.max_concurrent_research_units),
        "max_tool_calls": min(max_tool_calls, max(research_config.max_react_tool_calls, max_tool_calls)),
    }


def _isolated_researcher_config(config: RunnableConfig) -> RunnableConfig:
    """Create a per-researcher config so parallel workers cannot mutate each other."""
    task_config = copy(config)
    configurable = dict((config.get("configurable") or {}) if isinstance(config, dict) else {})
    configurable["viewed_images"] = {}
    task_config["configurable"] = configurable
    return task_config


def _merge_researcher_viewed_images(
    parent_config: RunnableConfig,
    task_configs: list[RunnableConfig],
) -> None:
    """Merge visual artifacts after parallel workers finish, avoiding live context bleed."""
    if not isinstance(parent_config, dict):
        return
    merged: dict = {}
    for task_config in task_configs:
        configurable = task_config.get("configurable") if isinstance(task_config, dict) else {}
        viewed_images = configurable.get("viewed_images") if isinstance(configurable, dict) else {}
        if isinstance(viewed_images, dict):
            merged.update(viewed_images)
    if not merged:
        return
    merge_viewed_images(parent_config, merged)
    parent_configurable = dict(parent_config.get("configurable") or {})
    existing = parent_configurable.get("viewed_images")
    if isinstance(existing, dict):
        existing.update(merged)
    else:
        existing = merged
    parent_configurable["viewed_images"] = existing
    parent_config["configurable"] = parent_configurable


_SUPERVISOR_MAX_MESSAGES = 40
"""Drop oldest messages beyond this count to keep the supervisor context lean."""
