import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from agent.api.schemas import ImagePayload, ResearchRequest
from agent.retrieval.policy import (
    LegacySourceRoutingError,
    build_retrieval_policy,
    reject_legacy_source_routing,
)
from agent.runtime.background_runs import BackgroundRunRequest
from agent.runtime.idempotency import (
    IdempotencyConflictError,
    canonical_request_hash,
    idempotency_store,
)
from agent.runtime.runs import RunStatus
from common.sse import format_sse_event
from common.thread_ownership import get_thread_owner, set_thread_owner


class BackgroundRunSubmitRequest(ResearchRequest):
    thread_id: Optional[str] = None
    webhook_url: Optional[str] = None


class CancelRequest(BaseModel):
    reason: Optional[str] = "User requested cancellation"


class UserSourceInjectionRequest(BaseModel):
    sources: list[dict[str, Any]] = Field(default_factory=list)
    note: str = ""


class RunEvidenceSummary(BaseModel):
    sources_count: int
    unsupported_claims_count: int
    freshness_ratio_30d: Optional[float] = None
    citation_coverage: Optional[float] = None
    query_coverage_score: Optional[float] = None
    freshness_warning: Optional[str] = None
    claim_verifier_total: Optional[int] = None
    claim_verifier_verified: Optional[int] = None
    claim_verifier_unsupported: Optional[int] = None
    claim_verifier_contradicted: Optional[int] = None


class RunMetricsResponse(BaseModel):
    run_id: str
    model: str
    route: str = ""
    started_at: str
    ended_at: Optional[str] = None
    duration_ms: float
    event_count: int
    nodes_started: dict[str, int]
    nodes_completed: dict[str, int]
    errors: list[str]
    cancelled: bool
    evidence_summary: RunEvidenceSummary
    status: Optional[str] = None
    token_summary: dict[str, Any] = Field(default_factory=dict)
    quality_summary: dict[str, Any] = Field(default_factory=dict)


class RunEventResponse(BaseModel):
    run_id: str
    thread_id: str
    seq: int
    type: str
    status: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""


class RunEventsResponse(BaseModel):
    thread_id: str
    after_seq: int = 0
    count: int = 0
    events: list[RunEventResponse] = Field(default_factory=list)


@dataclass(frozen=True)
class RunsRouterDeps:
    settings: Any
    checkpointer: Any
    metrics_registry: Any
    run_manager: Any
    background_run_manager: Any
    stream_agent_events: Any
    normalize_search_mode: Any
    safe_deepsearch_config: Any
    require_thread_owner: Any


def _idempotency_key(request: Request) -> str:
    return (
        request.headers.get("Idempotency-Key")
        or request.headers.get("X-Idempotency-Key")
        or ""
    ).strip()


def _stable_id_from_idempotency_key(prefix: str, *, key: str, user_id: str) -> str:
    digest = hashlib.sha1(f"{prefix}:{user_id}:{key}".encode()).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _begin_idempotency(
    request: Request,
    *,
    scope: str,
    user_id: str,
    payload: Any,
):
    key = _idempotency_key(request)
    if not key:
        return None
    try:
        return idempotency_store.begin(
            key=key,
            scope=scope,
            user_id=user_id,
            request_hash=canonical_request_hash(payload),
        )
    except IdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _idempotent_response(record: Any) -> JSONResponse | None:
    if record is not None and getattr(record, "status", "") == "completed":
        return JSONResponse(
            status_code=int(getattr(record, "http_status", 200) or 200),
            content=dict(getattr(record, "response", {}) or {}),
            headers={"X-Idempotency-Replayed": "true"},
        )
    return None


