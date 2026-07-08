from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from common.thread_ownership import get_thread_owner, set_thread_owner


class CancelRequest(BaseModel):
    """Request body for cancelling a running task."""

    reason: Optional[str] = "User requested cancellation"


class ForkSessionRequest(BaseModel):
    """Request payload for forking a research session."""

    source_thread_id: str = Field(..., description="Thread ID to fork from")
    new_thread_id: Optional[str] = Field(
        default=None,
        description="Optional target thread ID. Auto-generated if omitted.",
    )
    visibility: str = Field(
        default="private",
        description="Visibility for the forked session (private/group/public).",
    )


@dataclass(frozen=True, slots=True)
class ResearchControlRouterDeps:
    settings: Any
    checkpointer: Any
    logger: Any
    cancellation_manager: Any
    active_streams: dict[str, Any]
    task_status: Any
    require_thread_owner: Callable[[Request, str], None]


def _task_is_visible_to_principal(
    task_id: str, task_info: dict[str, Any], principal_id: str
) -> bool:
    owner_id = (get_thread_owner(task_id) or "").strip()
    if owner_id:
        return owner_id == principal_id
    metadata = task_info.get("metadata", {})
    if isinstance(metadata, dict):
        meta_user_id = metadata.get("user_id")
        if isinstance(meta_user_id, str) and meta_user_id.strip():
            return meta_user_id.strip() == principal_id
    return False


def build_research_control_router(deps: ResearchControlRouterDeps) -> APIRouter:
    router = APIRouter(tags=["research-control"])

    @router.post("/api/research/cancel/{thread_id}")
    async def cancel_research(
        thread_id: str, request: Request, payload: CancelRequest | None = None
    ):
        """Cancel a running research task."""
        deps.require_thread_owner(request, thread_id)

        reason = payload.reason if payload else "User requested cancellation"
        deps.logger.info(
            "Cancel request received for thread: %s, reason: %s",
            thread_id,
            reason,
        )

        cancelled = await deps.cancellation_manager.cancel(thread_id, reason)

        if thread_id in deps.active_streams:
            task = deps.active_streams[thread_id]
            task.cancel()
            del deps.active_streams[thread_id]
            deps.logger.info("Async task for %s cancelled", thread_id)

        if cancelled:
            return {
                "status": "cancelled",
                "thread_id": thread_id,
                "reason": reason,
                "timestamp": datetime.now().isoformat(),
            }
        return {
            "status": "not_found",
            "thread_id": thread_id,
            "message": "Task not found or already completed",
        }

    @router.post("/api/research/cancel-all")
    async def cancel_all_research(request: Request):
        """Cancel all currently running tasks."""
        deps.logger.info("Cancel all tasks requested")

        reason = "Batch cancellation requested"
        internal_key = (getattr(deps.settings, "internal_api_key", "") or "").strip()
        if internal_key:
            principal_id = (getattr(request.state, "principal_id", "") or "").strip()

            active = deps.cancellation_manager.get_active_tasks()
            owned_task_ids = [
                task_id
                for task_id, info in active.items()
                if _task_is_visible_to_principal(task_id, info, principal_id)
            ]
            for task_id in owned_task_ids:
                await deps.cancellation_manager.cancel(task_id, reason)
                task = deps.active_streams.pop(task_id, None)
                if task:
                    task.cancel()

            cancelled_count = len(owned_task_ids)
        else:
            await deps.cancellation_manager.cancel_all(reason)

            cancelled_count = len(deps.active_streams)
            for task in deps.active_streams.values():
                task.cancel()
            deps.active_streams.clear()

        return {
            "status": "all_cancelled",
            "cancelled_count": cancelled_count,
            "timestamp": datetime.now().isoformat(),
        }

    @router.post("/api/research/fork")
    async def fork_research_session(
        request: Request,
        payload: ForkSessionRequest,
    ):
        """Fork a research session to explore alternative paths."""
        if not deps.checkpointer:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Fork requires a persistent checkpointer (PostgreSQL). "
                    "Set DATABASE_URL in .env."
                ),
            )

        deps.require_thread_owner(request, payload.source_thread_id)

        try:
            from common.session_manager import get_session_manager

            manager = get_session_manager(deps.checkpointer)

            source_session = manager.get_session(payload.source_thread_id)
            if not source_session:
                raise HTTPException(
                    status_code=404,
                    detail=f"Source session not found: {payload.source_thread_id}",
                )

            owner_id = ""
            internal_key = (
                getattr(deps.settings, "internal_api_key", "") or ""
            ).strip()
            if internal_key:
                owner_id = (getattr(request.state, "principal_id", "") or "").strip()

            forked = manager.fork_session(
                source_thread_id=payload.source_thread_id,
                new_thread_id=payload.new_thread_id,
                owner_id=owner_id,
            )

            if not forked:
                raise HTTPException(
                    status_code=500,
                    detail="Fork operation failed. Check server logs for details.",
                )

            if owner_id:
                set_thread_owner(forked.thread_id, owner_id)

            deps.logger.info(
                "[Fork] Session forked: %s -> %s",
                payload.source_thread_id,
                forked.thread_id,
            )

            return {
                "status": "forked",
                "source_thread_id": payload.source_thread_id,
                "forked_thread_id": forked.thread_id,
                "session": forked.to_dict(),
                "timestamp": datetime.now().isoformat(),
            }

        except HTTPException:
            raise
        except Exception as exc:
            deps.logger.error("[Fork] Unexpected error: %s", exc, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Fork failed: {exc!s}",
            ) from exc

    @router.get("/api/tasks/active")
    async def get_active_tasks(request: Request):
        """Get all active tasks."""
        active_tasks = deps.cancellation_manager.get_active_tasks()

        internal_key = (getattr(deps.settings, "internal_api_key", "") or "").strip()
        if internal_key:
            principal_id = (getattr(request.state, "principal_id", "") or "").strip()
            active_tasks = {
                task_id: info
                for task_id, info in active_tasks.items()
                if _task_is_visible_to_principal(task_id, info, principal_id)
            }

            stats = {status.value: 0 for status in deps.task_status}
            for info in active_tasks.values():
                st = info.get("status")
                if isinstance(st, str) and st in stats:
                    stats[st] += 1
            stats["total"] = len(active_tasks)

            stream_count = sum(
                1
                for thread_id in deps.active_streams
                if _task_is_visible_to_principal(
                    thread_id,
                    active_tasks.get(thread_id, {}),
                    principal_id,
                )
            )
        else:
            stats = deps.cancellation_manager.get_stats()
            stream_count = len(deps.active_streams)

        return {
            "active_tasks": active_tasks,
            "stats": stats,
            "stream_count": stream_count,
            "timestamp": datetime.now().isoformat(),
        }

    return router
