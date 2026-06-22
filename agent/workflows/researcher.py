"""Researcher Subgraph: Individual research agent with mixed compression.

Each researcher is spawned by the supervisor to investigate a specific topic.
It operates in a tool-calling loop: search → reflect → search → reflect → compress.

Key integrations from the unified design:
- open_deep_research: researcher → researcher_tools → compress_research subgraph pattern
- gpt-researcher: mixed compression (raw pass / embedding filter / LLM clean)
- deer-flow: error handling for tool calls

The researcher subgraph is compiled once and invoked N times in parallel by the supervisor.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime
from typing import Any, Literal

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    filter_messages,
)
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from agent.core.configuration import ResearchConfiguration
from agent.core.model_routing import build_model_config, configurable_model
from agent.core.prompts import (
    COMPRESSION_SIMPLE_HUMAN_MESSAGE,
    resolve_prompt,
)
from agent.core.state import (
    EvidenceItem,
    ResearcherOutputState,
    ResearcherState,
    ResearchComplete,
    ThinkTool,
)
from agent.workflows.evidence_ledger import normalize_evidence_item
from agent.workflows.source_cache import cache_source_text
from agent.runtime.context import clear_viewed_images, get_viewed_images, merge_viewed_images

logger = logging.getLogger(__name__)


# =============================================================================
# Researcher Node
# =============================================================================

async def researcher(
    state: ResearcherState, config: RunnableConfig
) -> Command[Literal["researcher_tools"]]:
    """Main researcher logic: binds tools and generates next action.

    Uses available search tools + think_tool in a ReAct loop.
    Pattern from open_deep_research: researcher node.
    """
    research_config = ResearchConfiguration.from_runnable_config(config)
    researcher_messages = state.get("researcher_messages", [])

    # Get available tools
    tools = await _get_researcher_tools(config, research_config)
    deferred_names: frozenset[str] = frozenset()
    try:
        from common.config import settings
        from tools.core.deferred_tools import assemble_deferred_tools

        deferred_enabled = bool(
            (config.get("configurable") or {}).get(
                "mcp_deferred_tools_enabled",
                getattr(settings, "mcp_deferred_tools_enabled", False),
            )
        )
        deferred_setup = assemble_deferred_tools(
            tools,
            enabled=deferred_enabled,
            config=config,
        )
        tools = deferred_setup.final_tools
        deferred_names = deferred_setup.deferred_names
    except Exception as e:
        logger.debug("[Researcher] Deferred MCP setup skipped: %s", e)
    if not tools:
        raise ValueError(
            "No research tools available. Please configure at least one search API "
            "(Tavily, etc.) or MCP server."
        )

    # Configure model using task-type routing (open_deep_research 4-role pattern).
    # The researcher's main loop is a synthesis task — it combines search results
    # and decides next steps.  Content summarisation of individual pages is handled
    # inside the search/read tools with their own model selection.
    model_name = research_config.get_model_for_task(
        "result_synthesis", complexity="standard"
    )

    model_config = build_model_config(
        model=model_name,
        max_tokens=research_config.research_model_max_tokens,
        tags=["langsmith:nostream"],
    )

    # Build system prompt with MCP context and vision tool guidance
    mcp_prompt = research_config.mcp_prompt or ""

    # Build vision tools description
    vision_tools_parts = []
    vision_guidance_parts = []
    tool_counter = 3  # After search tools and think_tool

    if research_config.supports_vision:
        tool_counter += 1
        vision_tools_parts.append(
            f"{tool_counter}. **view_image**: View local image files "
            f"(jpg/png/webp/gif). Use when you find a relevant image file "
            f"that could contain charts, diagrams, or visual data."
        )

    if research_config.vision_enrich_data:
        tool_counter += 1
        vision_tools_parts.append(
            f"{tool_counter}. **extract_web_images**: Extract and analyze images "
            f"from web pages. Use when research topic involves visual data "
            f"(financial reports, charts, architecture diagrams, data "
            f"dashboards, infographics). Provide the URL of a page containing "
            f"relevant images."
        )
        vision_guidance_parts.append(
            "6. **When to use extract_web_images**: If your research topic "
            "involves financial data, statistical charts, diagrams, "
            "infographics, or any domain where visual data is key, "
            "use extract_web_images on relevant search result URLs to "
            "enrich your findings with visual data."
        )

    vision_tools_text = "\n".join(vision_tools_parts)
    vision_guidance_text = "\n".join(vision_guidance_parts)

    system_prompt = resolve_prompt("researcher",
        mcp_prompt=f"\n3. **MCP Tools**: Additional research tools\n{mcp_prompt}" if mcp_prompt else "",
        vision_tools=vision_tools_text,
        vision_guidance=vision_guidance_text,
        date=datetime.now().strftime("%Y-%m-%d"),
    )
    source_policy = _researcher_source_policy(config)
    source_guidance = _format_source_policy_guidance(source_policy)
    if source_guidance:
        system_prompt += "\n\n" + source_guidance
    if deferred_names:
        try:
            from tools.core.deferred_tools import deferred_tools_prompt_section

            system_prompt += "\n\n" + deferred_tools_prompt_section(deferred_names)
        except Exception:
            pass

    # === Skill Progressive Loading (deer-flow pattern) ===
    # Make active skills discoverable to the researcher so it can
    # read_file on a SKILL.md and follow its workflow on demand.
    configurable_skills = config.get("configurable") or {}
    researcher_skill_ids: list[str] = (
        configurable_skills.get("skill_ids")
        or configurable_skills.get("deepsearch_skill_ids")
        or []
    )
    if researcher_skill_ids:
        try:
            from agent.skills.prompt import get_skills_prompt_section
            _skills_section = get_skills_prompt_section(
                available_skills=set(researcher_skill_ids),
            )
            if _skills_section:
                system_prompt += "\n\n" + _skills_section
                logger.debug(
                    "[Researcher] Injected skills progressive loading section "
                    "(%d skills)", len(researcher_skill_ids)
                )
        except Exception as e:
            logger.debug(
                "[Researcher] Skills progressive loading skipped: %s", e
            )

    research_model = (
        configurable_model
        .bind_tools(tools)
        .with_retry(stop_after_attempt=research_config.max_structured_output_retries)
        .with_config(model_config)
    )

    messages = [SystemMessage(content=system_prompt)] + researcher_messages

    # === Image Injection (multimodal — deer-flow pattern) ===
    viewed_images = get_viewed_images(config)
    if viewed_images:
        try:
            from agent.workflows.multimodal import build_image_injection_message
            injection_msg = build_image_injection_message(viewed_images)
            if injection_msg:
                messages.append(injection_msg)
                logger.info(
                    f"[Researcher] Injected {len(viewed_images)} image(s) "
                    f"into LLM context"
                )
                # Clear from config so images are not re-injected on subsequent calls
                clear_viewed_images(config)
        except ImportError:
            pass

    # === Loop Detection ===
    from agent.runtime.middleware.shared import check_loop
    is_looping, _hint = check_loop(researcher_messages)
    if is_looping:
        logger.warning("[Researcher] Loop detected, forcing compression")
        return Command(
            goto="compress_research",
            update={"researcher_messages": researcher_messages},
        )

    response = await research_model.ainvoke(messages)

    # === Token Usage Tracking ===
    from agent.core.middleware import get_token_tracker
    tracker = get_token_tracker(config)
    usage = getattr(response, "usage_metadata", None) or {}
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    if input_tokens or output_tokens:
        tracker.record("research", input_tokens, output_tokens)

    return Command(
        goto="researcher_tools",
        update={
            "researcher_messages": [response],
            "tool_call_iterations": state.get("tool_call_iterations", 0) + 1,
        },
    )


# =============================================================================
# Researcher Tools Node
# =============================================================================

async def researcher_tools(
    state: ResearcherState, config: RunnableConfig
) -> Command[Literal["researcher", "compress_research"]]:
    """Execute tools called by the researcher.

    Handles:
    - think_tool: Record structured reflection, continue loop
    - Search tools: Execute searches, return results
    - ResearchComplete: Signal completion, proceed to compression

    Pattern from open_deep_research: researcher_tools node.
    """
    research_config = ResearchConfiguration.from_runnable_config(config)
    researcher_messages = state.get("researcher_messages", [])
    most_recent_message = researcher_messages[-1]

    # Exit if no tool calls
    if not most_recent_message.tool_calls:
        logger.info("[ResearcherTools] No tool calls, proceeding to compression")
        return Command(goto="compress_research")

    # Check for ResearchComplete
    research_complete_called = any(
        tc["name"] == "ResearchComplete"
        for tc in most_recent_message.tool_calls
    )
    if research_complete_called:
        logger.info("[ResearcherTools] ResearchComplete called, compressing")
        return Command(goto="compress_research")

    # Execute all tool calls in parallel
    tools = await _get_researcher_tools(config, research_config)
    tools_by_name = _build_tools_by_name(tools)

    tool_calls = most_recent_message.tool_calls

    # Researcher ThinkTool is a structured reflection checkpoint, not an external
    # executable tool.  Preserve it as a ToolMessage so the next ReAct step can
    # use the reflection without tripping generic tool execution.
    think_calls = [tc for tc in tool_calls if tc.get("name") == "ThinkTool"]
    think_tool_messages: list[ToolMessage] = []
    for tc in think_calls:
        args = tc.get("args") or {}
        gaps = args.get("gaps_identified", []) or []
        if not isinstance(gaps, list):
            gaps = [str(gaps)]
        think_tool_messages.append(ToolMessage(
            content=(
                "Reflection recorded:\n"
                f"- Gaps identified: {', '.join(str(g) for g in gaps) if gaps else 'none'}\n"
                f"- Confidence: {args.get('confidence_level', 'medium')}\n"
                f"- Next strategy: {args.get('next_strategy', 'search_more')}\n"
                f"- Details: {str(args.get('reflection', ''))[:800]}"
            ),
            name="ThinkTool",
            tool_call_id=tc["id"],
        ))

    # === Handle view_image + extract_web_images calls: capture base64 for injection ===
    image_tool_names = {"view_image", "extract_web_images"}
    image_tool_calls = [tc for tc in tool_calls if tc.get("name") in image_tool_names]
    non_image_calls = [
        tc for tc in tool_calls
        if tc.get("name") not in image_tool_names and tc.get("name") != "ThinkTool"
    ]

    # Execute image tool calls directly to capture base64 data
    captured_images: dict[str, dict[str, str]] = {}
    image_tool_messages: list[ToolMessage] = []
    for tc in image_tool_calls:
        tc_name = tc.get("name", "")

        if tc_name == "view_image":
            try:
                from agent.tools.view_image import view_image as view_image_fn
                tc_result = await view_image_fn(tc["args"].get("image_path", ""))
                if tc_result["success"]:
                    key = tc_result["image_path"]
                    captured_images[key] = {
                        "base64": tc_result["base64"],
                        "mime_type": tc_result["mime_type"],
                    }
                image_tool_messages.append(ToolMessage(
                    content=str(tc_result.get("error") or (
                        f"Successfully loaded image: {tc_result['image_path']}\n"
                        f"Format: {tc_result['mime_type']}\n"
                        f"Base64 length: {len(tc_result['base64'])} chars"
                    )),
                    name="view_image",
                    tool_call_id=tc["id"],
                ))
            except Exception as e:
                logger.error(f"[ResearcherTools] view_image failed: {e}")
                image_tool_messages.append(ToolMessage(
                    content=f"Error viewing image: {str(e)}",
                    name="view_image",
                    tool_call_id=tc["id"],
                ))

        elif tc_name == "extract_web_images":
            try:
                from agent.tools.extract_web_images import extract_web_images as extract_fn
                tc_result = await extract_fn(
                    tc["args"].get("url", ""),
                    max_images=tc["args"].get("max_images", 3),
                )
                if tc_result["success"]:
                    for img in tc_result["images"]:
                        key = img["url"]
                        captured_images[key] = {
                            "base64": img["base64"],
                            "mime_type": img["mime_type"],
                        }
                image_tool_messages.append(ToolMessage(
                    content=str(tc_result.get("error") or (
                        f"Successfully extracted {tc_result['image_count']} image(s)"
                        f" from: {tc_result['url']}"
                    )),
                    name="extract_web_images",
                    tool_call_id=tc["id"],
                ))
            except Exception as e:
                logger.error(f"[ResearcherTools] extract_web_images failed: {e}")
                image_tool_messages.append(ToolMessage(
                    content=f"Error extracting web images: {str(e)}",
                    name="extract_web_images",
                    tool_call_id=tc["id"],
                ))

    # Store captured images in config for injection in next researcher() call
    if captured_images:
        merge_viewed_images(config, captured_images)
        logger.info(
            f"[ResearcherTools] Stored {len(captured_images)} image(s) "
            f"in config for next LLM call"
        )

    # Execute non-image tool calls in parallel
    tasks = [
        _execute_tool_safely(tools_by_name.get(tc["name"]), tc["args"], config)
        for tc in non_image_calls
    ]
    observations = await asyncio.gather(*tasks)

    # Create tool messages
    tool_outputs = list(think_tool_messages) + list(image_tool_messages)
    evidence_items: list[dict[str, Any]] = []
    for tc, obs in zip(non_image_calls, observations):
        obs_text, cache_info = _maybe_cache_observation(
            observation=str(obs),
            tool_name=tc.get("name", ""),
            args=tc.get("args", {}),
            config=config,
        )
        tool_outputs.append(ToolMessage(
            content=obs_text,
            name=tc["name"],
            tool_call_id=tc["id"],
        ))
        extracted = _extract_evidence_from_observation(
            tool_name=tc.get("name", ""),
            args=tc.get("args", {}),
            observation=obs_text,
            research_topic=state.get("research_topic", ""),
        )
        if cache_info:
            for item in extracted:
                metadata = dict(item.get("metadata") if isinstance(item.get("metadata"), dict) else {})
                metadata.update(cache_info)
                item["metadata"] = metadata
                item["cached_path"] = cache_info.get("cached_path")
        evidence_items.extend(
            extracted
        )

    # Check iteration limits
    exceeded_iterations = (
        state.get("tool_call_iterations", 0) >= research_config.max_react_tool_calls
    )
    if exceeded_iterations:
        logger.info("[ResearcherTools] Max iterations exceeded, compressing")
        return Command(
            goto="compress_research",
            update={
                "researcher_messages": tool_outputs,
                "evidence_items": evidence_items,
            },
        )

    # Continue research loop
    return Command(
        goto="researcher",
        update={
            "researcher_messages": tool_outputs,
            "evidence_items": evidence_items,
        },
    )


# =============================================================================
# Compress Research Node (Mixed Compression Strategy)
# =============================================================================

async def compress_research(
    state: ResearcherState, config: RunnableConfig
) -> dict:
    """Compress research findings into a concise, structured summary.

    Implements the unified design's mixed compression strategy:
    1. Small content (< 8000 chars) → raw pass-through (zero cost)
    2. Medium content (8000-50000 chars) → embedding similarity filter (low cost)
    3. Large content (> 50000 chars) → LLM semantic compression (high quality)

    Pattern from open_deep_research: compress_research node.
    Enhanced with gpt-researcher's embedding-based compression.
    """
    research_config = ResearchConfiguration.from_runnable_config(config)
    researcher_messages = state.get("researcher_messages", [])

    # Aggregate all content from research messages
    raw_content = _aggregate_research_content(researcher_messages)
    total_chars = len(raw_content)

    logger.info(f"[Compress] Raw content size: {total_chars} chars")

    # === Strategy 1: Small content → raw pass-through ===
    if total_chars < research_config.compression_small_threshold:
        logger.info("[Compress] Small content - raw pass-through")
        raw_notes = "\n".join([
            str(m.content) for m in filter_messages(
                researcher_messages, include_types=["tool", "ai"]
            )
        ])
        return {
            "compressed_research": raw_content,
            "raw_notes": [raw_notes],
            "evidence_items": state.get("evidence_items", []),
        }

    # === Strategy 2: Medium content → embedding-based filtering ===
    elif total_chars < research_config.compression_medium_threshold:
        logger.info("[Compress] Medium content - attempting embedding filter")
        try:
            compressed = await _embedding_compress(
                raw_content,
                state.get("research_topic", ""),
                research_config,
            )
            if compressed:
                raw_notes = "\n".join([
                    str(m.content) for m in filter_messages(
                        researcher_messages, include_types=["tool", "ai"]
                    )
                ])
                return {
                    "compressed_research": compressed,
                    "raw_notes": [raw_notes],
                    "evidence_items": state.get("evidence_items", []),
                }
        except Exception as e:
            logger.warning(f"[Compress] Embedding compression failed: {e}, falling back to LLM")

    # === Strategy 3: Large content → LLM semantic compression ===
    logger.info("[Compress] Using LLM semantic compression")
    return await _llm_compress(researcher_messages, state, research_config, config)


# =============================================================================
# Researcher Subgraph Construction
# =============================================================================

def build_researcher_subgraph() -> StateGraph:
    """Build the researcher subgraph for individual research tasks.

    Returns a compiled subgraph that can be invoked with researcher_messages
    and research_topic as input.

    Graph: START → researcher ⇄ researcher_tools → compress_research → END
    """
    builder = StateGraph(
        ResearcherState,
        output=ResearcherOutputState,
        config_schema=ResearchConfiguration,
    )

    builder.add_node("researcher", researcher)
    builder.add_node("researcher_tools", researcher_tools)
    builder.add_node("compress_research", compress_research)

    builder.add_edge(START, "researcher")
    builder.add_edge("compress_research", END)

    return builder.compile()


# =============================================================================
# Helpers
# =============================================================================

async def _get_researcher_tools(
    config: RunnableConfig,
    research_config: ResearchConfiguration,
) -> list:
    """Get available tools for the researcher.

    Integrates:
    - Tavily search (from Weaver's existing tool ecosystem)
    - fallback_search (DuckDuckGo/web search)
    - Academic providers: ArXiv, PubMed, Semantic Scholar
    - Sandbox tools: shell, files, code execution
    - think_tool (enhanced, from unified design)
    - ResearchComplete (from open_deep_research)
    - MCP tools (from Weaver's MCP infrastructure)
    """
    tools = [ThinkTool, ResearchComplete]
    source_policy = _researcher_source_policy(config)
    include_web = source_policy["include_web"]
    include_academic = source_policy["include_academic"]
    include_mcp = source_policy["include_mcp"]

    # === Skill Guide Reader (Progressive Loading Layer 3) ===
    # Allows the researcher to load full SKILL.md content and supporting
    # resources (scripts, templates, references) on demand, following the
    # SKILL.md open standard progressive disclosure pattern.
    try:
        from langchain_core.tools import tool as lc_tool
        from pathlib import Path as _Path
        import os as _os

        _skills_root = _os.path.abspath(
            _os.path.join(_os.path.dirname(__file__), "..", "..", "skills")
        )
        _skill_bases = {
            "public": _os.path.join(_skills_root, "public"),
            "custom": _os.path.join(_skills_root, "custom"),
        }

        @lc_tool
        def read_skill_guide(file_path: str) -> str:
            """Read a SKILL.md file or supporting resource from the skills directory.

            Args:
                file_path: Relative path within the skills directory
                           (e.g., 'deep-research/SKILL.md' or 'html-report/templates/report.css')

            Returns:
                The full content of the requested file.
            """
            _safe = _os.path.normpath(file_path).lstrip("/")
            if ".." in _safe.split(_os.sep):
                return "Error: path traversal not allowed"
            _cfg = config.get("configurable") or {}
            _active = (
                _cfg.get("skill_ids")
                or _cfg.get("deepsearch_skill_ids")
                or []
            )
            if isinstance(_active, str):
                _active = [p.strip() for p in _active.split(",") if p.strip()]
            _active_set = {str(p).strip() for p in _active if str(p).strip()}
            _parts = _safe.split("/", 2)
            if _parts[0] in {"public", "custom"} and len(_parts) >= 2:
                _category, _requested_root = _parts[0], _parts[1]
                _relative = _parts[2] if len(_parts) > 2 else "SKILL.md"
            else:
                _category, _requested_root = "public", _parts[0]
                _relative = _safe.split("/", 1)[1] if "/" in _safe else "SKILL.md"
            if not _active_set or _requested_root not in _active_set:
                return (
                    "Error: read_skill_guide can only read files under active "
                    f"skills for this run. Active skills: {sorted(_active_set)}"
                )
            _base = _skill_bases.get(_category, _skill_bases["public"])
            full = _os.path.join(_base, _requested_root, _relative)
            try:
                _resolved_base = _Path(_base).resolve()
                _resolved_full = _Path(full).resolve()
                _resolved_full.relative_to(_resolved_base)
            except Exception:
                return "Error: path traversal not allowed"
            if not _resolved_full.is_file():
                return f"Error: file not found at '{_safe}'"
            return _resolved_full.read_text(encoding="utf-8")

        tools.append(read_skill_guide)
        logger.debug("[Researcher] Loaded read_skill_guide tool for progressive loading")
    except Exception as e:
        logger.debug("[Researcher] read_skill_guide tool not available: %s", e)

    # === View Image Tool (Multimodal / Vision Support — deer-flow pattern) ===
    if research_config.supports_vision:
        try:
            from agent.tools.view_image import view_image_tool
            tools.append(view_image_tool)
            logger.debug("[Researcher] Loaded view_image tool (vision enabled)")
        except ImportError:
            logger.debug("[Researcher] View image tool not available")

    # === Web Image Extraction Tool (vision_enrich_data — LLM-decided) ===
    if research_config.vision_enrich_data:
        try:
            from agent.tools.extract_web_images import extract_web_images_tool
            tools.append(extract_web_images_tool)
            logger.debug("[Researcher] Loaded extract_web_images tool (vision_enrich enabled)")
        except ImportError:
            logger.debug("[Researcher] Extract web images tool not available")

    # Load search tools from Weaver's existing ecosystem, honoring source routing.
    if include_web:
        try:
            from tools import tavily_search, fallback_search
            tools.append(tavily_search)
            tools.append(fallback_search)
            logger.debug("[Researcher] Loaded Tavily + fallback search")
        except ImportError:
            logger.warning("[Researcher] Could not import search tools from Weaver")

    # === Academic Retrievers (ArXiv, PubMed, Semantic Scholar) ===
    if include_academic:
        try:
            from tools.search.academic import arxiv_search, pubmed_search, semantic_scholar_search
            tools.extend([arxiv_search, pubmed_search, semantic_scholar_search])
            logger.debug("[Researcher] Loaded academic search tools (ArXiv, PubMed, Semantic Scholar)")
        except ImportError:
            logger.debug("[Researcher] Academic search tools not available")

    # === Sandbox Tools (code execution, shell, files) ===
    try:
        from tools.crawl.deep_read_tool import deep_read
        from tools.sandbox.sandbox_shell_tool import (
            SandboxExecuteCommandTool,
            SandboxCheckOutputTool,
        )
        from tools.sandbox.sandbox_files_tool import (
            SandboxCreateFileTool,
            SandboxReadFileTool,
        )
        from tools.code.code_executor import create_visualization, execute_python_code

        sandbox_shell = SandboxExecuteCommandTool()
        sandbox_check = SandboxCheckOutputTool()
        sandbox_files_read = SandboxReadFileTool()
        sandbox_files_create = SandboxCreateFileTool()

        tools.extend([
            deep_read,
            sandbox_shell, sandbox_check,
            sandbox_files_read, sandbox_files_create,
            execute_python_code, create_visualization,
        ])
        logger.debug("[Researcher] Loaded sandbox tools (shell, files, code)")
    except ImportError as e:
        logger.debug(f"[Researcher] Sandbox tools not available: {e}")
    except Exception as e:
        logger.warning(f"[Researcher] Failed to load sandbox tools: {e}")

    # Load MCP tools if enabled by config or selected source routing.
    if research_config.mcp_enabled or include_mcp:
        try:
            from tools.mcp import init_mcp_tools as _init_mcp_tools
            configurable = config.get("configurable") or {}
            mcp_tools = await _init_mcp_tools(
                enabled=True,
                policy_config=configurable,
            )
            if mcp_tools:
                tools.extend(mcp_tools)
                logger.debug(f"[Researcher] Loaded {len(mcp_tools)} MCP tools")
        except Exception as e:
            logger.warning(f"[Researcher] Failed to load MCP tools: {e}")

    # === Skill Tool Whitelist ===
    # Filter tools based on active skills' allowed-tools declarations.
    # Currently a no-op (no built-in skills declare allowed-tools), but the
    # code path is integrated so custom skills with restrictions work immediately.
    configurable = config.get("configurable") or {}
    active_skill_ids: list[str] = (
        configurable.get("skill_ids")
        or configurable.get("deepsearch_skill_ids")
        or []
    )
    if active_skill_ids:
        try:
            from agent.skills.tool_policy import filter_tools_by_skill_allowed_tools
            from agent.skills.parser import parse_skill_file
            from agent.skills.types import SkillCategory
            import os as _os
            from pathlib import Path as _Path

            _loaded = []
            _skills_root = _os.path.abspath(
                _os.path.join(_os.path.dirname(__file__), "..", "..", "skills")
            )
            for _category, _enum in (("public", SkillCategory.PUBLIC), ("custom", SkillCategory.CUSTOM)):
                _skills_base = _os.path.join(_skills_root, _category)
                if not _os.path.isdir(_skills_base):
                    continue
                for _entry in sorted(_os.listdir(_skills_base)):
                    if _entry not in active_skill_ids:
                        continue
                    _sf = _os.path.join(_skills_base, _entry, "SKILL.md")
                    if not _os.path.isfile(_sf):
                        continue
                    try:
                        _sk = parse_skill_file(
                            _Path(_sf),
                            _enum,
                            _Path(_entry),
                        )
                        if _sk:
                            _loaded.append(_sk)
                    except Exception:
                        pass
            if _loaded:
                tools = filter_tools_by_skill_allowed_tools(tools, _loaded)
                logger.debug(
                    "[Researcher] Skill tool whitelist applied "
                    "(%d skills, %d tools remaining)", len(_loaded), len(tools)
                )
        except Exception as e:
            logger.debug("[Researcher] Skill tool whitelist skipped: %s", e)

    return _dedupe_tools(tools)


def _tool_name(tool: Any) -> str:
    name = getattr(tool, "name", None)
    if isinstance(name, str) and name:
        return name
    if isinstance(tool, type):
        return tool.__name__
    name = getattr(tool, "__name__", None)
    return str(name or "")


def _build_tools_by_name(tools: list) -> dict[str, Any]:
    return {name: tool for tool in tools if (name := _tool_name(tool))}


def _dedupe_tools(tools: list) -> list:
    deduped: dict[str, Any] = {}
    for tool in tools:
        name = _tool_name(tool)
        if name and name not in deduped:
            deduped[name] = tool
    return list(deduped.values())


def _researcher_source_policy(config: RunnableConfig) -> dict[str, Any]:
    cfg = config.get("configurable") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    try:
        from agent.workflows.source_routing import build_source_routing_policy
        routing = build_source_routing_policy(config={"configurable": cfg})
    except Exception:
        routing = cfg.get("source_routing") if isinstance(cfg.get("source_routing"), dict) else {}

    mode = str(routing.get("mode") or cfg.get("source_policy") or "").strip().lower()
    providers = routing.get("providers") if isinstance(routing.get("providers"), list) else []
    budget_policy = (
        routing.get("budget_policy")
        if isinstance(routing.get("budget_policy"), dict)
        else {}
    )
    allowed_providers = {"web", "academic", "mcp"}
    provider_set = {
        provider
        for provider in (
            str(item).strip().lower() for item in providers if str(item).strip()
        )
        if provider in allowed_providers
    }
    if not provider_set:
        if mode == "mcp_only":
            provider_set = {"mcp"}
        else:
            provider_set = {"web"}

    if mode not in {"web_only", "mcp_only"}:
        mode = "web_only"

    return {
        "mode": mode or "web_only",
        "providers": sorted(provider_set),
        "include_web": "web" in provider_set,
        "include_academic": "academic" in provider_set or "web" in provider_set,
        "include_mcp": "mcp" in provider_set,
        "budget_policy": budget_policy,
    }


def _format_source_policy_guidance(source_policy: dict[str, Any]) -> str:
    providers = ", ".join(source_policy.get("providers", []) or []) or "web"
    budget = source_policy.get("budget_policy") or {}
    budget_lines = []
    if isinstance(budget, dict):
        for key in ("web", "academic", "mcp", "max_sources", "min_sources"):
            if key in budget:
                budget_lines.append(f"- {key}: {budget[key]}")
    lines = [
        "<Source Policy>",
        f"- mode: {source_policy.get('mode', 'web_only')}",
        f"- providers: {providers}",
    ]
    if budget_lines:
        lines.append("- budgets:")
        lines.extend(f"  {line}" for line in budget_lines)
    lines.append(
        "- Use only the available provider tools implied by this policy; if a "
        "provider returns no results, state the gap and continue with the next "
        "allowed provider."
    )
    lines.append("</Source Policy>")
    return "\n".join(lines)


def _extract_evidence_from_observation(
    *,
    tool_name: str,
    args: dict[str, Any],
    observation: str,
    research_topic: str,
    max_items: int = 8,
) -> list[dict[str, Any]]:
    text = str(observation or "").strip()
    if not text or text.startswith("Error"):
        return []

    url_pattern = re.compile(r"https?://[^\s\])>\"']+")
    urls = []
    seen = set()
    for url in url_pattern.findall(text):
        cleaned = url.rstrip(".,;")
        if cleaned not in seen:
            seen.add(cleaned)
            urls.append(cleaned)
        if len(urls) >= max_items:
            break

    chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n|---+", text) if chunk.strip()]
    if not chunks:
        chunks = [text]

    evidence: list[dict[str, Any]] = []
    query = str(args.get("query") or args.get("search_query") or research_topic or "")
    for idx, chunk in enumerate(chunks[:max_items], 1):
        url = urls[idx - 1] if idx - 1 < len(urls) else (urls[0] if urls else "")
        content = re.sub(r"\s+", " ", chunk).strip()[:1600]
        if len(content) < 40:
            continue
        evidence_hash = hashlib.sha1(
            f"{tool_name}|{query}|{idx}|{content[:160]}".encode("utf-8")
        ).hexdigest()[:16]
        item = EvidenceItem(
            id=f"{tool_name or 'tool'}_{evidence_hash}",
            type="tool_observation",
            url=url,
            source=url,
            content=content,
            tool=tool_name,
            query=query,
            retrieved_at=datetime.now().isoformat(timespec="seconds"),
        )
        normalized = normalize_evidence_item(item.to_artifact())
        if normalized:
            evidence.append(normalized)
    return evidence


def _maybe_cache_observation(
    *,
    observation: str,
    tool_name: str,
    args: dict[str, Any],
    config: RunnableConfig,
) -> tuple[str, dict[str, Any] | None]:
    metadata = {
        "tool": tool_name,
        "query": args.get("query") or args.get("url") or args.get("cached_path") or "",
    }
    cached = cache_source_text(
        text=observation,
        config=config,
        source_hint=f"{tool_name}-{metadata['query']}",
        metadata=metadata,
    )
    if not cached:
        return observation, None
    hint = (
        "\n\n---\n"
        "[Full tool output was cached for focused follow-up reads. "
        f"Use deep_read(cached_path='{cached['cached_path']}', section_query='...') "
        "or start_line/end_line if more detail is needed.]\n"
        f"cached_path: {cached['cached_path']}\n"
        f"line_count: {cached['line_count']}\n"
        f"content_hash: {cached['content_hash']}"
    )
    return observation + hint, cached


def _aggregate_research_content(messages: list) -> str:
    """Aggregate all content from research messages into a single string."""
    parts = []
    for msg in messages:
        if hasattr(msg, "content") and msg.content:
            content = msg.content
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and "text" in item:
                        parts.append(item["text"])
    return "\n\n".join(parts)


async def _execute_tool_safely(tool, args: dict, config: RunnableConfig) -> str:
    """Execute a tool with error handling via middleware (deer-flow pattern).

    Uses ToolErrorHandler from middleware to guarantee error messages
    are returned instead of exceptions propagating to the graph.
    """
    if tool is None:
        return f"Error: Tool not found for args {list(args.keys())}"

    tool_name = getattr(tool, "name", "unknown")
    if tool_name == "tool_search":
        try:
            from tools.core.deferred_tools import promote_deferred_tools

            metadata = getattr(tool, "metadata", None)
            catalog = (
                metadata.get("deferred_catalog")
                if isinstance(metadata, dict)
                else None
            )
            if catalog is None:
                raise ValueError("tool_search is missing deferred catalog metadata")
            update = promote_deferred_tools(catalog, str(args.get("query") or ""))
            promoted = update.get("promoted_tools")
            if isinstance(promoted, dict):
                configurable = config.setdefault("configurable", {})
                existing = configurable.get("promoted_tools")
                if (
                    isinstance(existing, dict)
                    and existing.get("catalog_hash") == promoted.get("catalog_hash")
                ):
                    names = list(existing.get("names") or []) + list(promoted.get("names") or [])
                    configurable["promoted_tools"] = {
                        "catalog_hash": promoted.get("catalog_hash"),
                        "names": list(dict.fromkeys(str(name) for name in names)),
                    }
                else:
                    configurable["promoted_tools"] = {
                        "catalog_hash": promoted.get("catalog_hash"),
                        "names": list(dict.fromkeys(str(name) for name in (promoted.get("names") or []))),
                    }
                try:
                    from agent.runtime.context import ensure_runtime_context

                    ensure_runtime_context(config).promoted_tools = dict(
                        configurable["promoted_tools"]
                    )
                except Exception:
                    pass
            return str(update.get("content") or "")
        except Exception as e:
            logger.warning("[Researcher] tool_search failed: %s", e)
            return f"Tool 'tool_search' error: {e}. Try a different query."

    try:
        from agent.core.middleware import ToolErrorHandler

        tool_call = {"name": tool_name, "id": "researcher", "args": args}
        tools_by_name = {tool_name: tool}
        result = await ToolErrorHandler.execute_with_error_handling(
            tool_call, tools_by_name, config
        )
        return str(result.content)
    except ImportError:
        try:
            result = await tool.ainvoke(args, config)
            return str(result)
        except Exception as e:
            logger.error(f"[Researcher] Tool execution error: {e}")
            return f"Error executing tool: {str(e)}"


async def _embedding_compress(
    content: str, topic: str, research_config: ResearchConfiguration
) -> str | None:
    """Compress content using embedding similarity filtering.

    Pattern from gpt-researcher's ContextCompressor.
    Splits content into chunks, filters by similarity to research topic.
    """
    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        from langchain_core.documents import Document

        # Chunk the content
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000, chunk_overlap=100
        )
        chunks = splitter.split_text(content)
        docs = [Document(page_content=chunk) for chunk in chunks]

        if len(docs) <= 5:
            # Already small enough
            return content

        # Try to use embeddings for filtering
        try:
            from langchain_openai import OpenAIEmbeddings
            # Try modern langchain first, fall back to langchain_classic
            try:
                from langchain.retrievers import ContextualCompressionRetriever
                from langchain.retrievers.document_compressors import (
                    DocumentCompressorPipeline,
                    EmbeddingsFilter,
                )
            except ImportError:
                from langchain_classic.retrievers import ContextualCompressionRetriever
                from langchain_classic.retrievers.document_compressors import (
                    DocumentCompressorPipeline,
                    EmbeddingsFilter,
                )

            embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

            class SimpleRetriever:
                def __init__(self, docs):
                    self.docs = docs
                def invoke(self, query, **kwargs):
                    return self.docs
                async def ainvoke(self, query, **kwargs):
                    return self.docs

            relevance_filter = EmbeddingsFilter(
                embeddings=embeddings,
                similarity_threshold=research_config.similarity_threshold,
            )
            pipeline = DocumentCompressorPipeline(
                transformers=[relevance_filter]
            )
            retriever = ContextualCompressionRetriever(
                base_compressor=pipeline,
                base_retriever=SimpleRetriever(docs),
            )
            relevant_docs = retriever.invoke(topic)
            return "\n\n".join([d.page_content for d in relevant_docs])

        except ImportError:
            logger.warning("[Compress] Embedding libraries not available")
            return None

    except Exception as e:
        logger.warning(f"[Compress] Embedding compression error: {e}")
        return None


async def _llm_compress(
    researcher_messages: list,
    state: ResearcherState,
    research_config: ResearchConfiguration,
    config: RunnableConfig,
) -> dict:
    """Compress research findings using LLM semantic understanding.

    Pattern from open_deep_research: compress_research node.
    Uses the compression_model (smart_llm) for accurate understanding and rewriting.
    """
    compression_model_name = research_config.get_compression_model()

    model_config = build_model_config(
        model=compression_model_name,
        max_tokens=research_config.compression_model_max_tokens,
        tags=["langsmith:nostream"],
    )

    synthesizer = configurable_model.with_config(model_config)

    messages = list(researcher_messages)
    messages.append(HumanMessage(content=COMPRESSION_SIMPLE_HUMAN_MESSAGE))

    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            compression_prompt = resolve_prompt("compression",
                date=datetime.now().strftime("%Y-%m-%d")
            )
            response = await synthesizer.ainvoke(
                [SystemMessage(content=compression_prompt)] + messages
            )

            raw_notes = "\n".join([
                str(m.content) for m in filter_messages(
                    researcher_messages, include_types=["tool", "ai"]
                )
            ])

            return {
                "compressed_research": str(response.content),
                "raw_notes": [raw_notes],
                "evidence_items": state.get("evidence_items", []),
            }

        except Exception as e:
            logger.warning(f"[Compress] LLM attempt {attempt + 1} failed: {e}")
            if attempt < max_attempts - 1:
                # Remove older messages to reduce context size
                if len(messages) > 4:
                    messages = messages[:1] + messages[3:]
            continue

    # All attempts failed
    raw_notes = "\n".join([
        str(m.content) for m in filter_messages(
            researcher_messages, include_types=["tool", "ai"]
        )
    ])
    return {
        "compressed_research": "Error: Research compression failed after maximum retries.",
        "raw_notes": [raw_notes],
        "evidence_items": state.get("evidence_items", []),
    }
