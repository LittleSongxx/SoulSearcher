"""Unified state definitions for the Deep Research Agent.

Integrates patterns from:
- open_deep_research: override_reducer, SupervisorState/ResearcherState subgraph nesting
- gpt-researcher: depth/breadth tracking, learnings accumulation
- deer-flow: Structured output models for tool calls
"""

import operator
from typing import Annotated, Any, Literal, Optional

from langchain_core.messages import BaseMessage, MessageLikeRepresentation
from langgraph.graph import MessagesState
from pydantic import BaseModel, Field
from typing_extensions import TypedDict


# =============================================================================
# Reducers
# =============================================================================

def override_reducer(current_value, new_value):
    """Reducer that allows complete override via {'type': 'override', 'value': ...}.

    Used for state fields that need full replacement (not accumulation).
    Pattern from open_deep_research.
    """
    if isinstance(new_value, dict) and new_value.get("type") == "override":
        return new_value.get("value", new_value)
    return operator.add(current_value, new_value)


# =============================================================================
# Structured Output Models (Pydantic - used as LangChain tools)
# =============================================================================

class ClarifyWithUser(BaseModel):
    """Structured clarification analysis."""
    need_clarification: bool = Field(
        description="Whether the user needs to be asked a clarifying question."
    )
    question: str = Field(
        description="A question to ask the user to clarify the research scope."
    )
    verification: str = Field(
        description="Verification that research will begin after user provides needed info."
    )


class ResearchQuestion(BaseModel):
    """Structured research brief from user messages."""
    research_brief: str = Field(
        description="A detailed research question that will guide the entire research process."
    )


class ComplexityAssessment(BaseModel):
    """Structured complexity classification for routing."""
    complexity: Literal["simple", "standard", "deep"] = Field(
        description="Complexity level of the research task."
    )
    estimated_depth: int = Field(
        description="Recommended recursion depth (1=surface, 2=moderate, 3=thorough).",
        ge=1, le=3
    )
    estimated_breadth: int = Field(
        description="Recommended search breadth (number of parallel queries).",
        ge=1, le=6
    )
    reasoning: str = Field(
        description="Brief reasoning for the complexity classification."
    )


class ConductResearch(BaseModel):
    """Delegate a research task to a specialized sub-researcher.

    The supervisor uses this tool to spawn parallel research subgraphs.
    Pattern from open_deep_research.
    """
    research_topic: str = Field(
        description="The topic to research. Must be a single, well-defined topic "
                    "described in detail (at least a paragraph). Include specific "
                    "instructions for the researcher."
    )


class ThinkTool(BaseModel):
    """Enhanced strategic reflection tool.

    Unlike open_deep_research's basic think_tool that just logs thoughts,
    this enhanced version structures thinking into actionable data that
    downstream nodes can consume for automated decision-making.

    Pattern: open_deep_research think_tool + gpt-researcher's structured reflection.
    """
    reflection: str = Field(
        description="Detailed reflection on current research progress and strategy."
    )
    gaps_identified: list[str] = Field(
        description="Specific information gaps that still need to be filled."
    )
    confidence_level: Literal["low", "medium", "high"] = Field(
        description="Current confidence level in research completeness."
    )
    next_strategy: Literal["search_more", "curate", "complete"] = Field(
        description="Recommended next action: search for more info, curate existing "
                    "sources, or complete the research phase."
    )


class SourceCurate(BaseModel):
    """Curate and rank collected sources by quality and relevance.

    From gpt-researcher's SourceCurator pattern.
    """
    max_sources: int = Field(
        default=10,
        description="Maximum number of top sources to retain after curation."
    )


class ResearchComplete(BaseModel):
    """Signal that the research phase is complete.

    Called by supervisor when satisfied with research coverage.
    From open_deep_research.
    """
    summary: str = Field(
        default="",
        description="Optional summary of why research is considered complete."
    )


class ResearchDeep(BaseModel):
    """Initiate deep recursive research using breadth x depth algorithm.

    This triggers gpt-researcher's deterministic deep research pattern
    when the supervisor determines comprehensive coverage is needed.
    Will be implemented in Phase 2.
    """
    research_topic: str = Field(
        description="The research topic for deep recursive exploration."
    )
    breadth: int = Field(
        default=4,
        description="Number of search queries to generate per depth level."
    )
    depth: int = Field(
        default=2,
        description="How many levels deep to recursively research."
    )