def _build_run_evidence_summary(thread_id: str, *, checkpointer: Any) -> RunEvidenceSummary:
    sources_count = 0
    unsupported_claims_count = 0
    freshness_ratio_30d: Optional[float] = None
    citation_coverage: Optional[float] = None
    query_coverage_score: Optional[float] = None
    freshness_warning: Optional[str] = None
    claim_verifier_total: Optional[int] = None
    claim_verifier_verified: Optional[int] = None
    claim_verifier_unsupported: Optional[int] = None
    claim_verifier_contradicted: Optional[int] = None

    if checkpointer:
        try:
            from common.session_manager import get_session_manager

            manager = get_session_manager(checkpointer)
            session_state = manager.get_session_state(thread_id)
            if not session_state:
                raise ValueError("session not found")

            artifacts = session_state.deepsearch_artifacts or {}
            if not isinstance(artifacts, dict):
                raise TypeError("deepsearch_artifacts is not a dict")

            sources = artifacts.get("sources", [])
            if isinstance(sources, list):
                sources_count = len(sources)

            claims = artifacts.get("claims", [])
            if isinstance(claims, list):
                for claim in claims:
                    if not isinstance(claim, dict):
                        continue
                    status = (claim.get("status") or "").strip().lower()
                    if status in ("unsupported", "contradicted"):
                        unsupported_claims_count += 1

            freshness_summary = artifacts.get("freshness_summary", {})
            if isinstance(freshness_summary, dict):
                ratio = freshness_summary.get("fresh_30_ratio")
                if ratio is not None:
                    try:
                        freshness_ratio_30d = float(ratio)
                    except (TypeError, ValueError):
                        freshness_ratio_30d = None

            quality_summary = artifacts.get("quality_summary", {})
            if isinstance(quality_summary, dict):
                raw_citation = quality_summary.get(
                    "citation_coverage", quality_summary.get("citation_coverage_score")
                )
                if raw_citation is not None:
                    try:
                        citation_coverage = float(raw_citation)
                    except (TypeError, ValueError):
                        citation_coverage = None

                raw_query_coverage = quality_summary.get("query_coverage_score")
                if raw_query_coverage is None:
                    nested_coverage = quality_summary.get("query_coverage")
                    if isinstance(nested_coverage, dict):
                        raw_query_coverage = nested_coverage.get("score")
                if raw_query_coverage is None:
                    nested_query_coverage = artifacts.get("query_coverage")
                    if isinstance(nested_query_coverage, dict):
                        raw_query_coverage = nested_query_coverage.get("score")
                if raw_query_coverage is not None:
                    try:
                        query_coverage_score = float(raw_query_coverage)
                    except (TypeError, ValueError):
                        query_coverage_score = None

                freshness_warning_raw = quality_summary.get("freshness_warning")
                if isinstance(freshness_warning_raw, str) and freshness_warning_raw.strip():
                    freshness_warning = freshness_warning_raw.strip()

                def _maybe_int(value: Any) -> Optional[int]:
                    if value is None:
                        return None
                    try:
                        return int(value)
                    except (TypeError, ValueError):
                        return None

                claim_verifier_total = _maybe_int(quality_summary.get("claim_verifier_total"))
                claim_verifier_verified = _maybe_int(quality_summary.get("claim_verifier_verified"))
                claim_verifier_unsupported = _maybe_int(
                    quality_summary.get("claim_verifier_unsupported")
                )
                claim_verifier_contradicted = _maybe_int(
                    quality_summary.get("claim_verifier_contradicted")
                )
        except Exception:
            pass

    return RunEvidenceSummary(
        sources_count=sources_count,
        unsupported_claims_count=unsupported_claims_count,
        freshness_ratio_30d=freshness_ratio_30d,
        citation_coverage=citation_coverage,
        query_coverage_score=query_coverage_score,
        freshness_warning=freshness_warning,
        claim_verifier_total=claim_verifier_total,
        claim_verifier_verified=claim_verifier_verified,
        claim_verifier_unsupported=claim_verifier_unsupported,
        claim_verifier_contradicted=claim_verifier_contradicted,
    )


