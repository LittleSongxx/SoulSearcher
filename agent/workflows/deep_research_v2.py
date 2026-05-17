"""Deep Research Tool: Breadth × Depth Recursive Research Algorithm.

Phase 2 implementation based on gpt-researcher's DeepResearchSkill.
Provides deterministic deep coverage when the supervisor needs comprehensive research.

Algorithm (from gpt-researcher):
1. For each depth level, generate `breadth` search queries
2. Execute queries concurrently (with semaphore for rate limiting)
3. For each query result:
   a. Execute search via retrievers
   b. Scrape and summarize content
   c. Extract learnings and follow-up questions via LLM
4. If depth > 1, recursively research each follow-up with breadth // 2 and depth - 1
5. Accumulate all learnings, citations, visited URLs, and context
6. Return structured results to the supervisor

Key differences from gpt-researcher:
- Integrated into LangGraph subgraph pattern (not standalone class)
- Uses our model routing (strategic_llm for query generation, fast_llm for summarization)
- Structured output for supervisor consumption
- Reuses existing Weaver search infrastructure
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_core.runnables import RunnableConfig

from agent.core.configuration import ResearchConfiguration
from agent.core.model_routing import configurable_model

logger = logging.getLogger(__name__)

# Maximum words in accumulated context (from gpt-researcher)
MAX_CONTEXT_WORDS = 25000


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class ResearchProgress:
    """Track research progress across depth levels."""
    current_depth: int = 1
    total_depth: int = 2
    current_breadth: int = 0
    total_breadth: int = 4
    total_queries: int = 0
    completed_queries: int = 0


@dataclass
class DeepResearchResult:
    """Structured result from a deep research execution."""
    learnings: list[str] = field(default_factory=list)
    citations: dict[str, str] = field(default_factory=dict)
    visited_urls: list[str] = field(default_factory=list)
    context: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)


# =============================================================================
# Utility: Word Counting & Trimming
# =============================================================================

def _count_words(text) -> int:
    """Count words in text, handling both strings and lists."""
    if isinstance(text, list):
        text = " ".join(str(item) for item in text)
    return len(str(text).split())


def _trim_context_to_word_limit(
    context_list: list[str], max_words: int = MAX_CONTEXT_WORDS
) -> list[str]:
    """Trim context to stay within word limit, keeping most recent items."""
    total_words = 0
    trimmed = []
    for item in reversed(context_list):
        words = _count_words(item)
        if total_words + words <= max_words:
            trimmed.insert(0, item)
            total_words += words
        else:
            break
    return trimmed


# =============================================================================
# Search Query Generation (strategic_llm)
# =============================================================================

async def _generate_search_queries(
    query: str,
    num_queries: int,
    research_config: ResearchConfiguration,
    config: RunnableConfig,
) -> list[dict[str, str]]:
    """Generate diverse search queries using the strategic LLM.

    Pattern from gpt-researcher: DeepResearchSkill.generate_search_queries.
    """
    model_config = {
        "model": research_config.strategic_llm,
        "max_tokens": 1024,
        "temperature": 0.4,
        "tags": ["langsmith:nostream"],
    }

    prompt = (
        f"Given the following prompt, generate {num_queries} unique search queries "
        f"to research the topic thoroughly. For each query, provide a research goal.\n\n"
        f"Format as 'Query: <query>' followed by 'Goal: <goal>' for each pair.\n\n"
        f"Prompt: {query}"
    )

    from langchain_core.messages import HumanMessage, SystemMessage

    try:
        response = await configurable_model.with_config(model_config).ainvoke([
            SystemMessage(content="You are an expert researcher generating search queries."),
            HumanMessage(content=prompt),
        ])
    except Exception as e:
        logger.error(f"[DeepResearch] Query generation failed: {e}")
        # Fallback: use the original query
        return [{"query": query, "researchGoal": "Research the main topic"}]

    content = response.content if hasattr(response, "content") else str(response)
    lines = content.split("\n")

    queries = []
    current_query = {}
    for line in lines:
        line = line.strip()
        if line.startswith("Query:"):
            if current_query:
                queries.append(current_query)
            current_query = {"query": line.replace("Query:", "").strip()}
        elif line.startswith("Goal:") and current_query:
            current_query["researchGoal"] = line.replace("Goal:", "").strip()

    if current_query:
        queries.append(current_query)

    return queries[:num_queries]


# =============================================================================
# Single Query Processing
# =============================================================================

async def _process_single_query(
    serp_query: dict[str, str],
    research_config: ResearchConfiguration,
    config: RunnableConfig,
) -> dict[str, Any] | None:
    """Process a single search query: search → scrape → extract learnings.

    Each query spawns a mini research pipeline:
    1. Search via available search tools
    2. Scrape top results (Phase 2: simple URL-based)
    3. Extract learnings and follow-up questions via LLM

    Pattern from gpt-researcher: DeepResearchSkill.process_query (inner function).
    """
    query_text = serp_query.get("query", "")
    research_goal = serp_query.get("researchGoal", "")

    if not query_text:
        return None

    try:
        # Step 1: Search using available tools
        search_results = await _execute_search(query_text, research_config, config)

        # Step 2: Build context from search results
        context_str = _build_context_from_results(search_results)

        if not context_str:
            logger.warning(f"[DeepResearch] No results for query: {query_text[:100]}")
            return None

        # Step 3: Extract learnings and follow-up questions
        extractions = await _extract_learnings(
            query_text, context_str, research_config, config
        )

        return {
            "learnings": extractions.get("learnings", []),
            "visited_urls": [r.get("url", "") for r in search_results if r.get("url")],
            "followUpQuestions": extractions.get("followUpQuestions", []),
            "researchGoal": research_goal,
            "citations": extractions.get("citations", {}),
            "context": context_str,
            "sources": search_results,
        }

    except Exception as e:
        logger.error(f"[DeepResearch] Error processing query '{query_text[:100]}': {e}")
        return None


async def _execute_search(
    query: str,
    research_config: ResearchConfiguration,
    config: RunnableConfig,
) -> list[dict]:
    """Execute search using Weaver's existing search tools."""
    results = []

    # Try Tavily search
    try:
        from tools import tavily_search
        search_response = await tavily_search.ainvoke(
            {"query": query, "max_results": 5},
            config,
        )
        if isinstance(search_response, str):
            # Parse string response
            results.append({"url": "", "title": "", "content": search_response})
        elif isinstance(search_response, list):
            results.extend(search_response)
        elif isinstance(search_response, dict):
            results.append(search_response)
    except Exception as e:
        logger.warning(f"[DeepResearch] Tavily search failed: {e}")

    # Try fallback search
    if not results:
        try:
            from tools import fallback_search
            fallback_response = await fallback_search.ainvoke(
                {"query": query, "max_results": 5},
                config,
            )
            if isinstance(fallback_response, str):
                results.append({"url": "", "title": "", "content": fallback_response})
            elif isinstance(fallback_response, list):
                results.extend(fallback_response)
        except Exception as e:
            logger.warning(f"[DeepResearch] Fallback search failed: {e}")

    # Try academic search providers (ArXiv, PubMed, Semantic Scholar)
    academic_providers = _get_academic_providers()
    for provider in academic_providers:
        try:
            provider_results = provider.search(query, max_results=3)
            for r in provider_results:
                results.append({
                    "url": getattr(r, "url", ""),
                    "title": getattr(r, "title", ""),
                    "content": getattr(r, "snippet", "") or getattr(r, "content", ""),
                })
        except Exception as e:
            logger.debug(f"[DeepResearch] Academic provider {provider.name} failed: {e}")

    return results


