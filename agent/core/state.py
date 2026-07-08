"""State definitions for the fixed-role vertical industry research agent."""

import operator
from typing import Annotated, Any, Literal, Optional

from langchain_core.messages import HumanMessage
from langgraph.graph import MessagesState
from pydantic import BaseModel, Field

from agent.core.artifacts import normalize_deepsearch_artifacts

# =============================================================================
# Reducers
# =============================================================================

def override_reducer(current_value, new_value):
    """Reducer that allows complete override via {'type': 'override', 'value': ...}.

    Used for state fields that need full replacement (not accumulation).
    Used by fields that need full replacement instead of accumulation.
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


class VerticalResearchTask(BaseModel):
    """Structured task contract for a fixed-role vertical research section."""
    agent_role: str = Field(description="Fixed role responsible for this task.")
    section_id: str = Field(description="Stable report section identifier.")
    research_dimension: str = Field(description="industry, company, policy, technology_trend, or mixed.")
    required_evidence_types: list[str] = Field(default_factory=list)
    required_metrics: list[str] = Field(default_factory=list)
    source_priority: list[str] = Field(default_factory=list)
    freshness_requirement: str = ""
    requires_data: bool = False
    requires_chart: bool = False


class ResearchComplete(BaseModel):
    """Signal that the fixed-role research pipeline completed."""
    summary: str = Field(default="", description="Brief completion summary.")


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

    This is the top-level state that flows through the fixed-role vertical graph.
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

    # === Fixed-role vertical research state ===
    vertical_profile: dict[str, Any]
    vertical_brief: dict[str, Any]
    research_tasks: Annotated[list[dict[str, Any]], override_reducer]
    datapoints: Annotated[list[dict[str, Any]], override_reducer]
    claim_checks: Annotated[list[dict[str, Any]], override_reducer]
    critic_feedback: Annotated[list[dict[str, Any]], override_reducer]
    agent_trace: Annotated[list[dict[str, Any]], override_reducer]
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


# =============================================================================
# State Builder
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

    Builds the standard AgentState used by the vertical research graph.

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

    initial_artifacts = normalize_deepsearch_artifacts(
        kwargs.get("initial_deepsearch_artifacts")
    )

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
        "vertical_profile": {},
        "vertical_brief": {},
        "research_tasks": [],
        "datapoints": [],
        "claim_checks": [],
        "critic_feedback": [],
        "agent_trace": [],
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
        "deepsearch_artifacts": initial_artifacts,
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
