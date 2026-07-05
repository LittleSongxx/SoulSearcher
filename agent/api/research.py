from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from agent.api.schemas import ImagePayload, ResearchRequest
from agent.retrieval.policy import (
    LegacySourceRoutingError,
    build_retrieval_policy,
    reject_legacy_source_routing,
)
from agent.runtime.runs import RunStatus
from common.research_events import build_research_run_event
from common.sse import (
    format_sse_event,
    format_sse_retry,
    iter_abort_on_disconnect,
    iter_with_sse_keepalive,
)
from common.stream_translate import data_stream_line_to_payload
from common.thread_ownership import set_thread_owner


@dataclass(frozen=True, slots=True)
class ResearchRouterDeps:
    settings: Any
    logger: Any
    run_manager: Any
    sse_active_connections: Any
    normalize_search_mode: Callable[[Any], dict[str, Any]]
    safe_deepsearch_config: Callable[[Any], dict[str, Any]]
    persist_run_stream_event: Callable[..., dict[str, Any] | None]
    stream_agent_events_call: Callable[..., Any]
    build_research_brief: Callable[[dict[str, Any], dict[str, Any]], Any]


def _normalize_images_payload(
    images: Optional[list[ImagePayload]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if not images:
        return normalized

    for img in images:
        if not img or not img.data:
            continue
        data = img.data
        if data.startswith("data:") and "," in data:
            data = data.split(",", 1)[1]
        normalized.append(
            {"name": img.name or "", "mime": img.mime or "", "data": data}
        )
    return normalized


def build_research_router(deps: ResearchRouterDeps) -> APIRouter:
    router = APIRouter(tags=["research"])

    @router.post("/api/research/sse")
    async def research_sse(request: Request, payload: ResearchRequest):
        """
        Standard SSE research endpoint.

        This endpoint translates the internal stream protocol into standard SSE
        frames (`event:` / `data:`).
        """
        query = (payload.query or "").strip()
        if not query:
            raise HTTPException(status_code=400, detail="Query is required")

        internal_key = (getattr(deps.settings, "internal_api_key", "") or "").strip()
        principal_id = (getattr(request.state, "principal_id", "") or "").strip()
        user_id = (
            principal_id
            if internal_key and principal_id
            else (payload.user_id or deps.settings.memory_user_id)
        )
        mode_info = deps.normalize_search_mode(payload.search_mode)
        model = (payload.model or deps.settings.primary_model).strip()
        thread_id = f"thread_{uuid.uuid4().hex}"
        run_id = f"run_{uuid.uuid4().hex}"
        set_thread_owner(thread_id, principal_id or "anonymous")
        deps.run_manager.start(
            run_id=run_id,
            thread_id=thread_id,
            model=model,
            route=mode_info.get("mode", ""),
            user_id=user_id,
            metadata={"input_preview": query[:200]},
        )
        safe_deepsearch_config = deps.safe_deepsearch_config(
            payload.deepsearch_config or {}
        )
        try:
            reject_legacy_source_routing(
                (payload.deepsearch_config or {}).get("source_routing")
            )
            reject_legacy_source_routing(
                (payload.research_brief or {}).get("source_routing")
            )
            normalized_retrieval_policy = build_retrieval_policy(
                payload.retrieval_policy
                or safe_deepsearch_config.get("retrieval_policy"),
                user_id=user_id,
                config={
                    "configurable": {
                        **safe_deepsearch_config,
                        "user_id": user_id,
                    }
                },
            )
        except LegacySourceRoutingError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        safe_deepsearch_config["retrieval_policy"] = normalized_retrieval_policy
        if payload.skill_ids:
            safe_deepsearch_config["skill_ids"] = [
                str(skill_id).strip()
                for skill_id in payload.skill_ids
                if str(skill_id).strip()
            ]
        should_prepare_research_brief = bool(mode_info.get("use_deep"))
        preview_retrieval_policy: dict[str, Any] = normalized_retrieval_policy
        normalized_research_brief: dict[str, Any] = {}
        if should_prepare_research_brief:
            preview_state: dict[str, Any] = {"input": query, "user_id": user_id}
            if isinstance(payload.research_brief, dict) and payload.research_brief:
                preview_state["research_brief"] = payload.research_brief
            preview_config: dict[str, Any] = {
                "configurable": {
                    "thread_id": thread_id,
                    "model": model,
                    "search_mode": mode_info,
                    "user_id": user_id,
                    **safe_deepsearch_config,
                }
            }
            try:
                preview_brief = deps.build_research_brief(
                    preview_state,
                    preview_config,
                )
                preview_brief.retrieval_policy = preview_retrieval_policy
                normalized_research_brief = preview_brief.to_dict()
                normalized_research_brief["retrieval_policy"] = preview_retrieval_policy
            except Exception as exc:
                deps.logger.debug("Failed to prepare preview research brief: %s", exc)
                normalized_research_brief = (
                    payload.research_brief
                    if isinstance(payload.research_brief, dict)
                    else {}
                )

        async def _sse_generator():
            gauge = None
            try:
                gauge = deps.sse_active_connections.labels("research_sse")
                gauge.inc()
            except Exception:
                gauge = None

            try:
                seq = 0
                try:
                    if await request.is_disconnected():
                        return
                except Exception:
                    pass
                yield format_sse_retry(2000)
                seq += 1
                if should_prepare_research_brief:
                    brief_payload = {
                        "thread_id": thread_id,
                        "research_brief": normalized_research_brief,
                        "retrieval_policy": preview_retrieval_policy,
                    }
                    stream_payload = {
                        "type": "brief_created",
                        "data": brief_payload,
                        "research_event": build_research_run_event(
                            "brief_created",
                            brief_payload,
                            seq=seq,
                        ),
                    }
                    persisted = deps.persist_run_stream_event(
                        run_id=run_id,
                        thread_id=thread_id,
                        seq=seq,
                        event_type="brief_created",
                        payload=stream_payload,
                    )
                    if persisted:
                        seq = int(persisted.get("seq") or seq)
                        stream_payload = dict(
                            persisted.get("payload") or stream_payload
                        )
                    yield format_sse_event(
                        event="brief_created",
                        data=stream_payload,
                        event_id=seq,
                    )

                if not (deps.settings.openai_api_key or "").strip():
                    seq += 1
                    error_payload = {
                        "type": "error",
                        "data": {
                            "message": "OPENAI_API_KEY is not configured",
                            "thread_id": thread_id,
                        },
                        "research_event": build_research_run_event(
                            "error",
                            {
                                "message": "OPENAI_API_KEY is not configured",
                                "thread_id": thread_id,
                            },
                            seq=seq,
                        ),
                    }
                    persisted = deps.persist_run_stream_event(
                        run_id=run_id,
                        thread_id=thread_id,
                        seq=seq,
                        event_type="error",
                        payload=error_payload,
                    )
                    if persisted:
                        seq = int(persisted.get("seq") or seq)
                        error_payload = dict(persisted.get("payload") or error_payload)
                    yield format_sse_event(
                        event="error",
                        data=error_payload,
                        event_id=seq,
                    )
                    seq += 1
                    done_payload = {
                        "type": "done",
                        "data": {"thread_id": thread_id},
                        "research_event": build_research_run_event(
                            "done",
                            {"thread_id": thread_id},
                            seq=seq,
                        ),
                    }
                    persisted = deps.persist_run_stream_event(
                        run_id=run_id,
                        thread_id=thread_id,
                        seq=seq,
                        event_type="done",
                        payload=done_payload,
                    )
                    if persisted:
                        seq = int(persisted.get("seq") or seq)
                        done_payload = dict(persisted.get("payload") or done_payload)
                    yield format_sse_event(
                        event="done",
                        data=done_payload,
                        event_id=seq,
                    )
                    deps.run_manager.finish(
                        thread_id,
                        status=RunStatus.failed,
                        error="OPENAI_API_KEY is not configured",
                    )
                    return

                source = iter_with_sse_keepalive(
                    deps.stream_agent_events_call(
                        query,
                        thread_id=thread_id,
                        run_id=run_id,
                        model=model,
                        search_mode=mode_info,
                        images=_normalize_images_payload(payload.images),
                        user_id=user_id,
                        request=request,
                        deepsearch_config=safe_deepsearch_config,
                        research_brief=normalized_research_brief,
                    ),
                    interval_s=15.0,
                )

                async for maybe_line in iter_abort_on_disconnect(
                    source,
                    is_disconnected=request.is_disconnected,
                    check_interval_s=0.25,
                ):
                    if maybe_line.startswith(":"):
                        yield maybe_line
                        continue

                    seq += 1
                    payload_data = data_stream_line_to_payload(maybe_line, seq=seq)
                    if payload_data:
                        event_type = str(payload_data.get("type") or "event")
                        persisted = deps.persist_run_stream_event(
                            run_id=run_id,
                            thread_id=thread_id,
                            seq=seq,
                            event_type=event_type,
                            payload=payload_data,
                        )
                        if persisted:
                            seq = int(persisted.get("seq") or seq)
                            payload_data = dict(
                                persisted.get("payload") or payload_data
                            )
                        yield format_sse_event(
                            event=event_type,
                            data=payload_data,
                            event_id=seq,
                        )
            finally:
                try:
                    if gauge is not None:
                        gauge.dec()
                except Exception:
                    pass

        return StreamingResponse(
            _sse_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-Thread-ID": thread_id,
            },
        )

    return router