def _get_academic_providers() -> list:
    """Get available academic search providers from Weaver's ecosystem."""
    providers = []
    try:
        from tools.search.academic import ArxivProvider, PubMedProvider, SemanticScholarProvider
        arxiv = ArxivProvider()
        if arxiv.is_available():
            providers.append(arxiv)
        pubmed = PubMedProvider()
        if pubmed.is_available():
            providers.append(pubmed)
        semantic = SemanticScholarProvider()
        if semantic.is_available():
            providers.append(semantic)
    except ImportError:
        pass
    return providers


def _build_context_from_results(search_results: list[dict]) -> str:
    """Build a context string from search results."""
    parts = []
    for i, result in enumerate(search_results[:10]):
        title = result.get("title", "")
        url = result.get("url", "")
        content = result.get("content") or result.get("raw_excerpt") or result.get("snippet", "")

        if content:
            parts.append(f"[{i+1}] Title: {title}\nURL: {url}\nContent: {content[:1500]}\n")

    return "\n---\n".join(parts)


async def _extract_learnings(
    query: str,
    context: str,
    research_config: ResearchConfiguration,
    config: RunnableConfig,
) -> dict[str, Any]:
    """Extract learnings and follow-up questions from search context.

    Uses strategic_llm for high-quality extraction.
    """
    model_config = {
        "model": research_config.strategic_llm,
        "max_tokens": 2000,
        "temperature": 0.4,
        "tags": ["langsmith:nostream"],
    }

    prompt = (
        f"Given the following research results for the query '{query}', "
        f"extract key learnings and suggest follow-up questions.\n\n"
        f"For each learning, include a citation to the source URL in brackets.\n"
        f"Format each learning as 'Learning [source_url]: <insight>'\n"
        f"Format each question as 'Question: <question>'\n\n"
        f"Research Results:\n{context}"
    )

    from langchain_core.messages import HumanMessage, SystemMessage

    try:
        response = await configurable_model.with_config(model_config).ainvoke([
            SystemMessage(content="You are an expert researcher analyzing search results."),
            HumanMessage(content=prompt),
        ])
    except Exception as e:
        logger.error(f"[DeepResearch] Learning extraction failed: {e}")
        return {"learnings": [], "followUpQuestions": [], "citations": {}}

    content = response.content if hasattr(response, "content") else str(response)

    # Parse learnings, questions, and citations
    import re
    learnings = []
    questions = []
    citations = {}

    for line in content.split("\n"):
        line = line.strip()
        if not line:
            continue

        if line.lower().startswith("learning"):
            url_match = re.search(r'\[(.*?)\]', line)
            if url_match:
                url = url_match.group(1)
                learning = line.split("]:", 1)[-1].strip() if "]:" in line else line
                clean_learning = re.sub(r'\[.*?\]', '', learning).strip()
                if clean_learning.lower().startswith("learning"):
                    clean_learning = clean_learning.split(":", 1)[-1].strip() if ":" in clean_learning else clean_learning[8:].strip()
                learnings.append(clean_learning)
                citations[clean_learning] = url
            else:
                url_match = re.search(r'https?://[^\s)\]]+', line)
                if url_match:
                    url = url_match.group(0)
                    clean = re.sub(r'https?://[^\s)\]]+', '', line).strip()
                    if clean.lower().startswith("learning"):
                        clean = clean.split(":", 1)[-1].strip() if ":" in clean else clean[8:].strip()
                    learnings.append(clean)
                    citations[clean] = url
                else:
                    learning_text = line.split(":", 1)[-1].strip() if ":" in line else line
                    learnings.append(learning_text)
        elif line.lower().startswith("question:"):
            questions.append(line.split(":", 1)[-1].strip())

    return {
        "learnings": learnings,
        "followUpQuestions": questions,
        "citations": citations,
    }


