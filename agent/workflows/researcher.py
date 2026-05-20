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
import logging
from datetime import datetime
from typing import Literal

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
from agent.core.model_routing import configurable_model
from agent.core.prompts import (
    COMPRESSION_SIMPLE_HUMAN_MESSAGE,
    COMPRESSION_SYSTEM_PROMPT,
    RESEARCHER_SYSTEM_PROMPT,
)
from agent.core.state import (
    ResearcherOutputState,
    ResearcherState,
    ResearchComplete,
    ThinkTool,
)

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
    if not tools:
        raise ValueError(
            "No research tools available. Please configure at least one search API "
            "(Tavily, etc.) or MCP server."
        )

    # Configure model
    complexity = "standard"  # Individual researcher always uses standard path
    model_name = research_config.get_model_for_complexity(complexity)

    model_config = {
        "model": model_name,
        "max_tokens": research_config.research_model_max_tokens,
        "tags": ["langsmith:nostream"],
    }

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

    system_prompt = RESEARCHER_SYSTEM_PROMPT.format(
        mcp_prompt=f"\n3. **MCP Tools**: Additional research tools\n{mcp_prompt}" if mcp_prompt else "",
        vision_tools=vision_tools_text,
        vision_guidance=vision_guidance_text,
        date=datetime.now().strftime("%Y-%m-%d"),
    )

    research_model = (
        configurable_model
        .bind_tools(tools)
        .with_retry(stop_after_attempt=research_config.max_structured_output_retries)
        .with_config(model_config)
    )

    messages = [SystemMessage(content=system_prompt)] + researcher_messages

    # === Image Injection (multimodal — deer-flow pattern) ===
    configurable = config.get("configurable") or {}
    viewed_images = configurable.get("viewed_images", {})
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
                configurable["viewed_images"] = {}
                config["configurable"] = configurable
        except ImportError:
            pass

    # === Loop Detection ===
    from agent.core.middleware import get_loop_detector
    loop_detector = get_loop_detector()
    recent_content = "\n".join([
        str(m.content)[:200]
        for m in researcher_messages[-5:]
        if hasattr(m, "content") and m.content
    ])
    if loop_detector.check(recent_content):
        logger.warning("[Researcher] Loop detected, forcing compression")
        return Command(
            goto="compress_research",
            update={"researcher_messages": researcher_messages},
        )

    response = await research_model.ainvoke(messages)

    # === Token Usage Tracking ===
    from agent.core.middleware import get_token_tracker
    tracker = get_token_tracker()
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
    tools_by_name = {
        getattr(t, "name", t.get("name", "")): t
        for t in tools
    }

    tool_calls = most_recent_message.tool_calls

    # === Handle view_image + extract_web_images calls: capture base64 for injection ===
    image_tool_names = {"view_image", "extract_web_images"}
    image_tool_calls = [tc for tc in tool_calls if tc.get("name") in image_tool_names]
    non_image_calls = [tc for tc in tool_calls if tc.get("name") not in image_tool_names]

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
        configurable = config.get("configurable") or {}
        existing = configurable.get("viewed_images", {})
        if isinstance(existing, dict):
            existing.update(captured_images)
        else:
            existing = captured_images
        configurable["viewed_images"] = existing
        config["configurable"] = configurable
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
    tool_outputs = list(image_tool_messages)
    for tc, obs in zip(non_image_calls, observations):
        tool_outputs.append(ToolMessage(
            content=str(obs),
            name=tc["name"],
            tool_call_id=tc["id"],
        ))

    # Check iteration limits
    exceeded_iterations = (
        state.get("tool_call_iterations", 0) >= research_config.max_react_tool_calls
    )
    if exceeded_iterations:
        logger.info("[ResearcherTools] Max iterations exceeded, compressing")
        return Command(
            goto="compress_research",
            update={"researcher_messages": tool_outputs},
        )

    # Continue research loop
    return Command(
        goto="researcher",
        update={"researcher_messages": tool_outputs},
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

    # Load search tools from Weaver's existing ecosystem
    try:
        from tools import tavily_search, fallback_search
        tools.append(tavily_search)
        tools.append(fallback_search)
        logger.debug("[Researcher] Loaded Tavily + fallback search")
    except ImportError:
        logger.warning("[Researcher] Could not import search tools from Weaver")

    # === Academic Retrievers (ArXiv, PubMed, Semantic Scholar) ===
    try:
        from tools.search.academic import arxiv_search, pubmed_search, semantic_scholar_search
        tools.extend([arxiv_search, pubmed_search, semantic_scholar_search])
        logger.debug("[Researcher] Loaded academic search tools (ArXiv, PubMed, Semantic Scholar)")
    except ImportError:
        logger.debug("[Researcher] Academic search tools not available")

    # === Sandbox Tools (code execution, shell, files) ===
    try:
        from tools.sandbox.sandbox_shell_tool import (
            SandboxExecuteCommandTool,
            SandboxCheckOutputTool,
        )
        from tools.sandbox.sandbox_files_tool import (
            SandboxCreateFileTool,
            SandboxReadFileTool,
        )
        from tools.code.code_executor_enhanced import CodeExecutorTool

        sandbox_shell = SandboxExecuteCommandTool()
        sandbox_check = SandboxCheckOutputTool()
        sandbox_files_read = SandboxReadFileTool()
        sandbox_files_create = SandboxCreateFileTool()
        code_tool = CodeExecutorTool()

        tools.extend([
            sandbox_shell, sandbox_check,
            sandbox_files_read, sandbox_files_create,
            code_tool,
        ])
        logger.debug("[Researcher] Loaded sandbox tools (shell, files, code)")
    except ImportError as e:
        logger.debug(f"[Researcher] Sandbox tools not available: {e}")
    except Exception as e:
        logger.warning(f"[Researcher] Failed to load sandbox tools: {e}")

    # Load MCP tools if enabled
    if research_config.mcp_enabled:
        try:
            from tools.core.mcp import init_mcp_tools
            mcp_tools = await init_mcp_tools(config)
            if mcp_tools:
                tools.extend(mcp_tools)
                logger.debug(f"[Researcher] Loaded {len(mcp_tools)} MCP tools")
        except Exception as e:
            logger.warning(f"[Researcher] Failed to load MCP tools: {e}")

    return tools


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

    try:
        from agent.core.middleware import ToolErrorHandler

        tool_call = {"name": getattr(tool, "name", "unknown"), "id": "researcher", "args": args}
        tools_by_name = {getattr(tool, "name", ""): tool}
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

    model_config = {
        "model": compression_model_name,
        "max_tokens": research_config.compression_model_max_tokens,
        "tags": ["langsmith:nostream"],
    }

    synthesizer = configurable_model.with_config(model_config)

    messages = list(researcher_messages)
    messages.append(HumanMessage(content=COMPRESSION_SIMPLE_HUMAN_MESSAGE))

    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            compression_prompt = COMPRESSION_SYSTEM_PROMPT.format(
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
    }
