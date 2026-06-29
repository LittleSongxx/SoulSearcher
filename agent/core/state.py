"""Unified state definitions for the Deep Research Agent.

Integrates patterns from:
- open_deep_research: override_reducer, SupervisorState/ResearcherState subgraph nesting
- gpt-researcher: depth/breadth tracking, learnings accumulation
- deer-flow: Structured output models for tool calls
"""

import operator
from typing import Annotated, Any, Literal, Optional

from langchain_core.messages import HumanMessage, MessageLikeRepresentation
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
        description="Recommended recursion depth (1=surface, 2=thorough, 3=deep).",
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
    """Delegate a research task to a specialised sub-researcher.

    Use this tool when you need in-depth investigation of a specific topic.
    You can call this tool multiple times in parallel for different topics.
    Each call spawns an independent researcher that searches, reads, and
    synthesises findings via the ReAct loop with mixed compression.

    Research effort levels:
      - "quick"       — fast verification for narrow facts
      - "normal"      — default multi-source synthesis
      - "thorough"    — deeper comparison or evidence-heavy investigation
      - "exhaustive"  — rare, highest-budget coverage for deep tasks

    Legacy values are still accepted by the runtime:
    "medium" → "normal", "deep" → "thorough",
    "very_thorough" → "exhaustive".
    """
    topic: str = Field(
        description="The specific topic to research. Be precise — one well-scoped "
                    "subject per call. For example: 'safety record of mRNA vaccines "
                    "in elderly populations' rather than 'vaccines'."
    )
    context: str = Field(
        default="",
        description="Brief context to help the researcher: what is already known, "
                    "what specific angles matter, or what type of sources to prefer."
    )
    research_effort: Literal["quick", "normal", "thorough", "exhaustive"] = Field(
        default="normal",
        description="How much budget to spend on this sub-research task."
    )
    thoroughness: Literal[
        "quick",
        "medium",
        "deep",
        "very_thorough",
        "normal",
        "thorough",
        "exhaustive",
    ] | None = Field(
        default=None,
        description=(
            "Deprecated alias for research_effort. Prefer research_effort. "
            "Accepted for backward compatibility."
        )
    )


class ThinkTool(BaseModel):
    """Pause and reflect on research progress before deciding next steps.

    Use this tool when you need to step back and assess whether the research
    has covered enough ground.  The structured reflection fields help you
    identify gaps and choose the right next action.
    """
    reflection: str = Field(
        description="What have we learned so far?  What patterns emerged across "
                    "the collected sources?  Are there contradictions to resolve?"
    )
    gaps_identified: list[str] = Field(
        description="Specific information gaps that still need to be filled. "
                    "Be concrete: name the missing data point, comparison, or angle."
    )
    confidence_level: Literal["low", "medium", "high"] = Field(
        description="How confident are you that the collected evidence fully "
                    "answers the research brief?"
    )
    next_strategy: Literal["search_more", "curate", "complete"] = Field(
        description="What to do next: search for missing information, curate "
                    "and rank the collected sources, or conclude the research phase."
    )


class SourceCurate(BaseModel):
    """Rank and filter the collected sources by quality and relevance.

    Call this when enough raw research has been gathered and you need to
    select the best sources for the final report.
    """
    max_sources: int = Field(
        default=10,
        description="Maximum number of top sources to retain after curation."
    )


class ResearchComplete(BaseModel):
    """Signal that the research phase is complete and findings are ready for
    final report generation.  Only call this when you are confident that the
    collected evidence sufficiently addresses the research brief.
    """
    summary: str = Field(
        default="",
        description="Brief summary of what was covered and why the research "
                    "is considered complete."
    )


