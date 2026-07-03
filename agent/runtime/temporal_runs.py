"""Temporal-backed execution for durable background research runs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from agent.runtime.runs import RunStatus, run_manager
from agent.runtime.callback_outbox import callback_outbox
from common.config import settings
from common.stream_translate import data_stream_line_to_payload


@dataclass
class TemporalRunHandle:
    workflow_id: str
    run_id: str = ""


def temporal_backend_enabled() -> bool:
    return str(getattr(settings, "background_execution_backend", "local") or "local").lower() == "temporal"


async def start_temporal_background_run(request: Any) -> TemporalRunHandle:
    client = await _temporal_client()
    workflow_id = _workflow_id(request.thread_id)
    request_payload = {
        **request.to_dict(),
        "temporal_workflow_version": str(getattr(settings, "temporal_workflow_version", "v2") or "v2"),
    }
    try:
        handle = await client.start_workflow(
            "DeepResearchBackgroundWorkflow",
            request_payload,
            id=workflow_id,
            task_queue=str(getattr(settings, "temporal_task_queue", "soulsearcher-deep-research") or "soulsearcher-deep-research"),
            execution_timeout=timedelta(seconds=int(getattr(settings, "temporal_workflow_timeout_seconds", 21600) or 21600)),
        )
        run_id = getattr(handle, "first_execution_run_id", "") or getattr(handle, "result_run_id", "") or ""
        return TemporalRunHandle(workflow_id=workflow_id, run_id=str(run_id or ""))
    except Exception as exc:
        if "already started" not in str(exc).lower() and "workflow execution already started" not in str(exc).lower():
            raise
        handle = client.get_workflow_handle(workflow_id)
        run_id = getattr(handle, "first_execution_run_id", "") or ""
        return TemporalRunHandle(workflow_id=workflow_id, run_id=str(run_id or ""))


async def cancel_temporal_background_run(thread_id: str, reason: str = "") -> None:
    client = await _temporal_client()
    handle = client.get_workflow_handle(_workflow_id(thread_id))
    try:
        await handle.signal("cancel_requested", reason or "User requested cancellation")
    finally:
        await handle.cancel()


async def signal_temporal_resume(thread_id: str, payload: dict[str, Any] | None = None) -> None:
    client = await _temporal_client()
    handle = client.get_workflow_handle(_workflow_id(thread_id))
    await handle.signal("resume_requested", payload or {})


async def _temporal_client():
    try:
        from temporalio.client import Client
    except Exception as exc:  # pragma: no cover - covered when optional dep missing
        raise RuntimeError("temporalio is required for SOULSEARCHER_BACKGROUND_EXECUTION_BACKEND=temporal") from exc

    return await Client.connect(
        str(getattr(settings, "temporal_address", "localhost:7233") or "localhost:7233"),
        namespace=str(getattr(settings, "temporal_namespace", "default") or "default"),
    )


def _workflow_id(thread_id: str) -> str:
    return f"soulsearcher-background-{thread_id}"


async def run_background_activity(request_payload: dict[str, Any]) -> dict[str, Any]:
    from agent.runtime.background_runs import BackgroundRunRequest

    request = BackgroundRunRequest.from_dict(request_payload)
    from main import stream_agent_events  # Imported inside the worker process to avoid app import cycles.

    run_manager.update(
        request.thread_id,
        status=RunStatus.running,
        metadata={"execution_backend": "temporal", "temporal_activity_started": True},
    )
    record = run_manager.get(request.thread_id)
    run_id = record.run_id if record else request.thread_id
    seq = 0
    final_content = ""
    final_format = "markdown"
    callback_cfg = request.deepsearch_config.get("a2a_callback") if isinstance(request.deepsearch_config, dict) else {}
    callback_cfg = callback_cfg if isinstance(callback_cfg, dict) else {}
    try:
        async for event_line in stream_agent_events(
            request.input_text,
            thread_id=request.thread_id,
            run_id=run_id,
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
            enriched = enrich_run_event_payload(payload, seq=seq)
            event_type = str(enriched.get("type") or "")
            data = enriched.get("data") if isinstance(enriched.get("data"), dict) else {}
            if event_type in {"completion", "report_written", "artifact"}:
                content = str(data.get("content") or data.get("text") or "").strip()
                if content:
                    final_content = content
                    final_format = str(data.get("format") or final_format or "markdown")
            persisted = run_manager.append_event(
                run_id=run_id,
                thread_id=request.thread_id,
                seq=seq,
                type=str(enriched.get("type") or "event"),
                payload=enriched,
            )
            seq = max(seq, int(persisted.get("seq") or seq))
        record = run_manager.get(request.thread_id)
        if record and record.status in {RunStatus.queued, RunStatus.running, RunStatus.resumed}:
            run_manager.update(request.thread_id, status=RunStatus.completed, metadata={"execution_backend": "temporal"})
        await _deliver_a2a_completion_callback(callback_cfg, final_content=final_content, final_format=final_format)
        return {"status": "completed", "thread_id": request.thread_id, "events": seq}
    except asyncio.CancelledError:
        run_manager.update(request.thread_id, status=RunStatus.cancelled, error="Temporal workflow cancelled")
        raise
    except Exception as exc:
        run_manager.update(request.thread_id, status=RunStatus.failed, error=str(exc), metadata={"execution_backend": "temporal"})
        await _deliver_a2a_failure_callback(callback_cfg, error=str(exc), seq=seq)
        raise


async def initialize_run_activity(request_payload: dict[str, Any]) -> dict[str, Any]:
    request = _background_request(request_payload)
    operation_key = _operation_key(request, "initialize_run")
    run_manager.update(
        request.thread_id,
        status=RunStatus.running,
        metadata={
            "execution_backend": "temporal",
            "temporal_workflow_version": "v2",
            "operation_key": operation_key,
            "stage": "initialize_run",
        },
    )
    record = run_manager.get(request.thread_id)
    run_id = record.run_id if record else request.thread_id
    seq = _append_stage_event(
        run_id=run_id,
        thread_id=request.thread_id,
        event_type="workflow_stage",
        stage="initialize_run",
        status="completed",
        operation_key=operation_key,
    )
    return {"status": "completed", "thread_id": request.thread_id, "run_id": run_id, "last_event_seq": seq}


async def build_brief_activity(request_payload: dict[str, Any]) -> dict[str, Any]:
    request = _background_request(request_payload)
    record = run_manager.get(request.thread_id)
    run_id = record.run_id if record else request.thread_id
    operation_key = _operation_key(request, "build_brief")
    seq = _append_stage_event(
        run_id=run_id,
        thread_id=request.thread_id,
        event_type="brief_created",
        stage="build_brief",
        status="completed",
        operation_key=operation_key,
        data={"brief": request.research_brief or request.input_text[:1000]},
    )
    return {"status": "completed", "thread_id": request.thread_id, "run_id": run_id, "last_event_seq": seq}


async def plan_or_wait_hitl_activity(request_payload: dict[str, Any]) -> dict[str, Any]:
    request = _background_request(request_payload)
    config = request.deepsearch_config if isinstance(request.deepsearch_config, dict) else {}
    record = run_manager.get(request.thread_id)
    run_id = record.run_id if record else request.thread_id
    operation_key = _operation_key(request, "plan_or_wait_hitl")
    if bool(config.get("require_plan_approval")) and not isinstance(request_payload.get("resume_payload"), dict):
        run_manager.update(
            request.thread_id,
            status=RunStatus.waiting_for_input,
            metadata={"stage": "plan_or_wait_hitl", "operation_key": operation_key},
        )
        seq = _append_stage_event(
            run_id=run_id,
            thread_id=request.thread_id,
            event_type="interrupt",
            stage="plan_or_wait_hitl",
            status=RunStatus.waiting_for_input.value,
            operation_key=operation_key,
            data={"kind": "plan_approval", "allowed_decisions": ["approve", "edit", "reject"]},
        )
        return {"status": RunStatus.waiting_for_input.value, "thread_id": request.thread_id, "run_id": run_id, "last_event_seq": seq}
    run_manager.update(request.thread_id, status=RunStatus.running, metadata={"stage": "plan_or_wait_hitl", "operation_key": operation_key})
    seq = _append_stage_event(
        run_id=run_id,
        thread_id=request.thread_id,
        event_type="workflow_stage",
        stage="plan_or_wait_hitl",
        status="completed",
        operation_key=operation_key,
    )
    return {"status": "completed", "thread_id": request.thread_id, "run_id": run_id, "last_event_seq": seq}


async def execute_plan_tasks_activity(request_payload: dict[str, Any]) -> dict[str, Any]:
    request = _background_request(request_payload)
    operation_key = _operation_key(request, "execute_plan_tasks")
    run_manager.update(request.thread_id, status=RunStatus.running, metadata={"stage": "execute_plan_tasks", "operation_key": operation_key})
    return await run_background_activity({**request_payload, "operation_key": operation_key})


async def write_report_activity(payload: dict[str, Any]) -> dict[str, Any]:
    return await _post_stage_activity(payload, stage="write_report", event_type="report_stage")


async def quality_check_activity(payload: dict[str, Any]) -> dict[str, Any]:
    return await _post_stage_activity(payload, stage="quality_check", event_type="quality_update")


async def publish_artifacts_activity(payload: dict[str, Any]) -> dict[str, Any]:
    return await _post_stage_activity(payload, stage="publish_artifacts", event_type="artifact")


async def complete_callbacks_activity(payload: dict[str, Any]) -> dict[str, Any]:
    return await _post_stage_activity(payload, stage="complete_callbacks", event_type="workflow_stage")


def _background_request(request_payload: dict[str, Any]):
    from agent.runtime.background_runs import BackgroundRunRequest

    return BackgroundRunRequest.from_dict(request_payload)


def _operation_key(request: Any, stage: str) -> str:
    idempotency_key = ""
    config = request.deepsearch_config if isinstance(request.deepsearch_config, dict) else {}
    if isinstance(config, dict):
        idempotency_key = str(config.get("idempotency_key") or config.get("client_request_id") or "")
    return f"{request.thread_id}:{stage}:{idempotency_key or request.input_text[:80]}"


def _append_stage_event(
    *,
    run_id: str,
    thread_id: str,
    event_type: str,
    stage: str,
    status: str,
    operation_key: str,
    data: dict[str, Any] | None = None,
) -> int:
    existing = run_manager.events_after(thread_id, after_seq=0, limit=2000)
    seq = max([int(item.get("seq") or 0) for item in existing] or [0]) + 1
    payload = enrich_run_event_payload(
        {
            "type": event_type,
            "data": {
                "phase": stage,
                "status": status,
                "operation_key": operation_key,
                **(data or {}),
            },
        },
        seq=seq,
    )
    persisted = run_manager.append_event(run_id=run_id, thread_id=thread_id, seq=seq, type=event_type, payload=payload)
    return int(persisted.get("seq") or seq)


async def _post_stage_activity(payload: dict[str, Any], *, stage: str, event_type: str) -> dict[str, Any]:
    request_payload = payload.get("request") if isinstance(payload.get("request"), dict) else payload
    request = _background_request(request_payload)
    record = run_manager.get(request.thread_id)
    run_id = record.run_id if record else request.thread_id
    operation_key = _operation_key(request, stage)
    seq = _append_stage_event(
        run_id=run_id,
        thread_id=request.thread_id,
        event_type=event_type,
        stage=stage,
        status="completed",
        operation_key=operation_key,
    )
    run_manager.update(request.thread_id, metadata={"stage": stage, "operation_key": operation_key})
    return {
        "status": "completed",
        "thread_id": request.thread_id,
        "run_id": run_id,
        "last_event_seq": seq,
        "previous": payload.get("previous") if isinstance(payload, dict) else {},
    }


def enrich_run_event_payload(payload: dict[str, Any], *, seq: int) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    event_type = str(payload.get("type") or "")
    phase = str(data.get("phase") or _phase_from_event(event_type) or "")
    status = str(data.get("status") or _status_from_event(event_type) or "")
    enriched = dict(payload)
    enriched.setdefault("phase", phase)
    enriched.setdefault("status", status)
    enriched.setdefault("progress", data.get("progress") if isinstance(data.get("progress"), (int, float)) else None)
    enriched.setdefault("last_event_seq", seq)
    enriched.setdefault("heartbeat_at", "")
    enriched.setdefault("artifact_ref", data.get("artifact_ref") or data.get("artifact_id") or "")
    enriched.setdefault("visibility", _visibility_from_event(event_type))
    return enriched


def _phase_from_event(event_type: str) -> str:
    if event_type in {"brief_created", "research_node_start", "research_node_complete"}:
        return "planning"
    if event_type in {"search", "sources"}:
        return "searching"
    if event_type in {"tool", "tool_start", "tool_result", "tool_error"}:
        return "tooling"
    if event_type in {"quality_update"}:
        return "evaluating"
    if event_type in {"completion", "report_written", "done"}:
        return "writing"
    return "running"


def _status_from_event(event_type: str) -> str:
    if event_type == "done":
        return RunStatus.completed.value
    if event_type == "interrupt":
        return RunStatus.waiting_for_input.value
    if event_type in {"cancelled", "canceled"}:
        return RunStatus.cancelled.value
    if event_type == "error":
        return RunStatus.failed.value
    return RunStatus.running.value


def _visibility_from_event(event_type: str) -> str:
    if event_type in {"text", "thinking"}:
        return "stream"
    if event_type in {"search", "research_node_start", "research_node_complete", "quality_update", "interrupt", "error", "done", "completion"}:
        return "milestone"
    return "debug"


async def _deliver_a2a_completion_callback(callback_cfg: dict[str, Any], *, final_content: str, final_format: str) -> None:
    url = str(callback_cfg.get("callback_url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return
    task_id = str(callback_cfg.get("task_id") or "")
    context_id = str(callback_cfg.get("context_id") or task_id)
    metadata = callback_cfg.get("metadata") if isinstance(callback_cfg.get("metadata"), dict) else {}
    token = str(callback_cfg.get("callback_token") or "").strip()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-SoulSearcher-Callback-Token"] = token
    if final_content:
        callback_outbox.enqueue(
            settings,
            url=url,
            payload={
                "event": "task.artifact_update",
                "task_id": task_id,
                "context_id": context_id,
                "status": "working",
                "artifact": {
                    "artifact_id": "final-report",
                    "name": "Research Report",
                    "format": final_format or "markdown",
                    "parts": [{"text": final_content, "mediaType": f"text/{final_format or 'markdown'}"}],
                },
                "metadata": metadata,
                "updated_at": _now_iso(),
            },
            headers=headers,
            max_attempts=max(1, int(getattr(settings, "a2a_callback_retry_attempts", 2) or 2)),
        )
    callback_outbox.enqueue(
        settings,
        url=url,
        payload={
            "event": "task.completed",
            "task_id": task_id,
            "context_id": context_id,
            "status": "completed",
            "message": "Research completed.",
            "metadata": metadata,
            "updated_at": _now_iso(),
        },
        headers=headers,
        max_attempts=max(1, int(getattr(settings, "a2a_callback_retry_attempts", 2) or 2)),
    )
    await callback_outbox.dispatch_ready(settings, limit=20)


async def _deliver_a2a_failure_callback(callback_cfg: dict[str, Any], *, error: str, seq: int) -> None:
    url = str(callback_cfg.get("callback_url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return
    task_id = str(callback_cfg.get("task_id") or "")
    context_id = str(callback_cfg.get("context_id") or task_id)
    metadata = callback_cfg.get("metadata") if isinstance(callback_cfg.get("metadata"), dict) else {}
    token = str(callback_cfg.get("callback_token") or "").strip()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-SoulSearcher-Callback-Token"] = token
    callback_outbox.enqueue(
        settings,
        url=url,
        payload={
            "event": "task.failed",
            "task_id": task_id,
            "context_id": context_id,
            "status": "failed",
            "error": {
                "code": "deep_research_failed",
                "message": error,
                "stage": "temporal_activity",
                "retryable": True,
                "last_event_seq": seq,
            },
            "metadata": metadata,
            "updated_at": _now_iso(),
        },
        headers=headers,
        max_attempts=max(1, int(getattr(settings, "a2a_callback_retry_attempts", 2) or 2)),
    )
    await callback_outbox.dispatch_ready(settings, limit=20)


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


try:
    from temporalio import activity, workflow

    _WORKFLOW_ACTIVITY_TIMEOUT = timedelta(seconds=int(getattr(settings, "temporal_workflow_timeout_seconds", 21600) or 21600))
    _SHORT_ACTIVITY_TIMEOUT = timedelta(seconds=300)

    @activity.defn(name="run_deep_research_activity")
    async def temporal_run_deep_research_activity(request_payload: dict[str, Any]) -> dict[str, Any]:
        return await run_background_activity(request_payload)

    @activity.defn(name="initialize_run_activity")
    async def temporal_initialize_run_activity(request_payload: dict[str, Any]) -> dict[str, Any]:
        return await initialize_run_activity(request_payload)

    @activity.defn(name="build_brief_activity")
    async def temporal_build_brief_activity(request_payload: dict[str, Any]) -> dict[str, Any]:
        return await build_brief_activity(request_payload)

    @activity.defn(name="plan_or_wait_hitl_activity")
    async def temporal_plan_or_wait_hitl_activity(request_payload: dict[str, Any]) -> dict[str, Any]:
        return await plan_or_wait_hitl_activity(request_payload)

    @activity.defn(name="execute_plan_tasks_activity")
    async def temporal_execute_plan_tasks_activity(request_payload: dict[str, Any]) -> dict[str, Any]:
        return await execute_plan_tasks_activity(request_payload)

    @activity.defn(name="write_report_activity")
    async def temporal_write_report_activity(payload: dict[str, Any]) -> dict[str, Any]:
        return await write_report_activity(payload)

    @activity.defn(name="quality_check_activity")
    async def temporal_quality_check_activity(payload: dict[str, Any]) -> dict[str, Any]:
        return await quality_check_activity(payload)

    @activity.defn(name="publish_artifacts_activity")
    async def temporal_publish_artifacts_activity(payload: dict[str, Any]) -> dict[str, Any]:
        return await publish_artifacts_activity(payload)

    @activity.defn(name="complete_callbacks_activity")
    async def temporal_complete_callbacks_activity(payload: dict[str, Any]) -> dict[str, Any]:
        return await complete_callbacks_activity(payload)

    @workflow.defn(name="DeepResearchBackgroundWorkflow")
    class DeepResearchBackgroundWorkflow:
        def __init__(self) -> None:
            self._resume_payloads: list[dict[str, Any]] = []
            self._cancel_reason = ""

        @workflow.run
        async def run(self, request_payload: dict[str, Any]) -> dict[str, Any]:
            payload = dict(request_payload or {})
            await workflow.execute_activity(
                "initialize_run_activity",
                payload,
                start_to_close_timeout=_SHORT_ACTIVITY_TIMEOUT,
                heartbeat_timeout=timedelta(seconds=60),
            )
            await workflow.execute_activity(
                "build_brief_activity",
                payload,
                start_to_close_timeout=_SHORT_ACTIVITY_TIMEOUT,
                heartbeat_timeout=timedelta(seconds=60),
            )
            plan_result = await workflow.execute_activity(
                "plan_or_wait_hitl_activity",
                payload,
                start_to_close_timeout=_SHORT_ACTIVITY_TIMEOUT,
                heartbeat_timeout=timedelta(seconds=60),
            )
            if isinstance(plan_result, dict) and plan_result.get("status") == RunStatus.waiting_for_input.value:
                await workflow.wait_condition(lambda: bool(self._resume_payloads) or bool(self._cancel_reason))
                if self._cancel_reason:
                    return {"status": RunStatus.cancelled.value, "reason": self._cancel_reason}
                payload["resume_payload"] = self._resume_payloads[-1]
                await workflow.execute_activity(
                    "plan_or_wait_hitl_activity",
                    payload,
                    start_to_close_timeout=_SHORT_ACTIVITY_TIMEOUT,
                    heartbeat_timeout=timedelta(seconds=60),
                )
            execute_result = await workflow.execute_activity(
                "execute_plan_tasks_activity",
                payload,
                start_to_close_timeout=_WORKFLOW_ACTIVITY_TIMEOUT,
                heartbeat_timeout=timedelta(seconds=120),
            )
            stage_payload = {"request": payload, "previous": execute_result}
            report_result = await workflow.execute_activity(
                "write_report_activity",
                stage_payload,
                start_to_close_timeout=_SHORT_ACTIVITY_TIMEOUT,
                heartbeat_timeout=timedelta(seconds=60),
            )
            quality_result = await workflow.execute_activity(
                "quality_check_activity",
                {"request": payload, "previous": report_result},
                start_to_close_timeout=_SHORT_ACTIVITY_TIMEOUT,
                heartbeat_timeout=timedelta(seconds=60),
            )
            artifact_result = await workflow.execute_activity(
                "publish_artifacts_activity",
                {"request": payload, "previous": quality_result},
                start_to_close_timeout=_SHORT_ACTIVITY_TIMEOUT,
                heartbeat_timeout=timedelta(seconds=60),
            )
            return await workflow.execute_activity(
                "complete_callbacks_activity",
                {"request": payload, "previous": artifact_result},
                start_to_close_timeout=_SHORT_ACTIVITY_TIMEOUT,
                heartbeat_timeout=timedelta(seconds=60),
            )

        @workflow.signal
        async def resume_requested(self, payload: dict[str, Any]) -> None:
            self._resume_payloads.append(payload)

        @workflow.signal
        async def cancel_requested(self, reason: str) -> None:
            self._cancel_reason = reason

    @workflow.defn(name="DeepResearchBackgroundWorkflowV1")
    class DeepResearchBackgroundWorkflowV1:
        @workflow.run
        async def run(self, request_payload: dict[str, Any]) -> dict[str, Any]:
            return await workflow.execute_activity(
                "run_deep_research_activity",
                request_payload,
                start_to_close_timeout=_WORKFLOW_ACTIVITY_TIMEOUT,
                heartbeat_timeout=timedelta(seconds=120),
            )

        @workflow.signal
        async def resume_requested(self, payload: dict[str, Any]) -> None:
            del payload

        @workflow.signal
        async def cancel_requested(self, reason: str) -> None:
            del reason

except Exception:  # pragma: no cover - temporalio is optional for local/test mode
    temporal_run_deep_research_activity = None  # type: ignore[assignment]
    temporal_initialize_run_activity = None  # type: ignore[assignment]
    temporal_build_brief_activity = None  # type: ignore[assignment]
    temporal_plan_or_wait_hitl_activity = None  # type: ignore[assignment]
    temporal_execute_plan_tasks_activity = None  # type: ignore[assignment]
    temporal_write_report_activity = None  # type: ignore[assignment]
    temporal_quality_check_activity = None  # type: ignore[assignment]
    temporal_publish_artifacts_activity = None  # type: ignore[assignment]
    temporal_complete_callbacks_activity = None  # type: ignore[assignment]
    DeepResearchBackgroundWorkflow = None  # type: ignore[assignment]
    DeepResearchBackgroundWorkflowV1 = None  # type: ignore[assignment]
