from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/traces")


@router.get("/{thread_id}")
async def get_traces(thread_id: str, request: Request):
    """
    Get traces for a thread.

    Returns the latest trace with full span tree.
    """
    from common.thread_ownership import _require_thread_owner
    from common.config import settings
    from common.tracing import get_trace

    if not settings.enable_tracing:
        raise HTTPException(status_code=400, detail="Tracing is not enabled")

    _require_thread_owner(request, thread_id)

    trace = get_trace(thread_id)
    if not trace:
        raise HTTPException(
            status_code=404, detail=f"No traces found for thread {thread_id}"
        )

    return trace


@router.get("/{thread_id}/summary")
async def get_trace_summary(thread_id: str, request: Request):
    """
    Get trace summary for a thread.

    Returns high-level statistics: token counts, durations, node breakdown.
    """
    from common.thread_ownership import _require_thread_owner
    from common.config import settings
    from common.tracing import get_trace_summary as _get_summary

    if not settings.enable_tracing:
        raise HTTPException(status_code=400, detail="Tracing is not enabled")

    _require_thread_owner(request, thread_id)

    summary = _get_summary(thread_id)
    if not summary:
        raise HTTPException(
            status_code=404, detail=f"No traces found for thread {thread_id}"
        )

    return summary


@router.get("/{thread_id}/all")
async def get_all_traces(thread_id: str, request: Request):
    """
    Get all traces for a thread.

    Returns list of all stored traces (up to buffer limit).
    """
    from common.thread_ownership import _require_thread_owner
    from common.config import settings
    from common.tracing import get_all_traces as _get_all

    if not settings.enable_tracing:
        raise HTTPException(status_code=400, detail="Tracing is not enabled")

    _require_thread_owner(request, thread_id)

    traces = _get_all(thread_id)
    return {"thread_id": thread_id, "count": len(traces), "traces": traces}