# =============================================================================
# Main Recursive Deep Research Algorithm
# =============================================================================

async def execute_deep_research(
    query: str,
    breadth: int,
    depth: int,
    config: RunnableConfig,
    learnings: list[str] | None = None,
    citations: dict[str, str] | None = None,
    visited_urls: set[str] | None = None,
) -> DeepResearchResult:
    """Execute breadth × depth recursive deep research.

    This is the core algorithm from gpt-researcher's DeepResearchSkill,
    adapted for the LangGraph subgraph architecture.

    Args:
        query: The research question/topic.
        breadth: Number of search queries to generate per depth level.
        depth: Recursion depth (1 = single pass, 2 = follow-up on results, 3 = thorough).
        config: RunnableConfig for model access.
        learnings: Accumulated learnings from parent calls (for recursion).
        citations: Accumulated citations from parent calls.
        visited_urls: Already visited URLs to avoid duplicates.

    Returns:
        DeepResearchResult with all accumulated learnings, citations, and context.
    """
    research_config = ResearchConfiguration.from_runnable_config(config)

    logger.info(
        f"[DeepResearch] Starting: depth={depth}, breadth={breadth}, "
        f"query='{query[:100]}...'"
    )

    if learnings is None:
        learnings = []
    if citations is None:
        citations = {}
    if visited_urls is None:
        visited_urls = set()

    # Step 1: Generate search queries
    logger.info(f"[DeepResearch] Generating {breadth} search queries...")
    serp_queries = await _generate_search_queries(
        query, breadth, research_config, config
    )
    logger.info(f"[DeepResearch] Generated {len(serp_queries)} queries")

    all_learnings = list(learnings)
    all_citations = dict(citations)
    all_visited_urls = set(visited_urls)
    all_context = []
    all_sources = []

    # Step 2: Process queries with concurrency limit
    concurrency = research_config.deep_research_concurrency
    semaphore = asyncio.Semaphore(concurrency)

    async def bounded_process(serp_query: dict[str, str]) -> dict[str, Any] | None:
        async with semaphore:
            return await _process_single_query(serp_query, research_config, config)

    tasks = [bounded_process(q) for q in serp_queries]
    results = await asyncio.gather(*tasks)
    results = [r for r in results if r is not None]

    logger.info(f"[DeepResearch] {len(results)}/{len(serp_queries)} queries successful")

    # Step 3: Collect results and recurse deeper
    for result in results:
        all_learnings.extend(result.get("learnings", []))
        all_visited_urls.update(result.get("visited_urls", []))
        all_citations.update(result.get("citations", {}))

        if result.get("context"):
            all_context.append(result["context"])
        if result.get("sources"):
            all_sources.extend(result["sources"])

        # Step 4: Recursive deeper research
        if depth > 1:
            new_breadth = max(2, breadth // 2)
            new_depth = depth - 1

            follow_ups = result.get("followUpQuestions", [])
            research_goal = result.get("researchGoal", "")

            next_query = f"""
Previous research goal: {research_goal}
Follow-up questions: {' '.join(follow_ups[:3])}
"""

            deeper = await execute_deep_research(
                query=next_query,
                breadth=new_breadth,
                depth=new_depth,
                config=config,
                learnings=all_learnings,
                citations=all_citations,
                visited_urls=all_visited_urls,
            )

            all_learnings = deeper.learnings
            all_visited_urls.update(deeper.visited_urls)
            all_citations.update(deeper.citations)
            if deeper.context:
                all_context.extend(deeper.context)
            if deeper.sources:
                all_sources.extend(deeper.sources)

    # Step 5: Deduplicate and trim
    unique_learnings = list(dict.fromkeys(all_learnings))  # Preserve order
    trimmed_context = _trim_context_to_word_limit(all_context)

    logger.info(
        f"[DeepResearch] Complete: {len(unique_learnings)} learnings, "
        f"{len(all_visited_urls)} URLs, {len(trimmed_context)} context items"
    )

    return DeepResearchResult(
        learnings=unique_learnings,
        citations=all_citations,
        visited_urls=list(all_visited_urls),
        context=trimmed_context,
        sources=all_sources,
    )


# =============================================================================
# Supervisor-facing Tool Wrapper
# =============================================================================

def format_deep_research_result(result: DeepResearchResult) -> str:
    """Format deep research results for supervisor consumption.

    Returns a structured string that the supervisor can read as a ToolMessage.
    """
    parts = []

    parts.append(f"## Deep Research Complete")
    parts.append(f"**Learnings extracted**: {len(result.learnings)}")
    parts.append(f"**Sources visited**: {len(result.visited_urls)}")

    if result.learnings:
        parts.append(f"\n### Key Learnings\n")
        for i, learning in enumerate(result.learnings[:20], 1):
            citation = result.citations.get(learning, "")
            if citation:
                parts.append(f"{i}. {learning} [Source: {citation}]")
            else:
                parts.append(f"{i}. {learning}")

        if len(result.learnings) > 20:
            parts.append(f"\n... and {len(result.learnings) - 20} more learnings")

    if result.visited_urls:
        parts.append(f"\n### Sources Visited\n")
        for url in result.visited_urls[:15]:
            parts.append(f"- {url}")

    if result.context:
        # Include summarized context
        parts.append(f"\n### Research Context Summary\n")
        total_context = "\n".join(result.context)
        parts.append(total_context[:5000])  # Limit for supervisor context

    return "\n".join(parts)
