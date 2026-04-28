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
    agent_node,
    clarify_node,
    compressor_node,
    coordinator_node,
    deepsearch_node,
    direct_answer_node,
    evaluator_node,
    human_review_node,
    initiate_research,
    hitl_draft_review_node,
    hitl_plan_review_node,
    hitl_sources_review_node,
    perform_parallel_search,
    planner_node,
    refine_plan_node,
    revise_report_node,
    route_node,
    tree_search_node,
    web_search_plan_node,
    writer_node,
)

from .state import AgentState, QueryState

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
    """
    Create the research agent graph.

    The graph flow:
    1. START -> planner (creates research plan)
    2. planner -> [parallel] perform_parallel_search (executes searches)
    3. perform_parallel_search -> writer (aggregates results)
    4. writer -> END

    Optional hierarchical mode (use_hierarchical_agents=True):
    - Uses coordinator to decide next action: plan, research, synthesize, complete
    - Enables more intelligent research loop control
    """
    from common.config import settings

    use_hierarchical = getattr(settings, "use_hierarchical_agents", False)
    use_hybrid = use_hierarchical and getattr(settings, "use_hybrid_search", True)

    # Initialize the graph
    workflow = StateGraph(AgentState)

    # Add nodes
    workflow.add_node("router", route_node)
    workflow.add_node("direct_answer", direct_answer_node)
    workflow.add_node("agent", agent_node)
    workflow.add_node("clarify", clarify_node)
    workflow.add_node("planner", planner_node)
    workflow.add_node("web_plan", web_search_plan_node)
    workflow.add_node("refine_plan", refine_plan_node)
    workflow.add_node("hitl_plan_review", hitl_plan_review_node)
    workflow.add_node("perform_parallel_search", perform_parallel_search)
    workflow.add_node("writer", writer_node)
    workflow.add_node("hitl_draft_review", hitl_draft_review_node)
    workflow.add_node("evaluator", evaluator_node)
    workflow.add_node("reviser", revise_report_node)
    workflow.add_node("human_review", human_review_node)
    workflow.add_node("deepsearch", deepsearch_node)
    workflow.add_node("compressor", compressor_node)
    workflow.add_node("hitl_sources_review", hitl_sources_review_node)

    # Add coordinator node for hierarchical mode
    if use_hierarchical:
        workflow.add_node("coordinator", coordinator_node)
    if use_hybrid:
        workflow.add_node("tree_search", tree_search_node)

    # Set entry point
    workflow.set_entry_point("router")

    def route_decision(state: AgentState) -> str:
        route = state.get("route", "direct")
        logger.info(f"[route_decision] state['route'] = '{route}'")

        if route == "deep":
            if use_hierarchical:
                logger.info(
                    "[route_decision] → Routing to 'coordinator' node (hierarchical)"
                )
                return "coordinator"
            logger.info("[route_decision] → Routing to 'deepsearch' node")
            return "deepsearch"
        if route == "agent":
            logger.info("[route_decision] → Routing to 'agent' node")
            return "agent"
        if route == "web":
            logger.info("[route_decision] → Routing to 'web_plan' node")
            return "web_plan"
        if route == "direct":
            logger.info("[route_decision] → Routing to 'direct_answer' node")
            return "direct_answer"

        logger.info("[route_decision] → Routing to 'clarify' node (default)")
        return "clarify"

    route_targets = ["direct_answer", "agent", "web_plan", "clarify", "deepsearch"]
    if use_hierarchical:
        route_targets.append("coordinator")

    workflow.add_conditional_edges("router", route_decision, route_targets)

    # Coordinator edges (hierarchical mode only)
    if use_hierarchical:

        def after_coordinator(state: AgentState) -> str:
            action = state.get("coordinator_action", "research")
            logger.info(f"[after_coordinator] action='{action}'")
            if action == "synthesize":
                return "writer"
            elif action == "complete":
                return "human_review"
            # plan, research, reflect all go to search
            if use_hybrid:
                return "tree_search"
            return "planner"

        coord_targets = ["writer", "human_review"]
        if use_hybrid:
            coord_targets.append("tree_search")
        else:
            coord_targets.append("planner")
        workflow.add_conditional_edges("coordinator", after_coordinator, coord_targets)

        # Hybrid: tree_search → writer (via optional HITL sources review).
        # Skips compressor because the hybrid writer uses summary_notes + sources
        # directly (same as baseline _final_report), not compressed_knowledge.
        if use_hybrid:
            workflow.add_edge("tree_search", "hitl_sources_review")

    def after_clarify(state: AgentState) -> str:
        return "human_review" if state.get("needs_clarification") else "planner"

    workflow.add_conditional_edges(
        "clarify", after_clarify, ["planner", "human_review"]
    )

    # Planning path (agent + deep)
    workflow.add_edge("planner", "hitl_plan_review")
    workflow.add_edge("refine_plan", "hitl_plan_review")

    # Web search only path
    workflow.add_edge("web_plan", "hitl_plan_review")

    # Plan review (optional HITL) then dispatch searches
    workflow.add_conditional_edges(
        "hitl_plan_review", initiate_research, ["perform_parallel_search"]
    )

    # After search: deep mode goes through compressor, others go directly to writer
    def after_search(state: AgentState) -> str:
        if state.get("route") == "deep":
            return "compressor"
        return "writer"

    workflow.add_conditional_edges(
        "perform_parallel_search", after_search, ["compressor", "writer"]
    )

    # Compressor feeds into (optional) sources review, then writer.
    workflow.add_edge("compressor", "hitl_sources_review")
    workflow.add_edge("hitl_sources_review", "writer")

    def after_writer(state: AgentState) -> str:
        if state.get("route") == "deep":
            if use_hierarchical:
                # Hybrid: run evaluator for quality signals, then coordinator decides
                return "evaluator"
            return "evaluator"
        return "human_review"

    writer_targets = ["evaluator", "human_review"]
    workflow.add_edge("writer", "hitl_draft_review")
    workflow.add_conditional_edges("hitl_draft_review", after_writer, writer_targets)

    def after_evaluator(state: AgentState) -> str:
        """
        Decide next step based on evaluator verdict and dimensions.

        Routes:
        - "pass" → human_review (report is good)
        - "revise" with low coverage/missing topics → refine_plan (need more info)
        - "revise" with acceptable coverage → reviser (rewrite report)
        - "incomplete" → refine_plan (major gaps)
        - max_revisions exceeded → human_review (stop iterating)

        In hierarchical mode, revise/incomplete route back to coordinator
        instead of refine_plan, letting coordinator decide next action
        with full quality context.
        """
        verdict = state.get("verdict", "pass")
        revision_count = int(state.get("revision_count", 0))
        max_revisions = int(state.get("max_revisions", 0))

        # Check if we've exceeded max revisions
        if revision_count >= max_revisions:
            if use_hierarchical:
                return "coordinator"
            logger.info(
                f"Max revisions ({max_revisions}) reached, proceeding to human review"
            )
            return "human_review"

        if verdict == "pass":
            if use_hierarchical:
                return "coordinator"  # coordinator will see quality=pass and complete
            return "human_review"

        if verdict == "incomplete":
            if use_hierarchical:
                return "coordinator"  # coordinator decides: more research or complete
            return "refine_plan"

        # For "revise" verdict, check if we need more research or just a rewrite
        eval_dims = state.get("eval_dimensions", {})
        coverage = eval_dims.get("coverage", 0.7)
        missing_topics = state.get("missing_topics", [])

        if use_hierarchical:
            return "coordinator"  # let coordinator handle all revise decisions

        # Low coverage or missing topics → need more research
        if coverage < 0.6 or missing_topics:
            logger.info(
                f"Low coverage ({coverage:.2f}) or missing topics, routing to refine_plan"
            )
            return "refine_plan"

        # Acceptable coverage but poor writing → rewrite
        logger.info("Coverage acceptable, routing to reviser for rewrite")
        return "reviser"

    evaluator_targets = ["refine_plan", "reviser", "human_review"]
    if use_hierarchical:
        evaluator_targets.append("coordinator")
    workflow.add_conditional_edges("evaluator", after_evaluator, evaluator_targets)

    # Reviser rewrites the report and goes back to evaluator
    workflow.add_edge("reviser", "evaluator")

    # Direct answer path
    workflow.add_edge("direct_answer", "human_review")
    workflow.add_edge("agent", "human_review")

    # Final edge
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
