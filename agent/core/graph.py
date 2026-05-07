import asyncio
import logging
from pathlib import Path

from langgraph.graph import END, StateGraph

try:
    import psycopg
except ModuleNotFoundError:
    psycopg = None

if psycopg is not None:
    from langgraph.checkpoint.postgres import PostgresSaver

from agent.workflows.nodes import (
    deepsearch_node,
    direct_answer_node,
    human_review_node,
    route_node,
)

from .state import AgentState

logger = logging.getLogger(__name__)


if psycopg is not None:

    class AsyncCompatPostgresSaver(PostgresSaver):
        """Add async checkpoint methods to the sync Postgres saver used by this app."""

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
                self.put,
                config,
                checkpoint,
                metadata,
                new_versions,
            )

        async def aput_writes(self, config, writes, task_id, task_path=""):
            return await asyncio.to_thread(
                self.put_writes,
                config,
                writes,
                task_id,
                task_path,
            )

        async def adelete_thread(self, thread_id: str):
            return await asyncio.to_thread(self.delete_thread, thread_id)

else:

    class AsyncCompatPostgresSaver:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("psycopg is required for PostgreSQL checkpointing")


def create_research_graph(checkpointer=None, interrupt_before=None, store=None):
    from common.config import settings

    workflow = StateGraph(AgentState)

    workflow.add_node("router", route_node)
    workflow.add_node("direct_answer", direct_answer_node)
    workflow.add_node("human_review", human_review_node)
    workflow.add_node("deepsearch", deepsearch_node)

    workflow.set_entry_point("router")

    def route_decision(state: AgentState) -> str:
        route = state.get("route", "direct")
        logger.info(f"[route_decision] state['route'] = '{route}'")

        if route == "deep":
            logger.info("[route_decision] → Routing to 'deepsearch' node")
            return "deepsearch"

        logger.info("[route_decision] → Routing to 'direct_answer' node")
        return "direct_answer"

    workflow.add_conditional_edges(
        "router", route_decision, ["direct_answer", "deepsearch"]
    )
    workflow.add_edge("direct_answer", "human_review")
    workflow.add_edge("deepsearch", "human_review")
    workflow.add_edge("human_review", END)

    # HITL checkpoints are implemented via explicit review nodes that use
    # `langgraph.types.interrupt()` (see agent/workflows/nodes.py).
    hitl_checkpoints = getattr(settings, "hitl_checkpoints", "") or ""
    if hitl_checkpoints.strip():
        logger.info(f"HITL checkpoints enabled: {hitl_checkpoints}")

    # Compile the graph
    graph = workflow.compile(
        checkpointer=checkpointer,
        store=store,
        interrupt_before=interrupt_before,
    )

    logger.info("Research graph compiled successfully")

    return graph


def export_graph_mermaid(
    output_path: str = "graph_mermaid.md", xray: bool = True
) -> Path:
    """
    Export the compiled graph to a mermaid markdown file for visualization.
    """
    graph = create_research_graph(checkpointer=None, interrupt_before=None)
    mermaid = graph.get_graph(xray=xray).draw_mermaid()
    path = Path(output_path)
    path.write_text(f"```mermaid\n{mermaid}\n```", encoding="utf-8")
    logger.info(f"Graph mermaid exported to {path}")
    return path


def create_checkpointer(database_url: str):
    """
    Create a PostgreSQL checkpointer for state persistence.

    This allows long-running agents to pause/resume and handle failures.
    """
    if not database_url:
        raise ValueError(
            "database_url is required to initialize the Postgres checkpointer."
        )
    if psycopg is None:
        raise RuntimeError(
            "psycopg is required to initialize the Postgres checkpointer."
        )

    # Create connection (psycopg3)
    try:
        conn = psycopg.connect(database_url, autocommit=True)
    except Exception as e:
        raise RuntimeError(
            f"Failed to connect to Postgres for checkpointer: {e}"
        ) from e

    # Create checkpointer
    checkpointer = AsyncCompatPostgresSaver(conn)

    # Setup tables
    checkpointer.setup()

    logger.info("PostgreSQL checkpointer initialized")
    return checkpointer
