from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from common.evidence_store import build_evidence_store_snapshot


@dataclass(frozen=True, slots=True)
class SessionsRouterDeps:
    checkpointer: Any
    settings: Any
    logger: Any
    require_thread_owner: Callable[[Request, str], None]
    get_emitter: Callable[[str], Any]
    tool_event: Any


# ==================== Sessions API ====================


class SessionSummary(BaseModel):
    thread_id: str
    status: str
    topic: str
    created_at: str
    updated_at: str
    route: str
    has_report: bool
    revision_count: int
    message_count: int
    owner_id: str = ""
    group_id: str = ""
    visibility: str = "private"


class SessionsListResponse(BaseModel):
    count: int
    sessions: list[SessionSummary]


class EvidenceSource(BaseModel):
    title: str = ""
    url: str
    rawUrl: Optional[str] = None
    domain: Optional[str] = None
    provider: Optional[str] = None
    publishedDate: Optional[str] = None


class EvidenceClaimEvidence(BaseModel):
    url: str
    snippet_hash: Optional[str] = None
    quote: Optional[str] = None
    heading_path: Optional[list[str]] = None


class EvidenceClaim(BaseModel):
    claim: str
    status: str
    evidence_urls: list[str] = []
    evidence_passages: list[EvidenceClaimEvidence] = []
    score: float = 0.0
    notes: str = ""


class FetchedPageItem(BaseModel):
    url: str
    raw_url: str
    method: str
    text: Optional[str] = None
    title: Optional[str] = None
    published_date: Optional[str] = None
    retrieved_at: Optional[str] = None
    markdown: Optional[str] = None
    http_status: Optional[int] = None
    error: Optional[str] = None
    attempts: int = 1


class EvidencePassageItem(BaseModel):
    url: str
    text: str
    start_char: int
    end_char: int
    heading: Optional[str] = None
    heading_path: Optional[list[str]] = None
    page_title: Optional[str] = None
    retrieved_at: Optional[str] = None
    method: Optional[str] = None
    quote: Optional[str] = None
    snippet_hash: Optional[str] = None


class EvidenceItemResponse(BaseModel):
    id: str
    source_type: str
    provider: Optional[str] = None
    url: Optional[str] = None
    document_id: Optional[str] = None
    title: Optional[str] = None
    snippet: Optional[str] = None
    content_ref: Optional[str] = None
    published_date: Optional[str] = None
    retrieved_at: Optional[str] = None
    query: Optional[str] = None
    quality_score: Optional[float] = None
    freshness_score: Optional[float] = None
    citation_id: Optional[str] = None
    metadata: dict[str, Any] = {}


class CitationAnnotationResponse(BaseModel):
    id: str
    citation_id: str
    marker: str
    source_index: Optional[int] = None
    start_char: int
    end_char: int
    section: str = ""
    title: Optional[str] = None
    url: Optional[str] = None
    rawUrl: Optional[str] = None
    domain: Optional[str] = None
    provider: Optional[str] = None
    publishedDate: Optional[str] = None
    evidence_ids: list[str] = []
    occurrence: int = 0


class TimelineEventResponse(BaseModel):
    id: str
    order: int
    event_type: str
    title: str
    timestamp: Optional[str] = None
    query: Optional[str] = None
    result_count: Optional[int] = None
    providers: list[str] = []
    run_index: Optional[int] = None
    url: Optional[str] = None
    rawUrl: Optional[str] = None
    provider: Optional[str] = None
    publishedDate: Optional[str] = None
    citation_id: Optional[str] = None
    source_index: Optional[int] = None
    evidence_id: Optional[str] = None
    source_type: Optional[str] = None
    document_id: Optional[str] = None
    stage: Optional[str] = None
    epoch: Optional[int] = None
    gate_count: Optional[int] = None
    failed_count: Optional[int] = None


