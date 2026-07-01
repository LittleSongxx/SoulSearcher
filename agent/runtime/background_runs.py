from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from agent.runtime.background_leases import BackgroundLeaseStore, Lease
from agent.runtime.runs import RunStatus, run_manager
from common.cancellation import cancellation_manager
from common.config import settings
from common.stream_translate import data_stream_line_to_payload

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_text": self.input_text,
            "thread_id": self.thread_id,
            "model": self.model,
            "search_mode": self.search_mode or {},
            "images": self.images,
            "user_id": self.user_id,
            "deepsearch_config": self.deepsearch_config or {},
            "research_brief": self.research_brief,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> BackgroundRunRequest:
        return cls(
            input_text=str(payload.get("input_text") or ""),
            thread_id=str(payload.get("thread_id") or ""),
            model=str(payload.get("model") or "") or None,
            search_mode=(
                dict(payload.get("search_mode"))
                if isinstance(payload.get("search_mode"), dict)
                else None
            ),
            images=[
                item for item in (payload.get("images") or [])
                if isinstance(item, dict)
            ],
            user_id=str(payload.get("user_id") or "") or None,
            deepsearch_config=(
                dict(payload.get("deepsearch_config"))
                if isinstance(payload.get("deepsearch_config"), dict)
                else {}
            ),
            research_brief=(
                dict(payload.get("research_brief"))
                if isinstance(payload.get("research_brief"), dict)
                else None
            ),
        )