class EvidenceItem(BaseModel):
    """Structured source evidence carried through research, report, and eval."""
    id: str = Field(description="Stable evidence identifier within the run.")
    type: str = Field(default="source_text", description="Evidence kind.")
    source_id: str = Field(default="", description="Optional normalized source id.")
    title: str = Field(default="", description="Source or passage title.")
    url: str = Field(default="", description="Source URL when available.")
    source: str = Field(default="", description="Human-readable source reference.")
    content: str = Field(default="", description="Evidence passage or source excerpt.")
    tool: str = Field(default="", description="Tool or stage that produced the evidence.")
    query: str = Field(default="", description="Query or research task that found it.")
    retrieved_at: str = Field(default="", description="ISO timestamp for retrieval.")
    score: float | None = Field(default=None, description="Optional relevance score.")
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_artifact(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


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
    retrieval_policy: dict[str, Any]

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
    evidence_items: Annotated[list[dict[str, Any]], override_reducer]
    plan_graph: Annotated[dict[str, Any], override_reducer]
    plan_events: Annotated[list[dict[str, Any]], override_reducer]
    plan_version: int
    research_plan: Annotated[list[str], override_reducer]
    research_todos: Annotated[list[dict[str, Any]], override_reducer]
    todo_summary: dict[str, Any]

    quality_summary: dict[str, Any]
    quality_gates: list[dict[str, Any]]
    quality_followup_required: bool
    quality_followup_count: int
    deepsearch_artifacts: dict[str, Any]

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
    evidence_items: Annotated[list[dict[str, Any]], override_reducer]
    plan_graph: Annotated[dict[str, Any], override_reducer]
    plan_events: Annotated[list[dict[str, Any]], override_reducer]
    plan_version: int
    research_plan: Annotated[list[str], override_reducer]
    research_todos: Annotated[list[dict[str, Any]], override_reducer]
    todo_summary: dict[str, Any]
    research_iterations: int
    curated_sources: list[dict[str, Any]]
    retrieval_policy: dict[str, Any]


class ResearcherState(TypedDict):
    """State for individual researcher subgraphs.

    Each researcher is spawned by the supervisor with a specific research_topic.
    It uses search tools to gather information, reflects via think_tool,
    and produces compressed research output.

    ``research_effort`` controls the sub-agent budget:
    - "quick" → fast verification
    - "normal" → balanced investigation (default)
    - "thorough" → broader multi-source investigation
    - "exhaustive" → highest-budget research for deep tasks
    """
    researcher_messages: Annotated[list[MessageLikeRepresentation], operator.add]
    tool_call_iterations: int
    research_topic: str
    research_effort: str  # "quick" | "normal" | "thorough" | "exhaustive"
    thoroughness: str  # Deprecated alias retained for state compatibility.
    research_depth: int
    research_breadth: int
    compressed_research: str
    raw_notes: Annotated[list[str], override_reducer]
    evidence_items: Annotated[list[dict[str, Any]], override_reducer]
    retrieval_policy: dict[str, Any]


class ResearcherOutputState(BaseModel):
    """Output state from individual researchers (returned to supervisor)."""
    compressed_research: str
    raw_notes: Annotated[list[str], override_reducer]
    evidence_items: list[dict[str, Any]] = Field(default_factory=list)


# =============================================================================
# State Bridge: Old → New State Adapter
# =============================================================================

def ensure_user_input_message(messages: list | None, input_text: str = "") -> list:
    """Return messages with the current user query present as a HumanMessage."""
    result = list(messages or [])
    query = str(input_text or "").strip()
    if not query:
        return result

    for message in result:
        if isinstance(message, HumanMessage) and str(message.content).strip() == query:
            return result
        if isinstance(message, dict):
            role = str(message.get("role") or message.get("type") or "").lower()
            content = str(message.get("content") or "").strip()
            if role in {"human", "user"} and content == query:
                return result

    result.append(HumanMessage(content=query))
    return result


def build_initial_state(
    input_text: str = "",
    user_id: str = "",
    images: list[dict[str, Any]] | None = None,
    research_brief: dict[str, Any] | None = None,
    messages: list | None = None,
    **kwargs,
) -> dict:
    """Build an initial AgentState from main.py fields.

    Bridges request-builder fields into the standard AgentState.

    Args:
        input_text: User's query text.
        user_id: User identifier.
        images: Optional images (stored in metadata, not core state).
        research_brief: Pre-existing research brief dict (from store).
        messages: Initial messages (system prompts, memory context, etc.).
        **kwargs: Additional caller fields (ignored).

    Returns:
        dict ready to be used as initial_state for the research graph.
    """
    raw_skill_ids = kwargs.get("skill_ids", [])
    if isinstance(raw_skill_ids, str):
        skill_ids = [part.strip() for part in raw_skill_ids.split(",") if part.strip()]
    elif isinstance(raw_skill_ids, list):
        skill_ids = [str(part).strip() for part in raw_skill_ids if str(part).strip()]
    else:
        skill_ids = []

    retrieval_policy = kwargs.get("retrieval_policy")
    if not isinstance(retrieval_policy, dict):
        retrieval_policy = {}

    initial_sources = kwargs.get("initial_sources")
    if not isinstance(initial_sources, list):
        initial_sources = []
    initial_sources = [item for item in initial_sources if isinstance(item, dict)]

    initial_artifacts = kwargs.get("initial_deepsearch_artifacts")
    if not isinstance(initial_artifacts, dict):
        initial_artifacts = {}

    initial_state: dict[str, Any] = {
        "input": input_text,
        "images": images or [],
        "skill_ids": skill_ids,
        "retrieval_policy": retrieval_policy,
        "research_brief": None,
        "complexity": "standard",
        "estimated_depth": 1,
        "estimated_breadth": 2,
        "needs_clarification": False,
        "supervisor_messages": [],
        "raw_notes": [],
        "notes": [],
        "research_iterations": 0,
        "sources": initial_sources,
        "curated_sources": [],
        "evidence_items": [],
        "plan_graph": {},
        "plan_events": [],
        "plan_version": 1,
        "research_plan": [],
        "research_todos": [],
        "todo_summary": {},
        "quality_summary": {},
        "quality_gates": [],
        "quality_followup_required": False,
        "quality_followup_count": 0,
        "deepsearch_artifacts": dict(initial_artifacts),
        "final_report": "",
        "report_format": kwargs.get("report_format", "markdown"),
        "messages": ensure_user_input_message(messages, input_text),
    }

    # Handle research_brief from store memory (pre-existing brief)
    if isinstance(research_brief, dict) and research_brief:
        brief_text = research_brief.get("research_brief", "")
        if brief_text:
            initial_state["research_brief"] = brief_text
        brief_retrieval_policy = research_brief.get("retrieval_policy")
        if isinstance(brief_retrieval_policy, dict) and not initial_state["retrieval_policy"]:
            initial_state["retrieval_policy"] = brief_retrieval_policy

    # Inject user_id into configurable metadata (not state directly)
    if user_id:
        initial_state["_user_id"] = user_id

    return initial_state