class SupervisorDecisionResponse(BaseModel):
    round_index: int
    action: str
    reason: str = ""
    missing_topics: list[str] = []
    next_worker_topics: list[str] = []
    failed_gates: list[str] = []
    quality_snapshot: dict[str, Any] = {}


class WorkerRunResponse(BaseModel):
    worker_id: str
    context_id: str
    topic: str
    focus: str = ""
    queries: list[str] = []
    round_index: int = 0
    result_count: int = 0
    evidence_count: int = 0
    summary: str = ""
    provider_breakdown: dict[str, int] = {}
    status: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    errors: list[str] = []


class IntermediateStepResponse(BaseModel):
    id: str
    order: int
    type: str
    title: Optional[str] = None
    status: Optional[str] = None
    worker_id: Optional[str] = None
    context_id: Optional[str] = None
    round_index: Optional[int] = None
    result_count: Optional[int] = None
    evidence_count: Optional[int] = None
    timestamp: Optional[str] = None
    reason: Optional[str] = None
    missing_topics: list[str] = []


class EvidenceResponse(BaseModel):
    sources: list[EvidenceSource] = []
    claims: list[EvidenceClaim] = []
    quality_summary: dict[str, Any] = {}
    quality_details: dict[str, Any] = {}
    research_brief: dict[str, Any] = {}
    plan_graph: dict[str, Any] = {}
    plan_events: list[dict[str, Any]] = []
    plan_summary: dict[str, Any] = {}
    research_todos: list[dict[str, Any]] = []
    todo_summary: dict[str, Any] = {}
    retrieval_policy: dict[str, Any] = {}
    evidence_store: dict[str, Any] = {}
    access_policy: dict[str, Any] = {}
    quality_gates: list[dict[str, Any]] = []
    evidence_items: list[EvidenceItemResponse] = []
    citation_annotations: list[CitationAnnotationResponse] = []
    timeline: list[TimelineEventResponse] = []
    supervisor_decisions: list[SupervisorDecisionResponse] = []
    worker_runs: list[WorkerRunResponse] = []
    intermediate_steps: list[IntermediateStepResponse] = []
    continue_requests: list[dict[str, Any]] = []
    fetched_pages: list[FetchedPageItem] = []
    passages: list[EvidencePassageItem] = []
    research_pipeline: dict[str, Any] = {}
    stage_runtime: dict[str, Any] = {}
    source_quality: dict[str, Any] = {}
    reader_plan: dict[str, Any] = {}
    worker_orchestration: dict[str, Any] = {}
    branch_diagnostics: dict[str, Any] = {}
    brief_review: dict[str, Any] = {}
    fallback: dict[str, Any] = {}



class SessionResumeRequest(BaseModel):
    """Request to resume a session."""

    additional_input: Optional[str] = None
    update_state: Optional[dict[str, Any]] = None


class ContinueResearchRequest(BaseModel):
    target_type: str = Field(..., description="section | claim | source | gap")
    target_id: Optional[str] = None
    target_index: Optional[int] = None
    target_text: Optional[str] = None
    instruction: Optional[str] = None


class ContinueResearchResponse(BaseModel):
    success: bool
    thread_id: str
    status: str
    continue_request: dict[str, Any]
    resume_input: str
    update_state: dict[str, Any]
    stream_payload: dict[str, Any]
    resume_state: dict[str, Any]