# =============================================================================
# State Definitions
# =============================================================================

class AgentInputState(MessagesState):
    """Input state: only messages required from caller."""
    pass


class AgentState(MessagesState):
    """Main agent state for the complete research workflow.

    This is the top-level state that flows through the entire graph:
    Input Gateway → Supervisor Subgraph → Final Report Generation.
    """
    # === User input ===
    input: str
    skill_ids: list[str]
    images: list[dict[str, Any]]  # Base64-encoded images from user input (multimodal)

    # === Input Gateway outputs ===
    research_brief: Optional[str]
    complexity: str  # "simple" | "standard" | "deep"
    estimated_depth: int
    estimated_breadth: int
    needs_clarification: bool

    # === Supervisor state (accumulated across iterations) ===
    supervisor_messages: Annotated[list[MessageLikeRepresentation], override_reducer]
    raw_notes: Annotated[list[str], override_reducer]
    notes: Annotated[list[str], override_reducer]
    research_iterations: int

    # === Report format ===
    report_format: str  # "markdown" (default) or "html"

    # === Collected sources ===
    sources: list[dict[str, str]]
    curated_sources: list[dict[str, Any]]

    # === Final output ===
    final_report: str


class SupervisorState(TypedDict):
    """State for the research supervisor subgraph.

    The supervisor manages research delegation: it decides which topics
    to research, delegates to parallel researcher subgraphs via ConductResearch,
    reflects via think_tool, and signals completion via ResearchComplete.
    """
    supervisor_messages: Annotated[list[MessageLikeRepresentation], override_reducer]
    research_brief: str
    complexity: str
    estimated_depth: int
    estimated_breadth: int
    notes: Annotated[list[str], override_reducer]
    raw_notes: Annotated[list[str], override_reducer]
    research_iterations: int
    deep_research_count: int
    curated_sources: list[dict[str, Any]]


class ResearcherState(TypedDict):
    """State for individual researcher subgraphs.

    Each researcher is spawned by the supervisor with a specific research_topic.
    It uses search tools to gather information, reflects via think_tool,
    and produces compressed research output.
    """
    researcher_messages: Annotated[list[MessageLikeRepresentation], operator.add]
    tool_call_iterations: int
    research_topic: str
    compressed_research: str
    raw_notes: Annotated[list[str], override_reducer]


class ResearcherOutputState(BaseModel):
    """Output state from individual researchers (returned to supervisor)."""
    compressed_research: str
    raw_notes: Annotated[list[str], override_reducer]


# =============================================================================
# State Bridge: Old → New State Adapter
# =============================================================================

def build_initial_state_v2(
    input_text: str = "",
    user_id: str = "",
    images: list[dict[str, Any]] | None = None,
    research_brief: dict[str, Any] | None = None,
    messages: list | None = None,
    **kwargs,
) -> dict:
    """Build an initial v2 AgentState from legacy main.py fields.

    Bridges the old AgentState (40+ fields) into the new streamlined AgentState
    (14 fields). Fields from the old state that don't exist in the new one are
    silently dropped — the v2 graph derives them internally.

    Args:
        input_text: User's query text.
        user_id: User identifier.
        images: Optional images (stored in metadata, not core state).
        research_brief: Pre-existing research brief dict (from store).
        messages: Initial messages (system prompts, memory context, etc.).
        **kwargs: Additional legacy fields (ignored).

    Returns:
        dict ready to be used as initial_state for the v2 graph.
    """
    initial_state: dict[str, Any] = {
        "input": input_text,
        "images": images or [],
        "skill_ids": kwargs.get("skill_ids", []),
        "research_brief": None,
        "complexity": "standard",
        "estimated_depth": 1,
        "estimated_breadth": 2,
        "needs_clarification": False,
        "supervisor_messages": [],
        "raw_notes": [],
        "notes": [],
        "research_iterations": 0,
        "sources": [],
        "curated_sources": [],
        "final_report": "",
        "report_format": kwargs.get("report_format", "markdown"),
        "messages": messages or [],
    }

    # Handle research_brief from store memory (pre-existing brief)
    if isinstance(research_brief, dict) and research_brief:
        brief_text = research_brief.get("research_brief", "")
        if brief_text:
            initial_state["research_brief"] = brief_text

    # Inject user_id into configurable metadata (not state directly)
    if user_id:
        initial_state["_user_id"] = user_id

    return initial_state
