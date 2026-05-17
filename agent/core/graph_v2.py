"""Unified Deep Research Graph — the "Strongest Version" integrating all three projects.

Architecture (from unified design):
┌─────────────────────────────────────────────────────────────────┐
│ INPUT GATEWAY                                                   │
│  clarify_with_user → write_research_brief → classify_complexity │
│       ↓                        ↓                    ↓           │
│    [END if need]          [always next]     simple→direct_answer│
│                                              standard/deep→sup  │
├─────────────────────────────────────────────────────────────────┤
│ RESEARCH EXECUTION                                              │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ SUPERVISOR SUBGRAPH                                        │  │
│  │  supervisor ⇄ supervisor_tools                            │  │
│  │    tools: ConductResearch, ThinkTool, SourceCurate,        │  │
│  │           ResearchComplete, [ResearchDeep - Phase 2]       │  │
│  │    ConductResearch spawns Researcher Subgraphs in parallel │  │
│  └───────────────────────────────────────────────────────────┘  │
│                              ↓                                   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ RESEARCHER SUBGRAPH (N parallel instances)                 │  │
│  │  researcher ⇄ researcher_tools → compress_research        │  │
│  │    tools: search, think_tool, ResearchComplete, MCP        │  │
│  │    compression: raw→embedding→LLM (mixed gradient)         │  │
│  └───────────────────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────────────────┤
│ REPORT GENERATION                                               │
│  [source_curation] → final_report_generation → END              │
└─────────────────────────────────────────────────────────────────┘

Key design principles:
1. Explicit data flow via typed State (not implicit middleware)
2. Subgraph nesting for clear boundaries (open_deep_research pattern)
3. Adaptive routing by complexity (unified design innovation)
4. Three-tier model routing (fast/smart/strategic)

Graph nodes:
    clarify_with_user ──→ write_research_brief ──→ classify_complexity
                                │                          │
                    [always next]              simple → direct_answer → END
                                               standard/deep → research_supervisor
                                                                    │
                                                    supervisor_subgraph (nested)
                                                                    │
                                                       final_report_generation
                                                                    │
                                                                   END
"""

from __future__ import annotations

import logging
from typing import Optional

from langgraph.graph import END, START, StateGraph

from agent.core.configuration import ResearchConfiguration
from agent.core.state_v2 import AgentInputState, AgentState

logger = logging.getLogger(__name__)


# =============================================================================
# Graph Construction
# =============================================================================

def create_unified_research_graph(
    checkpointer=None,
    interrupt_before: Optional[list[str]] = None,
    store=None,
):
    """Create the unified deep research graph.

    This is the main entry point for the Weaver Deep Research Agent v2.
    It compiles the full graph with subgraph nesting for clean boundaries.

    Args:
        checkpointer: Optional LangGraph checkpointer for state persistence.
        interrupt_before: Optional list of node names to interrupt before (HITL).
        store: Optional LangGraph store.

    Returns:
        Compiled LangGraph StateGraph.
    """
    # Lazy imports to avoid circular dependencies
    from agent.workflows.input_gateway import (
        clarify_with_user,
        classify_complexity,
        direct_answer,
        write_research_brief,
    )
    from agent.workflows.report_v2 import final_report_generation
    from agent.workflows.supervisor_v2 import build_supervisor_subgraph

    workflow = StateGraph(
        AgentState,
        input=AgentInputState,
        config_schema=ResearchConfiguration,
    )

    # === Build and add nodes ===

    # Input Gateway nodes
    workflow.add_node("clarify_with_user", clarify_with_user)
    workflow.add_node("write_research_brief", write_research_brief)
    workflow.add_node("classify_complexity", classify_complexity)

    # Fast path
    workflow.add_node("direct_answer", direct_answer)

    # Research Supervisor (compiled subgraph - open_deep_research pattern)
    workflow.add_node("research_supervisor", build_supervisor_subgraph())

    # Final Report Generation
    workflow.add_node("final_report_generation", final_report_generation)

    # === Define edges ===

    # Entry → Clarify
    workflow.add_edge(START, "clarify_with_user")
    # clarify_with_user has conditional edges: write_research_brief or __end__

    # Research brief → Complexity classification
    # (write_research_brief always goes to classify_complexity via Command)

    # classify_complexity has conditional edges: direct_answer or research_supervisor

    # Research supervisor → Final report
    workflow.add_edge("research_supervisor", "final_report_generation")

    # Direct answer → End
    workflow.add_edge("direct_answer", END)

    # Final report → End
    workflow.add_edge("final_report_generation", END)

    # === Compile ===
    graph = workflow.compile(
        checkpointer=checkpointer,
        store=store,
        interrupt_before=interrupt_before,
    )

    logger.info("[GraphV2] Unified research graph compiled successfully")
    return graph


# =============================================================================
# Convenience: Graph with Checkpointer
# =============================================================================

def create_research_graph_with_checkpointer(
    database_url: str,
    interrupt_before: Optional[list[str]] = None,
):
    """Create the unified graph with PostgreSQL checkpointer.

    Args:
        database_url: PostgreSQL connection URL.
        interrupt_before: Optional HITL interrupt points.

    Returns:
        Compiled graph with persistence.
    """
    import asyncio

    try:
        import psycopg
    except ModuleNotFoundError:
        raise RuntimeError("psycopg is required for PostgreSQL checkpointing")

    from langgraph.checkpoint.postgres import PostgresSaver

    # Create connection
    try:
        conn = psycopg.connect(database_url, autocommit=True)
    except Exception as e:
        raise RuntimeError(f"Failed to connect to Postgres: {e}") from e

    # Create async-compatible checkpointer
    class AsyncCompatPostgresSaver(PostgresSaver):
        async def aget_tuple(self, config):
            return await asyncio.to_thread(self.get_tuple, config)

        async def alist(self, config, *, filter=None, before=None, limit=None):
            items = await asyncio.to_thread(
                lambda: list(
                    self.list(config, filter=filter, before=before, limit=limit)
                )
            )
            for item in items:
                yield item

        async def aput(self, config, checkpoint, metadata, new_versions):
            return await asyncio.to_thread(
                self.put, config, checkpoint, metadata, new_versions
            )

        async def aput_writes(self, config, writes, task_id, task_path=""):
            return await asyncio.to_thread(
                self.put_writes, config, writes, task_id, task_path
            )

        async def adelete_thread(self, thread_id: str):
            return await asyncio.to_thread(self.delete_thread, thread_id)

    checkpointer = AsyncCompatPostgresSaver(conn)
    checkpointer.setup()

    logger.info("[GraphV2] PostgreSQL checkpointer initialized")

    return create_unified_research_graph(
        checkpointer=checkpointer,
        interrupt_before=interrupt_before,
    )