def build_sessions_router(deps: SessionsRouterDeps) -> APIRouter:
    router = APIRouter(tags=["sessions"])

    @router.get("/api/sessions", response_model=SessionsListResponse)
    async def list_sessions(
        request: Request,
        limit: int = 50,
        status: Optional[str] = None,
    ):
        """
        List all research sessions.

        Args:
            limit: Maximum sessions to return
            status: Filter by status (pending, running, completed, cancelled)
        """
        if not deps.checkpointer:
            raise HTTPException(status_code=400, detail="No checkpointer configured")

        try:
            from common.session_manager import get_session_manager

            manager = get_session_manager(deps.checkpointer)
            internal_key = (getattr(deps.settings, "internal_api_key", "") or "").strip()
            user_filter = None
            if internal_key:
                user_filter = (
                    getattr(request.state, "principal_id", "") or ""
                ).strip() or "internal"
            sessions = manager.list_sessions(
                limit=limit, status_filter=status, user_id_filter=user_filter
            )

            return {
                "count": len(sessions),
                "sessions": [s.to_dict() for s in sessions],
            }

        except Exception as e:
            deps.logger.error(f"List sessions error: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))


    @router.get("/api/sessions/{thread_id}")
    async def get_session(thread_id: str, request: Request):
        """
        Get session info by thread ID.
        """
        if not deps.checkpointer:
            raise HTTPException(status_code=400, detail="No checkpointer configured")

        try:
            from common.session_manager import get_session_manager

            manager = get_session_manager(deps.checkpointer)

            internal_key = (getattr(deps.settings, "internal_api_key", "") or "").strip()
            if internal_key:
                principal_id = (getattr(request.state, "principal_id", "") or "").strip()
                session_state = manager.get_session_state(thread_id)
                if session_state and isinstance(session_state.state, dict):
                    owner = session_state.state.get("user_id")
                    if (
                        isinstance(owner, str)
                        and owner.strip()
                        and owner.strip() != principal_id
                    ):
                        raise HTTPException(status_code=403, detail="Forbidden")

            session = manager.get_session(thread_id)

            if not session:
                raise HTTPException(
                    status_code=404, detail=f"Session not found: {thread_id}"
                )

            return session.to_dict()

        except HTTPException:
            raise
        except Exception as e:
            deps.logger.error(f"Get session error: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))


    @router.get("/api/sessions/{thread_id}/state")
    async def get_session_state(thread_id: str, request: Request):
        """
        Get full session state snapshot.
        """
        if not deps.checkpointer:
            raise HTTPException(status_code=400, detail="No checkpointer configured")

        try:
            from common.session_manager import get_session_manager

            manager = get_session_manager(deps.checkpointer)
            state = manager.get_session_state(thread_id)

            if not state:
                raise HTTPException(
                    status_code=404, detail=f"Session not found: {thread_id}"
                )

            internal_key = (getattr(deps.settings, "internal_api_key", "") or "").strip()
            if internal_key:
                principal_id = (getattr(request.state, "principal_id", "") or "").strip()
                owner = (
                    state.state.get("user_id") if isinstance(state.state, dict) else None
                )
                if (
                    isinstance(owner, str)
                    and owner.strip()
                    and owner.strip() != principal_id
                ):
                    raise HTTPException(status_code=403, detail="Forbidden")

            return state.to_dict()

        except HTTPException:
            raise
        except Exception as e:
            deps.logger.error(f"Get session state error: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))


    @router.get("/api/sessions/{thread_id}/evidence", response_model=EvidenceResponse)
    async def get_session_evidence(thread_id: str, request: Request):
        """
        Get evidence artifacts (sources + claims + quality summary) for a session.
        """
        if not deps.checkpointer:
            raise HTTPException(status_code=400, detail="No checkpointer configured")

        try:
            from common.session_manager import get_session_manager

            manager = get_session_manager(deps.checkpointer)
            session_state = manager.get_session_state(thread_id)
            if not session_state:
                raise HTTPException(
                    status_code=404, detail=f"Session not found: {thread_id}"
                )

            internal_key = (getattr(deps.settings, "internal_api_key", "") or "").strip()
            if internal_key:
                principal_id = (getattr(request.state, "principal_id", "") or "").strip()
                owner = (
                    session_state.state.get("user_id")
                    if isinstance(session_state.state, dict)
                    else None
                )
                if (
                    isinstance(owner, str)
                    and owner.strip()
                    and owner.strip() != principal_id
                ):
                    raise HTTPException(status_code=403, detail="Forbidden")

            artifacts = session_state.deepsearch_artifacts or {}
            if not isinstance(artifacts, dict):
                artifacts = {}

            sources = artifacts.get("sources", [])
            claims = artifacts.get("claims", [])
            quality_summary = artifacts.get("quality_summary", {})
            quality_details = artifacts.get("quality_details", {})
            research_brief = artifacts.get("research_brief", {})
            plan_graph = artifacts.get("plan_graph", {})
            plan_graph_events = (
                plan_graph.get("events") if isinstance(plan_graph, dict) else None
            )
            plan_events = (
                plan_graph_events
                if isinstance(plan_graph_events, list)
                else artifacts.get("plan_events", [])
            )
            plan_artifact_summary = artifacts.get("plan_summary", {})
            plan_graph_summary = (
                plan_graph.get("summary") if isinstance(plan_graph, dict) else None
            )
            plan_summary = (
                plan_graph_summary
                if isinstance(plan_graph_summary, dict)
                else plan_artifact_summary
                if isinstance(plan_artifact_summary, dict)
                else {}
            )
            research_todos = artifacts.get("research_todos", [])
            todo_summary = artifacts.get("todo_summary", {})
            quality_gates = artifacts.get("quality_gates", [])
            evidence_items = artifacts.get("evidence_items", [])
            citation_annotations = artifacts.get("citation_annotations", [])
            timeline = artifacts.get("timeline", [])
            supervisor_decisions = artifacts.get("supervisor_decisions", [])
            worker_runs = artifacts.get("worker_runs", [])
            intermediate_steps = artifacts.get("intermediate_steps", [])
            continue_requests = artifacts.get("continue_requests", [])
            fetched_pages = artifacts.get("fetched_pages", [])
            passages = artifacts.get("passages", [])
            research_pipeline = artifacts.get("research_pipeline", {})
            stage_runtime = artifacts.get("stage_runtime", {})
            source_quality = artifacts.get("source_quality", {})
            reader_plan = artifacts.get("reader_plan", {})
            worker_orchestration = artifacts.get("worker_orchestration", {})
            branch_diagnostics = artifacts.get("branch_diagnostics", {})
            brief_review = artifacts.get("brief_review", {})
            fallback = artifacts.get("fallback", {})
            evidence_store = build_evidence_store_snapshot(
                thread_id=thread_id,
                artifacts=artifacts,
                state=session_state.state if isinstance(session_state.state, dict) else {},
            )
            evidence_patch = evidence_store.to_response_patch()
            retrieval_policy = (
                artifacts.get("retrieval_policy")
                or evidence_patch.get("retrieval_policy")
                or {}
            )
            access_policy = (
                artifacts.get("access_policy") or evidence_patch.get("access_policy") or {}
            )

            return {
                "sources": (
                    sources
                    if isinstance(sources, list)
                    else evidence_patch.get("sources", [])
                ),
                "claims": (
                    claims if isinstance(claims, list) else evidence_patch.get("claims", [])
                ),
                "quality_summary": (
                    quality_summary if isinstance(quality_summary, dict) else {}
                ),
                "quality_details": (
                    quality_details if isinstance(quality_details, dict) else {}
                ),
                "research_brief": (
                    research_brief if isinstance(research_brief, dict) else {}
                ),
                "plan_graph": (
                    plan_graph
                    if isinstance(plan_graph, dict)
                    else evidence_patch.get("plan_graph", {})
                ),
                "plan_events": (
                    plan_events
                    if isinstance(plan_events, list)
                    else evidence_patch.get("plan_events", [])
                ),
                "plan_summary": (
                    plan_summary
                    if isinstance(plan_summary, dict)
                    else evidence_patch.get("plan_summary", {})
                ),
                "research_todos": (
                    research_todos
                    if isinstance(research_todos, list)
                    else evidence_patch.get("research_todos", [])
                ),
                "todo_summary": (
                    todo_summary
                    if isinstance(todo_summary, dict)
                    else evidence_patch.get("todo_summary", {})
                ),
                "retrieval_policy": (
                    retrieval_policy if isinstance(retrieval_policy, dict) else {}
                ),
                "evidence_store": evidence_store.to_dict(),
                "access_policy": access_policy if isinstance(access_policy, dict) else {},
                "quality_gates": (
                    quality_gates
                    if isinstance(quality_gates, list)
                    else evidence_patch.get("quality_gates", [])
                ),
                "evidence_items": (
                    evidence_items
                    if isinstance(evidence_items, list)
                    else evidence_patch.get("evidence_items", [])
                ),
                "citation_annotations": (
                    citation_annotations
                    if isinstance(citation_annotations, list)
                    else evidence_patch.get("citation_annotations", [])
                ),
                "timeline": timeline if isinstance(timeline, list) else [],
                "supervisor_decisions": (
                    supervisor_decisions if isinstance(supervisor_decisions, list) else []
                ),
                "worker_runs": worker_runs if isinstance(worker_runs, list) else [],
                "intermediate_steps": (
                    intermediate_steps if isinstance(intermediate_steps, list) else []
                ),
                "continue_requests": (
                    continue_requests if isinstance(continue_requests, list) else []
                ),
                "fetched_pages": (
                    fetched_pages
                    if isinstance(fetched_pages, list)
                    else evidence_patch.get("fetched_pages", [])
                ),
                "passages": (
                    passages
                    if isinstance(passages, list)
                    else evidence_patch.get("passages", [])
                ),
                "research_pipeline": (
                    research_pipeline if isinstance(research_pipeline, dict) else {}
                ),
                "stage_runtime": stage_runtime if isinstance(stage_runtime, dict) else {},
                "source_quality": (
                    source_quality if isinstance(source_quality, dict) else {}
                ),
                "reader_plan": reader_plan if isinstance(reader_plan, dict) else {},
                "worker_orchestration": (
                    worker_orchestration if isinstance(worker_orchestration, dict) else {}
                ),
                "branch_diagnostics": (
                    branch_diagnostics if isinstance(branch_diagnostics, dict) else {}
                ),
                "brief_review": brief_review if isinstance(brief_review, dict) else {},
                "fallback": fallback if isinstance(fallback, dict) else {},
            }

        except HTTPException:
            raise
        except Exception as e:
            deps.logger.error(f"Get session evidence error: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))


    @router.post(
        "/api/sessions/{thread_id}/continue-research",
        response_model=ContinueResearchResponse,
    )
    async def continue_research_session(
        thread_id: str,
        request: Request,
        payload: ContinueResearchRequest,
    ):
        if not deps.checkpointer:
            raise HTTPException(status_code=400, detail="No checkpointer configured")

        try:
            from agent.workflows.interactive_continue import build_continue_research_plan
            from common.session_manager import get_session_manager

            deps.require_thread_owner(request, thread_id)

            manager = get_session_manager(deps.checkpointer)
            session_state = manager.get_session_state(thread_id)
            if not session_state:
                raise HTTPException(
                    status_code=404, detail=f"Session not found: {thread_id}"
                )

            artifacts = dict(session_state.deepsearch_artifacts or {})
            plan = build_continue_research_plan(
                artifacts=artifacts,
                target_type=payload.target_type,
                target_id=payload.target_id or "",
                target_index=payload.target_index,
                target_text=payload.target_text or "",
                instruction=payload.instruction or "",
            )
            plan_payload = plan.to_dict()
            continue_requests = artifacts.get("continue_requests", [])
            if not isinstance(continue_requests, list):
                continue_requests = []
            continue_requests.append(plan_payload)
            artifacts["continue_requests"] = continue_requests
            update_state = dict(plan.update_state)
            if isinstance(update_state.get("plan_graph"), dict):
                artifacts["plan_graph"] = update_state["plan_graph"]
                artifacts["plan_events"] = update_state.get("plan_events", [])
                try:
                    from agent.workflows.plan_graph import summarize_plan_graph

                    artifacts["plan_summary"] = summarize_plan_graph(
                        update_state["plan_graph"]
                    )
                except Exception:
                    artifacts["plan_summary"] = {}
            if isinstance(update_state.get("research_todos"), list):
                artifacts["research_todos"] = update_state["research_todos"]
            if isinstance(update_state.get("todo_summary"), dict):
                artifacts["todo_summary"] = update_state["todo_summary"]
            update_state["deepsearch_artifacts"] = artifacts
            if isinstance(update_state.get("plan_graph"), dict):
                try:
                    emitter = await deps.get_emitter(thread_id)
                    payload_data = {
                        "reason": "interactive continue research target",
                        "plan_graph": update_state["plan_graph"],
                        "plan_summary": artifacts.get("plan_summary", {}),
                    }
                    await emitter.emit(deps.tool_event.REPLAN_APPLIED, payload_data)
                    await emitter.emit(deps.tool_event.PLAN_GRAPH_UPDATE, payload_data)
                except Exception:
                    pass
            restored_state = manager.build_resume_state(
                thread_id=thread_id,
                additional_input=plan.resume_input,
                update_state=update_state,
            )
            if restored_state is None:
                raise HTTPException(
                    status_code=404, detail=f"Session not found: {thread_id}"
                )

            stream_payload = {
                "messages": [{"role": "user", "content": plan.resume_input}],
                "stream": True,
                "search_mode": {
                    "useWebSearch": True,
                    "useAgent": True,
                    "useDeepSearch": True,
                },
                "thread_id": thread_id,
            }

            return {
                "success": True,
                "thread_id": thread_id,
                "status": "ready_to_continue",
                "continue_request": plan_payload,
                "resume_input": plan.resume_input,
                "update_state": update_state,
                "stream_payload": stream_payload,
                "resume_state": {
                    "route": restored_state.get("route"),
                    "research_plan_count": len(restored_state.get("research_plan") or []),
                    "has_deepsearch_artifacts": bool(
                        restored_state.get("deepsearch_artifacts")
                    ),
                    "resumed_from_checkpoint": bool(
                        restored_state.get("resumed_from_checkpoint")
                    ),
                },
            }
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except HTTPException:
            raise
        except Exception as e:
            deps.logger.error(f"Continue research error: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))


    @router.post("/api/sessions/{thread_id}/resume")
    async def resume_session(
        thread_id: str,
        request: Request,
        payload: SessionResumeRequest | None = None,
    ):
        """
        Resume a paused or cancelled research session.
        """
        if not deps.checkpointer:
            raise HTTPException(status_code=400, detail="No checkpointer configured")

        try:
            from common.session_manager import get_session_manager

            deps.require_thread_owner(request, thread_id)

            manager = get_session_manager(deps.checkpointer)

            # Check if session can be resumed
            can_resume, reason = manager.can_resume(thread_id)
            if not can_resume:
                raise HTTPException(status_code=400, detail=reason)

            # Get current state
            state = manager.get_session_state(thread_id)
            if not state:
                raise HTTPException(
                    status_code=404, detail=f"Session not found: {thread_id}"
                )

            restored_state = manager.build_resume_state(
                thread_id=thread_id,
                additional_input=payload.additional_input if payload else None,
                update_state=payload.update_state if payload else None,
            )
            if restored_state is None:
                raise HTTPException(
                    status_code=404, detail=f"Session not found: {thread_id}"
                )

            deepsearch_artifacts = restored_state.get("deepsearch_artifacts", {}) or {}
            quality_summary = (
                deepsearch_artifacts.get("quality_summary", {})
                if isinstance(deepsearch_artifacts, dict)
                else {}
            )
            queries = (
                deepsearch_artifacts.get("queries", [])
                if isinstance(deepsearch_artifacts, dict)
                else []
            )
            query_coverage = (
                deepsearch_artifacts.get("query_coverage", {})
                if isinstance(deepsearch_artifacts, dict)
                else {}
            )
            freshness_summary = (
                deepsearch_artifacts.get("freshness_summary", {})
                if isinstance(deepsearch_artifacts, dict)
                else {}
            )
            if not isinstance(query_coverage, dict):
                query_coverage = {}
            if not isinstance(freshness_summary, dict):
                freshness_summary = {}
            query_coverage_score = query_coverage.get("score")
            if query_coverage_score is not None:
                try:
                    query_coverage_score = float(query_coverage_score)
                except (TypeError, ValueError):
                    query_coverage_score = None
            if query_coverage_score is None and isinstance(quality_summary, dict):
                nested_coverage = quality_summary.get("query_coverage")
                if isinstance(nested_coverage, dict):
                    query_coverage_score = nested_coverage.get("score")
                if query_coverage_score is None:
                    query_coverage_score = quality_summary.get("query_coverage_score")
                if query_coverage_score is not None:
                    try:
                        query_coverage_score = float(query_coverage_score)
                    except (TypeError, ValueError):
                        query_coverage_score = None
            freshness_warning = ""
            if isinstance(quality_summary, dict):
                freshness_warning = str(quality_summary.get("freshness_warning") or "")

            # Resume the graph execution
            # Note: Actual resumption depends on the graph implementation
            # This returns info for the client to continue via SSE
            return {
                "success": True,
                "thread_id": thread_id,
                "status": "ready_to_resume",
                "message": (
                    f"Session {thread_id} is ready to resume. "
                    "Use the streaming endpoint with this thread_id."
                ),
                "current_state": {
                    "route": state.state.get("route"),
                    "revision_count": state.state.get("revision_count", 0),
                    "has_report": bool(state.state.get("final_report")),
                    "has_deepsearch_artifacts": bool(deepsearch_artifacts),
                    "deepsearch_queries": (
                        len(queries) if isinstance(queries, list) else 0
                    ),
                },
                "deepsearch_resume": {
                    "artifacts_restored": bool(deepsearch_artifacts),
                    "mode": (
                        deepsearch_artifacts.get("mode")
                        if isinstance(deepsearch_artifacts, dict)
                        else None
                    ),
                    "quality_summary": (
                        quality_summary if isinstance(quality_summary, dict) else {}
                    ),
                    "query_coverage_score": query_coverage_score,
                    "freshness_warning": freshness_warning,
                    "freshness_summary": freshness_summary,
                },
                "resume_state": {
                    "route": restored_state.get("route"),
                    "revision_count": restored_state.get("revision_count", 0),
                    "research_plan_count": len(
                        restored_state.get("research_plan", []) or []
                    ),
                    "resumed_from_checkpoint": bool(
                        restored_state.get("resumed_from_checkpoint")
                    ),
                },
            }

        except HTTPException:
            raise
        except Exception as e:
            deps.logger.error(f"Resume session error: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))


    @router.delete("/api/sessions/{thread_id}")
    async def delete_session(thread_id: str, request: Request):
        """
        Delete a research session.
        """
        if not deps.checkpointer:
            raise HTTPException(status_code=400, detail="No checkpointer configured")

        try:
            from common.session_manager import get_session_manager

            deps.require_thread_owner(request, thread_id)

            manager = get_session_manager(deps.checkpointer)
            success = manager.delete_session(thread_id)

            if not success:
                raise HTTPException(
                    status_code=400, detail=f"Failed to delete session: {thread_id}"
                )

            return {
                "success": True,
                "message": f"Session {thread_id} deleted",
            }

        except HTTPException:
            raise
        except Exception as e:
            deps.logger.error(f"Delete session error: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))


    return router
