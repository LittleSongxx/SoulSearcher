"""ThreadState for the DeerFlow-style lead agent.

Extends LangChain's AgentState with sandbox, thread data, title,
artifacts, todos, uploaded files, and viewed images.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any

from langchain.agents import AgentState
from langchain_core.messages import AnyMessage


def _last_value(existing: Any, new: Any) -> Any:
    """Reducer: replace with the latest value."""
    return new


def _merge_artifacts(existing: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any]:
    """Deduplicate artifacts by path."""
    merged = dict(existing or {})
    if new:
        merged.update(new)
    return merged


def _merge_viewed_images(existing: list[dict[str, Any]] | None, new: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Merge viewed images list, clearing on explicit None sentinel."""
    if new is None:
        return []
    existing_list = list(existing or [])
    for img in new or []:
        if img not in existing_list:
            existing_list.append(img)
    return existing_list


class ThreadState(AgentState):
    """Weaver thread state with DeerFlow-aligned fields."""

    # ── Sandbox ──
    sandbox: dict[str, Any]
    sandbox_id: str

    # ── Thread metadata ──
    thread_data: dict[str, Any]
    thread_id: str
    title: str

    # ── Artifacts & outputs ──
    artifacts: Annotated[dict[str, Any], _merge_artifacts]

    # ── Todo tracking ──
    todos: list[dict[str, Any]]

    # ── Uploads ──
    uploaded_files: list[dict[str, Any]]

    # ── Images ──
    viewed_images: Annotated[list[dict[str, Any]], _merge_viewed_images]

    # ── User input ──
    input: str
    images: list[dict[str, Any]]

    # ── Final output ──
    final_report: str
    draft_report: str

    # ── Execution control ──
    status: str
    is_complete: bool
    route: str
    routing_reasoning: str
    routing_confidence: float
    suggested_queries: list[str]

    # ── Clarification ──
    needs_clarification: bool
    clarification_question: str

    # ── Research data ──
    research_plan: list[str]
    scraped_content: Annotated[list[dict[str, Any]], operator.add]
    sources: list[dict[str, str]]
    deepsearch_artifacts: dict[str, Any]
    quality_summary: dict[str, Any]
    research_tree: dict[str, Any]
    compressed_knowledge: dict[str, Any]

    # ── Quality control ──
    evaluation: str
    verdict: str
    revision_count: int
    max_revisions: int

    # ── Coordinator ──
    coordinator_action: str
    coordinator_reasoning: str
    coordinator_iterations: int

    # ── Cancellation ──
    cancel_token_id: str | None
    is_cancelled: bool

    # ── Metrics ──
    total_input_tokens: int
    total_output_tokens: int

    # ── Domain ──
    domain: str
    domain_config: dict[str, Any]

    class Config:
        arbitrary_types_allowed = True