def build_runs_router(deps: RunsRouterDeps) -> APIRouter:
    router = APIRouter()

    @router.get("/api/runs")
    async def list_runs(request: Request):
        """List in-memory run metrics (per thread)."""
        runs_by_id = {str(run.get("run_id")): run for run in deps.metrics_registry.all()}
        for run in deps.run_manager.all():
            thread_key = str(run.get("thread_id") or run.get("run_id"))
            merged = dict(runs_by_id.get(thread_key, {}))
            merged.update(run)
            if "run_id" not in merged:
                merged["run_id"] = thread_key
            runs_by_id[thread_key] = merged
        runs = list(runs_by_id.values())
        internal_key = (getattr(deps.settings, "internal_api_key", "") or "").strip()
        if internal_key:
            principal_id = (getattr(request.state, "principal_id", "") or "").strip()
            runs = [
                run
                for run in runs
                if (
                    get_thread_owner(str(run.get("thread_id") or run.get("run_id") or ""))
                    or ""
                ).strip()
                == principal_id
            ]
        return {"runs": runs}

    @router.post("/api/runs/background")
    async def submit_background_run(request: Request, payload: BackgroundRunSubmitRequest):
        """Submit a long-running research job without holding an SSE connection."""
        if not deps.settings.background_runs_enabled:
            raise HTTPException(
                status_code=403,
                detail="Background runs are disabled. Set BACKGROUND_RUNS_ENABLED=true.",
            )
        owner_id = (getattr(request.state, "principal_id", "") or payload.user_id or "").strip()
        idem_key = _idempotency_key(request)
        thread_id = (payload.thread_id or "").strip()
        if not thread_id and idem_key:
            thread_id = _stable_id_from_idempotency_key(
                "bg",
                key=idem_key,
                user_id=owner_id or payload.user_id or "anonymous",
            )
        if not thread_id:
            thread_id = f"bg_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
        if owner_id:
            set_thread_owner(thread_id, owner_id)
        idempotency_record = _begin_idempotency(
            request,
            scope="POST /api/runs/background",
            user_id=owner_id or payload.user_id or "anonymous",
            payload={
                "thread_id": thread_id,
                "query": payload.query,
                "model": payload.model or deps.settings.primary_model,
                "search_mode": payload.search_mode,
                "user_id": payload.user_id,
                "deepsearch_config": payload.deepsearch_config,
                "retrieval_policy": payload.retrieval_policy,
                "skill_ids": payload.skill_ids,
                "webhook_url": payload.webhook_url,
                "research_brief": payload.research_brief,
            },
        )
        replay = _idempotent_response(idempotency_record)
        if replay is not None:
            return replay
        mode_info = deps.normalize_search_mode(payload.search_mode)
        safe_deepsearch_config = deps.safe_deepsearch_config(payload.deepsearch_config or {})
        try:
            reject_legacy_source_routing((payload.deepsearch_config or {}).get("source_routing"))
            safe_deepsearch_config["retrieval_policy"] = build_retrieval_policy(
                payload.retrieval_policy or safe_deepsearch_config.get("retrieval_policy"),
                user_id=payload.user_id or owner_id,
                config={
                    "configurable": {
                        **safe_deepsearch_config,
                        "user_id": payload.user_id or owner_id,
                    }
                },
            )
        except LegacySourceRoutingError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if payload.skill_ids:
            safe_deepsearch_config["skill_ids"] = payload.skill_ids
        if payload.webhook_url:
            safe_deepsearch_config["webhook_url"] = payload.webhook_url
        existing_record = deps.run_manager.get(thread_id)
        if existing_record and isinstance(existing_record.metadata, dict):
            injected_sources = existing_record.metadata.get("user_injected_sources")
            if isinstance(injected_sources, list) and injected_sources:
                safe_deepsearch_config["user_injected_sources"] = injected_sources
        images = [
            image.model_dump()
            for image in (payload.images or [])
            if isinstance(image, ImagePayload)
        ]
        try:
            status = await deps.background_run_manager.submit(
                BackgroundRunRequest(
                    input_text=payload.query,
                    thread_id=thread_id,
                    model=(payload.model or deps.settings.primary_model).strip(),
                    search_mode=mode_info,
                    images=images,
                    user_id=payload.user_id,
                    deepsearch_config=safe_deepsearch_config,
                    research_brief=payload.research_brief or None,
                ),
                stream_factory=deps.stream_agent_events,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        response_payload = {"status": "queued", "run": status}
        idempotency_store.complete(idempotency_record, response=response_payload)
        return response_payload

    @router.get("/api/runs/{thread_id}", response_model=RunMetricsResponse)
    async def get_run_metrics(thread_id: str, request: Request):
        """Get metrics for a specific run/thread."""
        internal_key = (getattr(deps.settings, "internal_api_key", "") or "").strip()
        if internal_key:
            principal_id = (getattr(request.state, "principal_id", "") or "").strip()
            owner_id = (get_thread_owner(thread_id) or "").strip()
            if owner_id and principal_id and owner_id != principal_id:
                raise HTTPException(status_code=403, detail="Forbidden")

        metrics = deps.metrics_registry.get(thread_id)
        if not metrics:
            record = deps.run_manager.get(thread_id)
            if not record:
                raise HTTPException(status_code=404, detail="Run not found")
            payload = {
                "run_id": record.run_id,
                "model": record.model,
                "route": record.route,
                "started_at": record.created_at,
                "ended_at": record.updated_at,
                "duration_ms": 0.0,
                "event_count": 0,
                "nodes_started": {},
                "nodes_completed": {},
                "errors": [record.error] if record.error else [],
                "cancelled": record.status == RunStatus.cancelled,
            }
        else:
            payload = metrics.to_dict()
        record = deps.run_manager.get(thread_id)
        if record:
            payload["status"] = record.status.value
            payload["token_summary"] = record.token_summary
            payload["quality_summary"] = record.quality_summary
        return RunMetricsResponse(
            **payload,
            evidence_summary=_build_run_evidence_summary(
                thread_id,
                checkpointer=deps.checkpointer,
            ),
        )

    @router.get("/api/runs/{thread_id}/events", response_model=RunEventsResponse)
    async def get_run_events(
        thread_id: str,
        request: Request,
        after_seq: int = 0,
        limit: int = 500,
    ):
        """Return persisted run events after a sequence number."""
        deps.require_thread_owner(request, thread_id)
        events = deps.run_manager.events_after(thread_id, after_seq=after_seq, limit=limit)
        return {
            "thread_id": thread_id,
            "after_seq": int(after_seq or 0),
            "count": len(events),
            "events": events,
        }

    @router.get("/api/runs/{thread_id}/events/sse")
    async def replay_run_events_sse(
        thread_id: str,
        request: Request,
        after_seq: int = 0,
        limit: int = 500,
    ):
        """Replay persisted run events as standard SSE frames."""
        deps.require_thread_owner(request, thread_id)

        last_event_id = (request.headers.get("Last-Event-ID") or "").strip()
        if last_event_id:
            try:
                after_seq = max(int(after_seq or 0), int(last_event_id))
            except ValueError:
                pass

        async def _replay_generator():
            for event in deps.run_manager.events_after(
                thread_id,
                after_seq=after_seq,
                limit=limit,
            ):
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    payload = {}
                event_type = str(event.get("type") or payload.get("type") or "event")
                yield format_sse_event(
                    event=event_type,
                    data=payload,
                    event_id=int(event.get("seq") or 0),
                )

        return StreamingResponse(
            _replay_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-Thread-ID": thread_id,
            },
        )

    @router.get("/api/runs/{thread_id}/background")
    async def get_background_run(thread_id: str, request: Request):
        deps.require_thread_owner(request, thread_id)
        status = deps.background_run_manager.status(thread_id)
        if not deps.run_manager.get(thread_id):
            raise HTTPException(status_code=404, detail="Run not found")
        return {"run": status}

    @router.post("/api/runs/{thread_id}/background/cancel")
    async def cancel_background_run(
        thread_id: str, request: Request, payload: CancelRequest | None = None
    ):
        deps.require_thread_owner(request, thread_id)
        reason = payload.reason if payload else "User requested cancellation"
        await deps.background_run_manager.cancel(
            thread_id,
            reason or "User requested cancellation",
        )
        return {"status": "cancelled", "thread_id": thread_id}

    @router.post("/api/runs/{thread_id}/resume")
    async def mark_run_resumed(thread_id: str, request: Request):
        deps.require_thread_owner(request, thread_id)
        record = deps.run_manager.get(thread_id)
        if not record:
            raise HTTPException(status_code=404, detail="Run not found")
        if deps.settings.background_runs_enabled and (record.metadata or {}).get("background"):
            try:
                run = await deps.background_run_manager.resume(
                    thread_id,
                    stream_factory=deps.stream_agent_events,
                )
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            return {"status": "resumed", "run": run}
        record = deps.run_manager.update(thread_id, status=RunStatus.resumed)
        if not record:
            raise HTTPException(status_code=404, detail="Run not found")
        return {"status": "resumed", "run": record.to_dict()}

    @router.post("/api/runs/{thread_id}/sources")
    async def inject_user_sources(
        thread_id: str,
        request: Request,
        payload: UserSourceInjectionRequest,
    ):
        deps.require_thread_owner(request, thread_id)
        sources = [source for source in payload.sources if isinstance(source, dict)]
        if not sources:
            raise HTTPException(status_code=400, detail="sources must contain at least one item")
        record = deps.run_manager.get(thread_id)
        if not record:
            raise HTTPException(status_code=404, detail="Run not found")
        metadata = dict(record.metadata or {})
        injected = list(metadata.get("user_injected_sources") or [])
        injected.extend(
            {
                **source,
                "source": source.get("source") or "user",
                "injected_at": datetime.now().isoformat(),
                "note": payload.note,
                "requires_current_run_verification": True,
            }
            for source in sources
        )
        deps.run_manager.update(
            thread_id,
            metadata={
                "user_injected_sources": injected,
                "last_user_source_injection_at": datetime.now().isoformat(),
            },
        )
        return {"status": "accepted", "thread_id": thread_id, "source_count": len(injected)}

    return router