class BackgroundRunManager:
    """Small in-process background queue for long research runs.

    The manager intentionally reuses the normal streaming execution path. It
    drains stream events in a background task so SSE callers and background
    callers cannot diverge in graph behavior.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._lease_store = BackgroundLeaseStore(
            redis_url=str(getattr(settings, "redis_url", "") or ""),
            ttl_seconds=int(getattr(settings, "background_run_lease_ttl_seconds", 1800) or 1800),
        )

    @property
    def lease_status(self) -> dict[str, Any]:
        return self._lease_store.status

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
                    "background_request": request.to_dict(),
                },
            )
            run_manager.update(request.thread_id, status=RunStatus.queued)
            task = asyncio.create_task(
                self._run(request, stream_factory=stream_factory),
                name=f"soulsearcher-background-run:{request.thread_id}",
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

    def mark_stale_active_runs_failed(
        self,
        *,
        reason: str = "Background run was active during process startup and requires manual resume.",
    ) -> int:
        """Mark durable background records left active by a previous process as stale.

        This keeps status endpoints honest after restarts. Actual automatic
        recovery can build on the persisted ``background_request`` snapshot, but
        should use a real distributed queue/lock before multiple workers can
        safely claim jobs.
        """
        count = 0
        for payload in run_manager.all():
            if not isinstance(payload, dict):
                continue
            metadata = payload.get("metadata")
            if not isinstance(metadata, dict) or not metadata.get("background"):
                continue
            status = str(payload.get("status") or "")
            if status not in {
                RunStatus.queued.value,
                RunStatus.running.value,
                RunStatus.resumed.value,
            }:
                continue
            thread_id = str(payload.get("thread_id") or payload.get("run_id") or "")
            if not thread_id:
                continue
            run_manager.update(
                thread_id,
                status=RunStatus.failed,
                error=reason,
                metadata={"stale_background_run": True},
            )
            count += 1
        return count

    async def cancel(self, thread_id: str, reason: str = "User requested cancellation") -> bool:
        await cancellation_manager.cancel(thread_id, reason)
        task = self._tasks.get(thread_id)
        if task and not task.done():
            task.cancel()
        run_manager.update(thread_id, status=RunStatus.cancelled, error=reason)
        await self._deliver_webhook(thread_id, status=RunStatus.cancelled, error=reason)
        return bool(task)

    async def resume(
        self,
        thread_id: str,
        *,
        stream_factory: StreamFactory,
    ) -> dict[str, Any]:
        async with self._lock:
            existing = self._tasks.get(thread_id)
            if existing and not existing.done():
                raise ValueError(f"Run already active for thread_id={thread_id}")
            record = run_manager.get(thread_id)
            if not record:
                raise ValueError(f"Run not found for thread_id={thread_id}")
            metadata = dict(record.metadata or {})
            snapshot = metadata.get("background_request")
            if not isinstance(snapshot, dict):
                raise ValueError("Run does not contain a resumable background request")
            request = BackgroundRunRequest.from_dict(snapshot)
            if not request.thread_id:
                request.thread_id = thread_id
            run_manager.update(
                thread_id,
                status=RunStatus.resumed,
                metadata={"resumed_background_at": datetime.now(UTC).isoformat()},
            )
            task = asyncio.create_task(
                self._run(request, stream_factory=stream_factory),
                name=f"soulsearcher-background-run:{thread_id}:resume",
            )
            self._tasks[thread_id] = task
            return self.status(thread_id)

    async def _run(
        self,
        request: BackgroundRunRequest,
        *,
        stream_factory: StreamFactory,
    ) -> None:
        lease = self._lease_store.acquire(request.thread_id)
        if not lease.acquired:
            run_manager.update(
                request.thread_id,
                status=RunStatus.queued,
                error=(
                    "Background run is already leased by another worker."
                    if lease.backend != "redis_unavailable"
                    else "Background run lease backend is unavailable."
                ),
                metadata={
                    "lease_conflict_at": datetime.now(UTC).isoformat(),
                    "lease_backend": lease.backend,
                },
            )
            return
        heartbeat = asyncio.create_task(
            self._heartbeat_lease(lease),
            name=f"soulsearcher-background-lease:{request.thread_id}",
        )
        run_manager.update(
            request.thread_id,
            status=RunStatus.running,
            metadata={
                "started_background_at": datetime.now(UTC).isoformat(),
                "lease_backend": lease.backend,
            },
        )
        record = run_manager.get(request.thread_id)
        run_id = record.run_id if record else request.thread_id
        seq = 0
        try:
            async for event_line in stream_factory(
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
                if not isinstance(event_line, str) or event_line.startswith(":"):
                    continue
                seq += 1
                payload = data_stream_line_to_payload(event_line, seq=seq)
                if not payload:
                    continue
                persisted = run_manager.append_event(
                    run_id=run_id,
                    thread_id=request.thread_id,
                    seq=seq,
                    type=str(payload.get("type") or "event"),
                    payload=payload,
                )
                seq = max(seq, int(persisted.get("seq") or seq))
            record = run_manager.get(request.thread_id)
            if record and record.status in {
                RunStatus.queued,
                RunStatus.running,
                RunStatus.resumed,
            }:
                run_manager.update(request.thread_id, status=RunStatus.completed)
                await self._deliver_webhook(request.thread_id, status=RunStatus.completed)
        except asyncio.CancelledError:
            run_manager.update(
                request.thread_id,
                status=RunStatus.cancelled,
                error="Background run cancelled",
            )
            with suppress(Exception):
                await self._deliver_webhook(
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
            await self._deliver_webhook(
                request.thread_id,
                status=RunStatus.failed,
                error=str(exc),
            )
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
            self._lease_store.release(lease)
            task = self._tasks.get(request.thread_id)
            if task and task.done():
                self._tasks.pop(request.thread_id, None)

    async def _heartbeat_lease(self, lease: Lease) -> None:
        interval = max(5.0, self._lease_store.ttl_seconds / 3)
        try:
            while True:
                await asyncio.sleep(interval)
                if not self._lease_store.refresh(lease):
                    logger.warning("[BackgroundRun] lost lease %s", lease.key)
                    thread_id = lease.key.rsplit(":", 1)[-1]
                    await cancellation_manager.cancel(thread_id, "Background run lease lost")
                    return
        except asyncio.CancelledError:
            return

    async def _deliver_webhook(
        self,
        thread_id: str,
        *,
        status: RunStatus,
        error: str = "",
    ) -> None:
        record = run_manager.get(thread_id)
        if not record:
            return
        metadata = dict(record.metadata or {})
        request_snapshot = metadata.get("background_request")
        deepsearch_config = (
            request_snapshot.get("deepsearch_config")
            if isinstance(request_snapshot, dict)
            else {}
        )
        webhook_url = str(
            metadata.get("webhook_url")
            or (
                deepsearch_config.get("webhook_url")
                if isinstance(deepsearch_config, dict)
                else ""
            )
            or ""
        ).strip()
        if not _safe_webhook_url(webhook_url):
            return
        payload = {
            "event": f"run.{status.value}",
            "thread_id": record.thread_id,
            "run_id": record.run_id,
            "status": status.value,
            "error": error or record.error,
            "updated_at": datetime.now(UTC).isoformat(),
            "quality_summary": record.quality_summary,
            "token_summary": record.token_summary,
        }
        delivery = {
            "webhook_url": webhook_url,
            "webhook_status": "pending",
            "webhook_attempted_at": datetime.now(UTC).isoformat(),
        }
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
                response = await client.post(webhook_url, json=payload)
            delivery.update(
                {
                    "webhook_status": "delivered"
                    if 200 <= response.status_code < 300
                    else "failed",
                    "webhook_http_status": response.status_code,
                }
            )
        except Exception as exc:
            logger.warning("[BackgroundRun] webhook delivery failed for %s: %s", thread_id, exc)
            delivery.update({"webhook_status": "failed", "webhook_error": str(exc)})
        run_manager.update(thread_id, metadata=delivery)


def _safe_webhook_url(url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


background_run_manager = BackgroundRunManager()
