import operator
from typing import Annotated, Any, Literal, Optional, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from agent.core.message_utils import summarize_messages
from common.config import settings

from .middleware import mask_old_observations, maybe_strip_tool_messages


def capped_add_messages(
    existing: list[BaseMessage] | None, new: list[BaseMessage] | None
) -> list[BaseMessage]:
    """
    Aggregate messages and trim to keep context bounded.

    Uses a hybrid strategy (observation masking + optional LLM summarization):
    1. Merge new messages via LangGraph's add_messages.
    2. Apply legacy strip_tool_messages if enabled (backward compat).
    3. Apply observation masking: mask stale ToolMessage observations while
       keeping reasoning/action history intact (KV-cache friendly).
    4. If trim_messages is enabled, keep head (immutable prefix for KV-cache
       stability) + tail, with optional summarization of the middle.
    """
    merged = add_messages(existing, new)
    merged = maybe_strip_tool_messages(merged)

    # Hybrid observation masking — replaces stale observations with compact
    # placeholders, preserving reasoning and action history.
    merged = mask_old_observations(merged)

    if not settings.trim_messages:
        return merged

    keep_first = max(int(getattr(settings, "trim_messages_keep_first", 1)), 0)
    keep_last = max(int(getattr(settings, "trim_messages_keep_last", 8)), 0)
    if keep_first + keep_last == 0 or len(merged) <= keep_first + keep_last:
        return merged

    # KV-cache friendly: head (prefix) is immutable — never modify these messages
    head = merged[:keep_first] if keep_first else []
    tail = merged[-keep_last:] if keep_last else []

    # Optional summarization of middle history (hybrid: masking first, then summarize)
    if settings.summary_messages and len(merged) > settings.summary_messages_trigger:
        middle = merged[keep_first : len(merged) - keep_last]
        summary_msg = summarize_messages(middle)
        # Append summary after immutable prefix (not replacing head messages)
        trimmed = head + [summary_msg] + tail
    else:
        trimmed = head + tail

    return trimmed


# Execution status type
ExecutionStatus = Literal[
    "pending", "running", "paused", "completed", "failed", "cancelled"
]


class AgentState(TypedDict):
    """
    The state schema for the research agent.
    This represents the agent's "short-term memory" during a research session.

    Enhanced with fields from Manus for better tracking and control.
    """

    # ============ Input/Output ============
    # User's original input/query
    input: str
    # Optional base64-encoded images from the user
    images: list[dict[str, Any]]
    # Final report/answer
    final_report: str
    # Draft report for evaluator/optimizer loop
    draft_report: str

    # ============ User Context ============
    # User identifier for memory/namespace
    user_id: str
    # Thread/conversation identifier
    thread_id: str
    # Agent profile ID (for GPTs-like behavior)
    agent_id: str

    # ============ Execution Control ============
    # Message history for LLM context (auto-trimmed via capped_add_messages)
    messages: Annotated[list[BaseMessage], capped_add_messages]
    # Structured research plan (list of search queries/steps)
    research_plan: list[str]
    # Current step being executed
    current_step: int
    # Execution status
    status: ExecutionStatus
    # Completion flag
    is_complete: bool
    # Start timestamp (ISO format)
    started_at: str
    # End timestamp (ISO format)
    ended_at: str

    # ============ Routing ============
    # Routing decision: direct, agent, web, deep, clarify
    route: str
    # Routing reasoning (from smart router)
    routing_reasoning: str
    # Routing confidence (0-1)
    routing_confidence: float
    # Suggested queries from router
    suggested_queries: list[str]

    # ============ Clarification ============
    # Flag for clarify step
    needs_clarification: bool
    # Clarification question to ask user
    clarification_question: str

    # ============ Research Data ============
    # All scraped content from searches
    scraped_content: Annotated[list[dict[str, Any]], operator.add]
    # Code execution results
    code_results: Annotated[list[dict[str, Any]], operator.add]
    # Summary notes from deep search
    summary_notes: list[str]
    # Sources collected
    sources: list[dict[str, str]]

    # ============ Deep Search Artifacts ============
    # Structured artifacts from deep search (quality metrics, claims, sources, etc.)
    deepsearch_artifacts: dict[str, Any]
    # Quality summary from deep search diagnostics
    quality_summary: dict[str, Any]

    # ============ Quality Control ============
    # Evaluation feedback for optimizer
    evaluation: str
    # Evaluator verdict ("pass" / "revise" / "incomplete")
    verdict: str
    # Structured evaluation dimensions (coverage, accuracy, freshness, coherence)
    eval_dimensions: dict[str, float]
    # Missing topics identified by evaluator
    missing_topics: list[str]
    # Revision control
    revision_count: int
    max_revisions: int

    # ============ Tool Control ============
    # Tool approval gating
    tool_approved: bool
    # Pending tool calls awaiting approval
    pending_tool_calls: list[dict[str, Any]]
    # Tool call accounting
    tool_call_count: int
    # Maximum tool calls allowed
    tool_call_limit: int
    # Tools enabled for this session
    enabled_tools: dict[str, bool]

    # ============ Cancellation & Error ============
    # Cancellation support
    cancel_token_id: Optional[str]  # 取消令牌 ID
    is_cancelled: bool  # 是否已取消
    # Error tracking
    errors: Annotated[list[str], operator.add]
    # Last error message
    last_error: str

    # ============ Research Tree ============
    # Tree-based research structure (serialized dict)
    research_tree: dict[str, Any]
    # Current branch being explored
    current_branch_id: Optional[str]
    # Whether tree exploration is enabled
    tree_exploration_enabled: bool

    # ============ Hierarchical Agent Control ============
    # Coordinator's chosen action (plan, research, synthesize, reflect, complete)
    coordinator_action: str
    # Coordinator's reasoning for the decision
    coordinator_reasoning: str
    # Number of coordinator iterations (prevents infinite loops)
    coordinator_iterations: int

    # ============ Compressed Knowledge ============
    # Structured compressed knowledge from research
    compressed_knowledge: dict[str, Any]

    # ============ Domain Routing ============
    # Detected research domain (scientific, legal, financial, etc.)
    domain: str
    # Domain-specific configuration (search hints, sources, etc.)
    domain_config: dict[str, Any]

    # ============ Sub-Agent Context Isolation ============
    # Tracking of sub-agent contexts for parallel branches
    sub_agent_contexts: dict[str, dict[str, Any]]

    # ============ Metrics ============
    # Token usage tracking
    total_input_tokens: int
    total_output_tokens: int


class ResearchPlan(TypedDict):
    """Structured research plan output."""

    queries: list[str]
    reasoning: str


class SearchResult(TypedDict):
    """Search result structure."""

    query: str
    results: list[dict[str, Any]]
    timestamp: str


class CodeExecution(TypedDict):
    """Code execution result structure."""

    code: str
    output: str
    error: str | None
    image: str | None  # Base64 encoded image if generated


class QueryState(TypedDict):
    """State for a single parallel research query."""

    query: str
