from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agent.runtime.runs import RunStatus, run_manager
from common.cancellation import cancellation_manager

logger = logging.getLogger(__name__)


StreamFactory = Callable[..., AsyncIterator[str]]


@dataclass
class BackgroundRunRequest:
    input_text: str
    thread_id: str
    model: str | None = None
    search_mode: dict[str, Any] | None = None
    images: list[dict[str, Any]] = field(default_factory=list)
    user_id: str | None = None
    deepsearch_config: dict[str, Any] = field(default_factory=dict)
    research_brief: dict[str, Any] | None = None


class BackgroundRunManager:
    """Small in-process background queue for long research runs.

    The manager intentionally reuses the normal streaming execution path. It
    drains stream events in a background task so SSE callers and background
    callers cannot diverge in graph behavior.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def submit(
        self,
        request: BackgroundRunRequest,
        *,
        stream_factory: StreamFactory,
    ) -> dict[str, Any]:
        async with self._lock:
            existing = self._tasks.get(request.thread_id)
            if existing and not existing.done():
                raise ValueError(f"Run already active for thread_id={request.thread_id}")
            run_manager.start(
                run_id=request.thread_id,
                thread_id=request.thread_id,
                model=request.model or "",
                route=str((request.search_mode or {}).get("mode") or ""),
                user_id=request.user_id or "",
                metadata={
                    "background": True,
                    "queued_at": datetime.now(UTC).isoformat(),
                    "input_preview": request.input_text[:200],
                },
            )
            run_manager.update(request.thread_id, status=RunStatus.queued)
            task = asyncio.create_task(
                self._run(request, stream_factory=stream_factory),
                name=f"weaver-background-run:{request.thread_id}",
            )
            self._tasks[request.thread_id] = task
            return self.status(request.thread_id)

    def status(self, thread_id: str) -> dict[str, Any]:
        record = run_manager.get(thread_id)
        task = self._tasks.get(thread_id)
        payload = record.to_dict() if record else {"thread_id": thread_id, "run_id": thread_id}
        payload["background"] = True
        payload["task_active"] = bool(task and not task.done())
        return payload

    async def cancel(self, thread_id: str, reason: str = "User requested cancellation") -> bool:
        await cancellation_manager.cancel(thread_id, reason)
        task = self._tasks.get(thread_id)
        if task and not task.done():
            task.cancel()
        run_manager.update(thread_id, status=RunStatus.cancelled, error=reason)
        return bool(task)

    async def _run(
        self,
        request: BackgroundRunRequest,
        *,
        stream_factory: StreamFactory,
    ) -> None:
        run_manager.update(
            request.thread_id,
            status=RunStatus.running,
            metadata={"started_background_at": datetime.now(UTC).isoformat()},
        )
        try:
            async for _event in stream_factory(
                request.input_text,
                thread_id=request.thread_id,
                model=request.model,
                search_mode=request.search_mode,
                images=request.images,
                user_id=request.user_id,
                request=None,
                deepsearch_config=request.deepsearch_config,
                research_brief=request.research_brief,
            ):
                pass
            record = run_manager.get(request.thread_id)
            if record and record.status in {
                RunStatus.queued,
                RunStatus.running,
                RunStatus.resumed,
            }:
                run_manager.update(request.thread_id, status=RunStatus.completed)
        except asyncio.CancelledError:
            run_manager.update(
                request.thread_id,
                status=RunStatus.cancelled,
                error="Background run cancelled",
            )
            raise
        except Exception as exc:
            logger.exception("[BackgroundRun] failed for %s", request.thread_id)
            run_manager.update(
                request.thread_id,
                status=RunStatus.failed,
                error=str(exc),
            )
        finally:
            task = self._tasks.get(request.thread_id)
            if task and task.done():
                self._tasks.pop(request.thread_id, None)


background_run_manager = BackgroundRunManager()
