"""SoulSearcher fixed-role vertical research graph.

The mainline graph is a single industry-intelligence workflow:
DomainRouter -> ResearchArchitect -> SourceScout -> EvidenceCurator ->
DataAnalyst -> ClaimVerifier -> CriticReviewer -> LeadWriter -> QualityGate ->
FinalReport.
"""

from __future__ import annotations

import logging
from typing import Optional

from langgraph.graph import END, START, StateGraph

from agent.core.configuration import ResearchConfiguration
from agent.core.state import AgentInputState, AgentState

logger = logging.getLogger(__name__)


def create_research_graph(
    checkpointer=None,
    interrupt_before: Optional[list[str]] = None,
    store=None,
):
    """Create the fixed-role vertical industry research graph."""
    from agent.workflows.vertical_research import (
        claim_verifier_node,
        critic_reviewer,
        data_analyst,
        domain_router,
        evidence_curator,
        final_report_node,
        lead_writer,
        quality_gate,
        research_architect,
        source_scout,
    )

    workflow = StateGraph(
        AgentState,
        input=AgentInputState,
        config_schema=ResearchConfiguration,
    )

    workflow.add_node("domain_router", domain_router)
    workflow.add_node("research_architect", research_architect)
    workflow.add_node("source_scout", source_scout)
    workflow.add_node("evidence_curator", evidence_curator)
    workflow.add_node("data_analyst", data_analyst)
    workflow.add_node("claim_verifier", claim_verifier_node)
    workflow.add_node("critic_reviewer", critic_reviewer)
    workflow.add_node("lead_writer", lead_writer)
    workflow.add_node("quality_gate", quality_gate)
    workflow.add_node("final_report", final_report_node)

    workflow.add_edge(START, "domain_router")
    workflow.add_edge("domain_router", "research_architect")
    workflow.add_edge("research_architect", "source_scout")
    workflow.add_edge("source_scout", "evidence_curator")
    workflow.add_edge("evidence_curator", "data_analyst")
    workflow.add_edge("data_analyst", "claim_verifier")
    workflow.add_edge("claim_verifier", "critic_reviewer")
    workflow.add_edge("critic_reviewer", "lead_writer")
    workflow.add_edge("lead_writer", "quality_gate")
    workflow.add_edge("quality_gate", "final_report")
    workflow.add_edge("final_report", END)

    graph = workflow.compile(
        checkpointer=checkpointer,
        store=store,
        interrupt_before=interrupt_before,
    )
    logger.info("[Graph] Fixed-role vertical research graph compiled successfully")
    return graph


def create_research_graph_with_checkpointer(
    database_url: str,
    interrupt_before: Optional[list[str]] = None,
):
    """Create the vertical graph with PostgreSQL checkpointing."""
    checkpointer = create_checkpointer(database_url)
    return create_research_graph(
        checkpointer=checkpointer,
        interrupt_before=interrupt_before,
    )


def create_checkpointer(database_url: str):
    """Create a PostgreSQL checkpointer for state persistence."""
    import asyncio

    try:
        import psycopg
    except ModuleNotFoundError as exc:
        raise RuntimeError("psycopg is required for PostgreSQL checkpointing") from exc

    from langgraph.checkpoint.postgres import PostgresSaver

    try:
        conn = psycopg.connect(database_url, autocommit=True)
        saver = PostgresSaver(conn)
        try:
            saver.setup()
        except RuntimeError as exc:
            if "event loop is already running" not in str(exc):
                raise
            loop = asyncio.get_event_loop()
            loop.run_until_complete(asyncio.to_thread(saver.setup))
        return saver
    except Exception as exc:
        raise RuntimeError(f"Failed to initialize PostgreSQL checkpointer: {exc}") from exc
