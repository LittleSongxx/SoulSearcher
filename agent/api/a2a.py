from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.request_handlers.default_request_handler import (
    SimpleRequestContextBuilder,
)
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import TaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    ListTasksRequest,
    ListTasksResponse,
    Message,
    Part,
    Task,
    TaskState,
    TaskStatus,
)
from a2a.utils.constants import PROTOCOL_VERSION_1_0, TransportProtocol
from fastapi import FastAPI
from google.protobuf import json_format

from agent.runtime.idempotency import (
    IdempotencyConflictError,
    IdempotencyRecord,
    canonical_request_hash,
    idempotency_store,
)
from agent.runtime.callback_outbox import callback_outbox, deliver_callback_once
from agent.runtime.background_runs import BackgroundRunRequest, background_run_manager
from agent.runtime.runs import RunStatus, run_manager
from agent.runtime.temporal_runs import signal_temporal_resume, temporal_backend_enabled
from common.cancellation import cancellation_manager
from common.stream_translate import data_stream_line_to_payload
from common.thread_ownership import set_thread_owner

logger = logging.getLogger(__name__)

StreamFactory = Callable[..., AsyncIterator[str]]
ResumeFactory = Callable[..., AsyncIterator[str]]

_DEFAULT_DEEP_SEARCH_MODE = {
    "mode": "deep",
    "useWebSearch": True,
    "useAgent": True,
    "useDeepSearch": True,
}
_PROGRESS_EVENTS = {
    "brief_created",
    "progress",
    "thinking",
    "search",
    "sources",
    "tool",
    "task_create",
    "task_update",
    "research_node_start",
    "research_node_complete",
    "research_tree_update",
}
_ARTIFACT_EVENTS = {"artifact", "completion", "report_written"}
_INTERRUPTED_STATES = {
    "TASK_STATE_INPUT_REQUIRED",
    "TASK_STATE_AUTH_REQUIRED",
}
_TERMINAL_STATES = {
    "TASK_STATE_COMPLETED",
    "TASK_STATE_CANCELED",
    "TASK_STATE_FAILED",
    "TASK_STATE_REJECTED",
}


def mount_a2a_routes(
    app: FastAPI,
    *,
    settings: Any,
    stream_factory: StreamFactory,
    resume_factory: ResumeFactory | None = None,
) -> AgentCard:
    """Mount SoulSearcher's A2A 1.0 JSON-RPC server routes on the FastAPI app."""
    agent_card = build_agent_card(settings)
    task_store = RunManagerA2ATaskStore(settings=settings)
    executor = SoulSearcherA2AExecutor(
        settings=settings,
        stream_factory=stream_factory,
        resume_factory=resume_factory,
    )
    handler = SoulSearcherA2ARequestHandler(
        agent_executor=executor,
        task_store=task_store,
        agent_card=agent_card,
        request_context_builder=IdempotentA2ARequestContextBuilder(task_store=task_store),
    )
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(agent_card),
        jsonrpc_routes=create_jsonrpc_routes(
            handler,
            rpc_url="/api/a2a",
            enable_v0_3_compat=False,
        ),
    )
    return agent_card


class RunManagerA2ATaskStore(TaskStore):
    """Persist A2A task snapshots through SoulSearcher's durable run registry."""

    def __init__(self, *, settings: Any) -> None:
        self.settings = settings

    async def save(self, task: Task, context: ServerCallContext) -> None:
        del context
        task_payload = _task_to_dict(task)
        metadata = _struct_to_dict(task.metadata)
        existing = run_manager.get(task.id)
        status_name = _task_state_name(task)
        run_status = _run_status_from_a2a(status_name)
        update_payload = {
            "a2a": True,
            "a2a_task_id": task.id,
            "a2a_context_id": task.context_id,
            "a2a_status": status_name,
            "a2a_task": task_payload,
            "client_request_id": _client_request_id(metadata),
            "idempotency_key": _idempotency_key(metadata),
            "a2a_request_hash": _a2a_request_hash_from_metadata(metadata),
            "thread_id": task.id,
            "run_id": task.id,
            "stalled": bool(metadata.get("stalled", False)),
        }
        update_payload = {
            key: value
            for key, value in update_payload.items()
            if value is not None and value != ""
        }
        if existing is None:
            record = run_manager.start(
                run_id=task.id,
                thread_id=task.id,
                route="a2a.deep-research",
                user_id=str(metadata.get("user_id") or ""),
                metadata=update_payload,
            )
            if record.status != run_status:
                run_manager.update(task.id, status=run_status)
        else:
            run_manager.update(task.id, status=run_status, metadata=update_payload)
        event = run_manager.append_event(
            run_id=task.id,
            thread_id=task.id,
            seq=0,
            type="a2a.task_snapshot",
            payload={"task": task_payload, "status": status_name},
            status=status_name,
        )
        run_manager.update(task.id, metadata={"last_event_seq": int(event.get("seq") or 0)})
        _complete_a2a_idempotency_from_metadata(
            metadata,
            scope="a2a.start",
            response={
                "task_id": task.id,
                "context_id": task.context_id,
                "status": status_name,
                "last_event_seq": int(event.get("seq") or 0),
            },
        )

    async def get(self, task_id: str, context: ServerCallContext) -> Task | None:
        del context
        record = run_manager.get(str(task_id or ""))
        if record is None:
            return None
        task = _task_from_record(record.to_dict())
        if task is None:
            return None
        if self._should_mark_stalled(record.to_dict(), task):
            _merge_task_metadata(task, {"stalled": True})
            await self.save(task, ServerCallContext(state={}))
            await self._deliver_callback(
                task,
                event="task.stalled",
                error=_error_envelope(
                    {"message": "A2A task has not emitted progress before the stalled timeout."},
                    task_id=task.id,
                    seq=_record_last_seq(record.to_dict()),
                    stage="watchdog",
                    retryable=True,
                ),
            )
        return task

    async def list(
        self,
        params: ListTasksRequest,
        context: ServerCallContext,
    ) -> ListTasksResponse:
        del context
        tasks: list[Task] = []
        for record in run_manager.all():
            task = _task_from_record(record)
            if task is None:
                continue
            if getattr(params, "context_id", "") and task.context_id != params.context_id:
                continue
            if getattr(params, "status", 0) and task.status.state != params.status:
                continue
            tasks.append(task)
        page_size = int(getattr(params, "page_size", 0) or 100)
        page_size = max(1, min(page_size, 500))
        return ListTasksResponse(tasks=tasks[:page_size], total_size=len(tasks), page_size=page_size)

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        del context
        run_manager.update(str(task_id or ""), metadata={"a2a_deleted": True})

    def find_task_by_idempotency(self, metadata: dict[str, Any], *, request_hash: str = "") -> Task | None:
        client_request_id = _client_request_id(metadata)
        idempotency_key = _idempotency_key(metadata)
        if not client_request_id and not idempotency_key:
            return None
        for record in run_manager.all():
            record_meta = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
            if not record_meta.get("a2a"):
                continue
            if client_request_id and client_request_id == record_meta.get("client_request_id"):
                _raise_if_a2a_hash_conflicts(record_meta, request_hash)
                return _task_from_record(record)
            if idempotency_key and idempotency_key == record_meta.get("idempotency_key"):
                _raise_if_a2a_hash_conflicts(record_meta, request_hash)
                return _task_from_record(record)
        return None

    async def _deliver_callback(
        self,
        task: Task,
        *,
        event: str,
        error: dict[str, Any] | None = None,
    ) -> None:
        metadata = _struct_to_dict(task.metadata)
        await _deliver_callback(self.settings, metadata, event=event, task=task, error=error or {})

    def _should_mark_stalled(self, record: dict[str, Any], task: Task) -> bool:
        status_name = _task_state_name(task)
        if status_name in _TERMINAL_STATES | _INTERRUPTED_STATES:
            return False
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        if bool(metadata.get("stalled")):
            return False
        timeout = float(getattr(self.settings, "a2a_stalled_timeout_seconds", 0) or 0)
        if timeout <= 0:
            return False
        try:
            updated_at = datetime.fromisoformat(str(record.get("updated_at") or "").replace("Z", "+00:00"))
        except Exception:
            return False
        return (datetime.now(UTC) - updated_at).total_seconds() > timeout


class IdempotentA2ARequestContextBuilder(SimpleRequestContextBuilder):
    def __init__(self, *, task_store: RunManagerA2ATaskStore) -> None:
        super().__init__(should_populate_referred_tasks=False, task_store=task_store)
        self._persistent_task_store = task_store

    async def build(
        self,
        context: ServerCallContext,
        params=None,
        task_id: str | None = None,
        context_id: str | None = None,
        task: Task | None = None,
    ) -> RequestContext:
        if params is not None and not task_id and not getattr(params.message, "task_id", ""):
            metadata = _metadata_from_send_params(params)
            request_hash = _a2a_request_hash_from_params(params)
            _merge_message_metadata(getattr(params, "message", None), {"a2a_request_hash": request_hash})
            existing = self._persistent_task_store.find_task_by_idempotency(metadata, request_hash=request_hash)
            if existing is not None:
                task_id = existing.id
                context_id = existing.context_id
                task = existing
        return await super().build(
            context=context,
            params=params,
            task_id=task_id,
            context_id=context_id,
            task=task,
        )


class SoulSearcherA2ARequestHandler(DefaultRequestHandler):
    def __init__(self, *args: Any, task_store: RunManagerA2ATaskStore, **kwargs: Any) -> None:
        super().__init__(*args, task_store=task_store, **kwargs)
        self._persistent_task_store = task_store

    async def on_message_send(self, params, context: ServerCallContext):
        replay = self._idempotent_replay(params)
        if replay is not None:
            return replay
        return await super().on_message_send(params, context)

    async def on_message_send_stream(
        self,
        params,
        context: ServerCallContext,
    ) -> AsyncGenerator[Any, None]:
        replay = self._idempotent_replay(params)
        if replay is not None:
            yield replay
            return
        async for item in super().on_message_send_stream(params, context):
            yield item

    def _idempotent_replay(self, params: Any) -> Task | None:
        message = getattr(params, "message", None)
        if message is None or getattr(message, "task_id", ""):
            return None
        metadata = _metadata_from_send_params(params)
        request_hash = _a2a_request_hash_from_params(params)
        _merge_message_metadata(message, {"a2a_request_hash": request_hash})
        record = _begin_a2a_idempotency(
            metadata,
            scope="a2a.start",
            request_hash=request_hash,
        )
        if record is not None and record.status == "completed" and record.response:
            task_id = str(record.response.get("task_id") or "")
            if task_id:
                task = _task_from_record(run_manager.get(task_id).to_dict()) if run_manager.get(task_id) else None
                if task is not None:
                    return task
        return self._persistent_task_store.find_task_by_idempotency(metadata, request_hash=request_hash)


def build_agent_card(settings: Any) -> AgentCard:
    base_url = public_base_url(settings)
    return AgentCard(
        name="SoulSearcher DeepResearch",
        description=(
            "Evidence-driven DeepResearch agent that plans, researches, "
            "synthesizes, and returns cited report artifacts."
        ),
        supported_interfaces=[
            AgentInterface(
                url=f"{base_url}/api/a2a",
                protocol_binding=TransportProtocol.JSONRPC.value,
                protocol_version=PROTOCOL_VERSION_1_0,
            )
        ],
        provider=AgentProvider(organization="SoulSearcher", url=base_url),
        version="1.0.0",
        documentation_url=f"{base_url}/",
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=False,
            extended_agent_card=False,
        ),
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/markdown", "text/html", "application/json"],
        skills=[
            AgentSkill(
                id="deep-research",
                name="Deep Research",
                description=(
                    "Conduct systematic multi-angle research with evidence, "
                    "quality checks, and final report artifacts."
                ),
                tags=["research", "deep-research", "web-research", "evidence"],
                input_modes=["text/plain", "application/json"],
                output_modes=["text/markdown", "text/html", "application/json"],
            )
        ],
    )


def public_base_url(settings: Any) -> str:
    configured = str(getattr(settings, "public_base_url", "") or "").strip()
    if configured:
        return configured.rstrip("/")
    port = int(getattr(settings, "port", 8001) or 8001)
    return f"http://127.0.0.1:{port}"


class SoulSearcherA2AExecutor(AgentExecutor):
    def __init__(
        self,
        *,
        settings: Any,
        stream_factory: StreamFactory,
        resume_factory: ResumeFactory | None = None,
    ) -> None:
        self.settings = settings
        self.stream_factory = stream_factory
        self.resume_factory = resume_factory

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = str(context.task_id or f"a2a_{uuid.uuid4().hex}")
        context_id = str(context.context_id or task_id)
        updater = TaskUpdater(event_queue, task_id, context_id)

        query = (context.get_user_input() or "").strip()
        metadata = _merged_metadata(context)
        options = _dict_value(metadata.get("options"))
        request_context = _dict_value(metadata.get("context"))
        base_task_metadata = _base_task_metadata(metadata, task_id=task_id, context_id=context_id)
        is_resume = _is_resume_request(task_id)

        await _enqueue_initial_task(
            event_queue,
            task_id,
            context_id,
            context.message,
            metadata=base_task_metadata,
        )

        if not query:
            await updater.reject(
                _agent_message(updater, "A DeepResearch query is required.")
            )
            return

        try:
            user_id = _resolve_user_id(
                self.settings,
                context,
                metadata,
                options,
                request_context,
            )
        except ValueError as exc:
            await updater.reject(_agent_message(updater, str(exc)))
            return
        owner_id = user_id or "a2a"
        base_task_metadata["user_id"] = owner_id
        set_thread_owner(task_id, owner_id)
        resume_payload: dict[str, Any] = {}
        resume_idempotency_record: IdempotencyRecord | None = None
        if is_resume:
            resume_payload = _resume_payload_from_message(query, metadata)
            resume_hash = canonical_request_hash({"task_id": task_id, "resume_payload": resume_payload})
            _merge_message_metadata(
                context.message,
                {
                    "a2a_resume_request_hash": resume_hash,
                    "resume_payload": resume_payload,
                },
            )
            resume_idempotency_record = _begin_a2a_idempotency(
                metadata,
                scope="a2a.resume",
                request_hash=resume_hash,
                user_id=owner_id,
            )
            if resume_idempotency_record is not None and resume_idempotency_record.status == "completed":
                existing = run_manager.get(task_id)
                replay_task = _task_from_record(existing.to_dict()) if existing is not None else None
                if replay_task is not None:
                    await event_queue.enqueue_event(replay_task)
                    return

        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            _agent_message(
                updater,
                "DeepResearch resume accepted." if is_resume else "DeepResearch task accepted.",
            ),
            metadata={**base_task_metadata, "event_type": "resume" if is_resume else "accepted"},
        )

        deepsearch_config = _dict_value(metadata.get("deepsearch_config"))
        deepsearch_config.update(_dict_value(options.get("deepsearch_config")))
        retrieval_policy = (
            _dict_value(metadata.get("retrieval_policy"))
            or _dict_value(options.get("retrieval_policy"))
            or _dict_value(request_context.get("retrieval_policy"))
        )
        if retrieval_policy:
            deepsearch_config["retrieval_policy"] = retrieval_policy

        research_brief = (
            _dict_value(metadata.get("research_brief"))
            or _dict_value(options.get("research_brief"))
            or _dict_value(request_context.get("research_brief"))
        )
        skill_ids = _list_value(metadata.get("skill_ids") or options.get("skill_ids"))
        if skill_ids:
            deepsearch_config["skill_ids"] = skill_ids

        search_mode = (
            _dict_value(metadata.get("search_mode"))
            or _dict_value(options.get("search_mode"))
            or dict(_DEFAULT_DEEP_SEARCH_MODE)
        )
        images = _list_of_dicts(options.get("images") or metadata.get("images"))
        if not images:
            images = _list_of_dicts(metadata.get("files") or options.get("files"))

        model = str(
            options.get("model")
            or metadata.get("model")
            or getattr(self.settings, "primary_model", "")
            or ""
        ).strip()

        final_content = ""
        final_format = "markdown"
        final_artifact_sent = False
        terminal_sent = False
        seq = 0

        if temporal_backend_enabled() and is_resume:
            await signal_temporal_resume(task_id, resume_payload or _resume_payload_from_message(query, metadata))
            await updater.update_status(
                TaskState.TASK_STATE_WORKING,
                _agent_message(
                    updater,
                    "DeepResearch resume accepted and sent to the background workflow.",
                    metadata={"event_type": "background_resume", "resume_payload": resume_payload},
                ),
                metadata={**base_task_metadata, "event_type": "background_resume"},
            )
            await _deliver_callback(
                self.settings,
                base_task_metadata,
                event="task.status_update",
                task_id=task_id,
                context_id=context_id,
                status="working",
                message="DeepResearch resume accepted and sent to the background workflow.",
            )
            _complete_a2a_idempotency_record(
                resume_idempotency_record,
                response={"task_id": task_id, "context_id": context_id, "status": "TASK_STATE_WORKING", "resumed": True},
            )
            return

        if temporal_backend_enabled() and not is_resume:
            deepsearch_config.update(
                {
                    "execution_mode": base_task_metadata.get("execution_mode") or "background",
                    "return_immediately": bool(base_task_metadata.get("return_immediately", True)),
                    "idempotency_key": base_task_metadata.get("idempotency_key") or "",
                    "client_request_id": base_task_metadata.get("client_request_id") or "",
                    "soulclaw_task_id": base_task_metadata.get("soulclaw_task_id") or task_id,
                }
            )
            deepsearch_config["a2a_callback"] = {
                "callback_url": base_task_metadata.get("callback_url") or "",
                "callback_token": base_task_metadata.get("callback_token") or "",
                "callback_token_id": base_task_metadata.get("callback_token_id") or "",
                "metadata": {
                    "client_request_id": base_task_metadata.get("client_request_id") or "",
                    "soulclaw_task_id": base_task_metadata.get("soulclaw_task_id") or task_id,
                    "callback_token_id": base_task_metadata.get("callback_token_id") or "",
                },
                "task_id": task_id,
                "context_id": context_id,
            }
            status = await background_run_manager.submit(
                BackgroundRunRequest(
                    input_text=query,
                    thread_id=task_id,
                    model=model or None,
                    search_mode=search_mode,
                    images=images,
                    user_id=user_id,
                    deepsearch_config=deepsearch_config,
                    research_brief=research_brief or None,
                ),
                stream_factory=self.stream_factory,
            )
            await updater.update_status(
                TaskState.TASK_STATE_WORKING,
                _agent_message(
                    updater,
                    "DeepResearch task accepted and is running in the background.",
                    metadata={"event_type": "background_accepted", "run": status},
                ),
                metadata={**base_task_metadata, "event_type": "background_accepted", "run": status},
            )
            await _deliver_callback(
                self.settings,
                base_task_metadata,
                event="task.status_update",
                task_id=task_id,
                context_id=context_id,
                status="working",
                message="DeepResearch task accepted and is running in the background.",
            )
            _complete_a2a_idempotency_record(
                resume_idempotency_record,
                response={"task_id": task_id, "context_id": context_id, "status": "TASK_STATE_WORKING"},
            )
            return

        try:
            if is_resume:
                if self.resume_factory is None:
                    raise RuntimeError("A2A task is waiting for input but resume_factory is not configured")
                line_iter = self.resume_factory(
                    resume_payload or _resume_payload_from_message(query, metadata),
                    thread_id=task_id,
                    run_id=task_id,
                    model=model or None,
                    search_mode=search_mode,
                    user_id=user_id,
                )
            else:
                line_iter = self.stream_factory(
                    query,
                    thread_id=task_id,
                    run_id=task_id,
                    model=model or None,
                    search_mode=search_mode,
                    images=images,
                    user_id=user_id,
                    request=None,
                    deepsearch_config=deepsearch_config,
                    research_brief=research_brief or None,
                )

            async for line in line_iter:
                if not isinstance(line, str) or line.startswith(":"):
                    continue
                seq += 1
                payload = data_stream_line_to_payload(line, seq=seq)
                if not payload:
                    continue
                event_type = str(payload.get("type") or "").strip()
                data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
                await _record_a2a_stream_event(task_id, seq=seq, event_type=event_type, payload=payload)

                if event_type in _ARTIFACT_EVENTS:
                    content = _extract_content(data)
                    if content:
                        previous_final_content = final_content
                        if (
                            event_type == "artifact"
                            and final_artifact_sent
                            and content == previous_final_content
                        ):
                            continue
                        final_content = content
                        final_format = str(data.get("format") or final_format or "markdown")
                        artifact_id = str(
                            data.get("id")
                            or data.get("artifact_id")
                            or data.get("artifactId")
                            or (
                                "final-report"
                                if event_type in {"completion", "report_written"}
                                else f"artifact-{seq}"
                            )
                        )
                        await updater.add_artifact(
                            [_report_part(content, final_format)],
                            artifact_id=artifact_id,
                            name=str(data.get("title") or data.get("name") or "Research Report"),
                            metadata={
                                "source_event_type": event_type,
                                "format": final_format,
                                "a2a_task_id": task_id,
                                "sequence": seq,
                            },
                            last_chunk=True,
                        )
                        await _deliver_callback(
                            self.settings,
                            base_task_metadata,
                            event="task.artifact_update",
                            task_id=task_id,
                            context_id=context_id,
                            artifact={
                                "artifact_id": artifact_id,
                                "name": str(data.get("title") or data.get("name") or "Research Report"),
                                "format": final_format,
                            },
                        )
                        if event_type in {"completion", "report_written"}:
                            final_artifact_sent = True
                    continue

                if event_type == "interrupt":
                    terminal_sent = True
                    hitl = _hitl_payload(data, task_id=task_id, context_id=context_id, seq=seq)
                    await updater.update_status(
                        TaskState.TASK_STATE_INPUT_REQUIRED,
                        _agent_message(
                            updater,
                            _extract_content(data) or "Research requires input before continuing.",
                            metadata={"hitl": hitl},
                        ),
                        metadata={**base_task_metadata, "event_type": "interrupt", "hitl": hitl},
                    )
                    await _deliver_callback(
                        self.settings,
                        base_task_metadata,
                        event="task.input_required",
                        task_id=task_id,
                        context_id=context_id,
                        hitl=hitl,
                    )
                    _complete_a2a_idempotency_record(
                        resume_idempotency_record,
                        response={"task_id": task_id, "context_id": context_id, "status": "TASK_STATE_INPUT_REQUIRED"},
                    )
                    return

                if event_type == "error":
                    terminal_sent = True
                    error = _error_envelope(data, task_id=task_id, seq=seq, stage=str(data.get("stage") or "research"))
                    await updater.update_status(
                        TaskState.TASK_STATE_FAILED,
                        _agent_message(
                            updater,
                            _extract_content(data) or "Research failed.",
                            metadata={"error": error},
                        ),
                        metadata={**base_task_metadata, "event_type": "error", "error": error},
                    )
                    await _deliver_callback(
                        self.settings,
                        base_task_metadata,
                        event="task.failed",
                        task_id=task_id,
                        context_id=context_id,
                        error=error,
                    )
                    _complete_a2a_idempotency_record(
                        resume_idempotency_record,
                        response={"task_id": task_id, "context_id": context_id, "status": "TASK_STATE_FAILED", "error": error},
                    )
                    return

                if event_type in {"cancelled", "canceled"}:
                    terminal_sent = True
                    await updater.update_status(
                        TaskState.TASK_STATE_CANCELED,
                        _agent_message(updater, _extract_content(data) or "Research canceled."),
                        metadata={**base_task_metadata, "event_type": "cancelled"},
                    )
                    await _deliver_callback(
                        self.settings,
                        base_task_metadata,
                        event="task.canceled",
                        task_id=task_id,
                        context_id=context_id,
                    )
                    _complete_a2a_idempotency_record(
                        resume_idempotency_record,
                        response={"task_id": task_id, "context_id": context_id, "status": "TASK_STATE_CANCELED"},
                    )
                    return

                if event_type == "done":
                    terminal_sent = True
                    await updater.update_status(
                        TaskState.TASK_STATE_COMPLETED,
                        _agent_message(updater, final_content or "Research completed."),
                        metadata={**base_task_metadata, "event_type": "done"},
                    )
                    await _deliver_callback(
                        self.settings,
                        base_task_metadata,
                        event="task.completed",
                        task_id=task_id,
                        context_id=context_id,
                    )
                    _complete_a2a_idempotency_record(
                        resume_idempotency_record,
                        response={"task_id": task_id, "context_id": context_id, "status": "TASK_STATE_COMPLETED"},
                    )
                    return

                if event_type in _PROGRESS_EVENTS:
                    progress = _progress_text(event_type, data)
                    if progress:
                        await updater.update_status(
                            TaskState.TASK_STATE_WORKING,
                            _agent_message(updater, progress, metadata={"event_type": event_type}),
                            metadata={**base_task_metadata, "event_type": event_type, "last_event_seq": seq},
                        )
                        await _deliver_callback(
                            self.settings,
                            base_task_metadata,
                            event="task.status_update",
                            task_id=task_id,
                            context_id=context_id,
                            status="working",
                            message=progress,
                        )

            if not terminal_sent:
                await updater.update_status(
                    TaskState.TASK_STATE_COMPLETED,
                    _agent_message(updater, final_content or "Research completed."),
                    metadata={**base_task_metadata, "event_type": "done"},
                )
                _complete_a2a_idempotency_record(
                    resume_idempotency_record,
                    response={"task_id": task_id, "context_id": context_id, "status": "TASK_STATE_COMPLETED"},
                )
        except asyncio.CancelledError:
            with suppress(Exception):
                await cancellation_manager.cancel(task_id, "A2A task cancelled")
                await updater.update_status(
                    TaskState.TASK_STATE_CANCELED,
                    _agent_message(updater, "Research canceled."),
                    metadata={**base_task_metadata, "event_type": "cancelled"},
                )
                _complete_a2a_idempotency_record(
                    resume_idempotency_record,
                    response={"task_id": task_id, "context_id": context_id, "status": "TASK_STATE_CANCELED"},
                )
            raise
        except Exception as exc:
            logger.exception("[A2A] DeepResearch task failed: %s", task_id)
            with suppress(Exception):
                error = _error_envelope(
                    {"message": str(exc)},
                    task_id=task_id,
                    seq=seq,
                    stage="a2a_executor",
                    retryable=True,
                )
                await updater.update_status(
                    TaskState.TASK_STATE_FAILED,
                    _agent_message(updater, str(exc), metadata={"error": error}),
                    metadata={**base_task_metadata, "event_type": "error", "error": error},
                )
                await _deliver_callback(
                    self.settings,
                    base_task_metadata,
                    event="task.failed",
                    task_id=task_id,
                    context_id=context_id,
                    error=error,
                )
                _complete_a2a_idempotency_record(
                    resume_idempotency_record,
                    response={"task_id": task_id, "context_id": context_id, "status": "TASK_STATE_FAILED", "error": error},
                )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = str(context.task_id or "")
        context_id = str(context.context_id or task_id or f"a2a_{uuid.uuid4().hex}")
        updater = TaskUpdater(event_queue, task_id or context_id, context_id)
        await _enqueue_initial_task(
            event_queue,
            task_id or context_id,
            context_id,
            context.message,
        )
        if task_id:
            await cancellation_manager.cancel(task_id, "A2A client requested cancellation")
        await updater.cancel(_agent_message(updater, "Research canceled."))


def _merged_metadata(context: RequestContext) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    request_metadata = _dict_value(getattr(context, "metadata", {}))
    metadata.update(request_metadata)
    message = context.message
    if message is not None:
        metadata.update(_struct_to_dict(message.metadata))
    return metadata


def _resolve_user_id(
    settings: Any,
    context: RequestContext,
    metadata: dict[str, Any],
    options: dict[str, Any],
    request_context: dict[str, Any],
) -> str:
    headers = _dict_value(context.call_context.state.get("headers"))
    auth_header = str(
        getattr(settings, "auth_user_header", "") or "X-SoulSearcher-User"
    )
    principal_id = _header_value(headers, auth_header)
    internal_key = str(getattr(settings, "internal_api_key", "") or "").strip()
    explicit = str(
        request_context.get("user_id")
        or options.get("user_id")
        or metadata.get("user_id")
        or ""
    ).strip()
    if internal_key:
        if principal_id:
            if explicit and explicit != principal_id:
                raise ValueError("A2A user_id does not match trusted user header")
            return principal_id
        return "internal"
    if explicit:
        return explicit
    return str(getattr(settings, "memory_user_id", "") or "default").strip() or "default"


def _agent_message(
    updater: TaskUpdater,
    text: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> Message:
    return updater.new_agent_message([Part(text=str(text or ""))], metadata=metadata)


async def _enqueue_initial_task(
    event_queue: EventQueue,
    task_id: str,
    context_id: str,
    message: Message | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    task = Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
        metadata=metadata or {},
    )
    if message is not None:
        task.history.append(message)
    await event_queue.enqueue_event(task)


def _task_to_dict(task: Task) -> dict[str, Any]:
    try:
        return json_format.MessageToDict(task)
    except Exception:
        return {"id": getattr(task, "id", ""), "contextId": getattr(task, "context_id", "")}


def _task_from_record(record: dict[str, Any]) -> Task | None:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    payload = metadata.get("a2a_task")
    if not isinstance(payload, dict):
        return None
    task = Task()
    try:
        json_format.ParseDict(payload, task)
    except Exception:
        return None
    return task


def _task_state_name(task: Task) -> str:
    try:
        return TaskState.Name(task.status.state)
    except Exception:
        return "TASK_STATE_WORKING"


def _run_status_from_a2a(state_name: str) -> RunStatus:
    if state_name == "TASK_STATE_COMPLETED":
        return RunStatus.completed
    if state_name == "TASK_STATE_FAILED":
        return RunStatus.failed
    if state_name == "TASK_STATE_CANCELED":
        return RunStatus.cancelled
    if state_name in _INTERRUPTED_STATES:
        return RunStatus.waiting_for_input
    if state_name == "TASK_STATE_SUBMITTED":
        return RunStatus.queued
    return RunStatus.running


def _merge_task_metadata(task: Task, values: dict[str, Any]) -> None:
    current = _struct_to_dict(task.metadata)
    current.update({key: value for key, value in values.items() if value is not None})
    task.metadata.Clear()
    task.metadata.update(current)


def _metadata_from_send_params(params: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    try:
        metadata.update(_struct_to_dict(params.metadata))
    except Exception:
        pass
    message = getattr(params, "message", None)
    if message is not None:
        try:
            metadata.update(_struct_to_dict(message.metadata))
        except Exception:
            pass
    return metadata


def _merge_message_metadata(message: Message | None, values: dict[str, Any]) -> None:
    if message is None:
        return
    clean = {key: value for key, value in values.items() if value is not None}
    try:
        current = _struct_to_dict(message.metadata)
        current.update(clean)
        message.metadata.Clear()
        message.metadata.update(current)
    except Exception:
        with suppress(Exception):
            message.metadata.update(clean)


def _message_fingerprint(message: Any) -> dict[str, Any]:
    if message is None:
        return {}
    return {
        "role": str(getattr(message, "role", "") or ""),
        "parts": _safe_proto_dict(getattr(message, "parts", [])),
    }


def _safe_proto_dict(value: Any) -> Any:
    try:
        return json_format.MessageToDict(value)
    except Exception:
        if isinstance(value, list):
            return [_safe_proto_dict(item) for item in value]
        if isinstance(value, dict):
            return value
        return str(value or "")


def _idempotency_relevant_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    ignored = {
        "callback_url",
        "callbackUrl",
        "callback_token",
        "callbackToken",
        "callback_token_id",
        "callbackTokenId",
        "a2a_request_hash",
        "a2a_resume_request_hash",
    }
    return {
        key: value
        for key, value in metadata.items()
        if key not in ignored and not str(key).lower().startswith("callback_")
    }


def _client_request_id(metadata: dict[str, Any]) -> str:
    return str(
        metadata.get("client_request_id")
        or metadata.get("clientRequestId")
        or metadata.get("soulclaw_task_id")
        or ""
    ).strip()


def _idempotency_key(metadata: dict[str, Any]) -> str:
    return str(
        metadata.get("idempotency_key")
        or metadata.get("idempotencyKey")
        or metadata.get("client_request_id")
        or metadata.get("clientRequestId")
        or ""
    ).strip()


def _idempotency_user_id(metadata: dict[str, Any], *, default: str = "a2a") -> str:
    options = _dict_value(metadata.get("options"))
    request_context = _dict_value(metadata.get("context"))
    return str(
        metadata.get("user_id")
        or metadata.get("userId")
        or options.get("user_id")
        or request_context.get("user_id")
        or default
    ).strip() or default


def _a2a_request_hash_from_metadata(metadata: dict[str, Any]) -> str:
    return str(
        metadata.get("a2a_request_hash")
        or metadata.get("request_hash")
        or metadata.get("requestHash")
        or ""
    ).strip()


def _raise_if_a2a_hash_conflicts(metadata: dict[str, Any], request_hash: str) -> None:
    stored_hash = str(metadata.get("a2a_request_hash") or "")
    if request_hash and stored_hash and stored_hash != request_hash:
        raise IdempotencyConflictError("Idempotency-Key was already used with a different A2A request payload.")


def _a2a_request_hash_from_params(params: Any) -> str:
    message = getattr(params, "message", None)
    metadata = _metadata_from_send_params(params)
    return canonical_request_hash(
        {
            "message": _message_fingerprint(message),
            "metadata": _idempotency_relevant_metadata(metadata),
            "configuration": _safe_proto_dict(getattr(params, "configuration", None)),
        }
    )


def _begin_a2a_idempotency(
    metadata: dict[str, Any],
    *,
    scope: str,
    request_hash: str,
    user_id: str = "",
) -> IdempotencyRecord | None:
    key = _idempotency_key(metadata)
    if not key:
        return None
    return idempotency_store.begin(
        key=key,
        scope=scope,
        user_id=user_id or _idempotency_user_id(metadata),
        request_hash=request_hash,
    )


def _complete_a2a_idempotency_record(record: IdempotencyRecord | None, *, response: dict[str, Any]) -> None:
    idempotency_store.complete(record, response=response)


def _complete_a2a_idempotency_from_metadata(
    metadata: dict[str, Any],
    *,
    scope: str,
    response: dict[str, Any],
) -> None:
    request_hash = _a2a_request_hash_from_metadata(metadata)
    key = _idempotency_key(metadata)
    if not key or not request_hash:
        return
    record = _begin_a2a_idempotency(metadata, scope=scope, request_hash=request_hash)
    _complete_a2a_idempotency_record(record, response=response)


def _base_task_metadata(metadata: dict[str, Any], *, task_id: str, context_id: str) -> dict[str, Any]:
    options = _dict_value(metadata.get("options"))
    request_context = _dict_value(metadata.get("context"))
    callback_url = str(
        metadata.get("callback_url")
        or metadata.get("callbackUrl")
        or options.get("callback_url")
        or request_context.get("callback_url")
        or ""
    ).strip()
    callback_token = str(
        metadata.get("callback_token")
        or metadata.get("callbackToken")
        or options.get("callback_token")
        or request_context.get("callback_token")
        or ""
    ).strip()
    execution_mode = str(
        metadata.get("execution_mode")
        or options.get("execution_mode")
        or request_context.get("execution_mode")
        or "background"
    ).strip()
    return_immediately = bool(
        metadata.get("return_immediately")
        if "return_immediately" in metadata
        else options.get("return_immediately", request_context.get("return_immediately", True))
    )
    return {
        "a2a": True,
        "task_id": task_id,
        "thread_id": task_id,
        "run_id": task_id,
        "context_id": context_id,
        "client_request_id": _client_request_id(metadata),
        "idempotency_key": _idempotency_key(metadata),
        "a2a_request_hash": _a2a_request_hash_from_metadata(metadata),
        "soulclaw_task_id": str(metadata.get("soulclaw_task_id") or request_context.get("soulclaw_task_id") or ""),
        "session_id": str(request_context.get("session_id") or metadata.get("session_id") or ""),
        "turn_id": str(request_context.get("turn_id") or metadata.get("turn_id") or ""),
        "capability": str(metadata.get("capability") or request_context.get("capability") or "deep-research"),
        "execution_mode": execution_mode or "background",
        "return_immediately": return_immediately,
        "callback_url": callback_url,
        "callback_token": callback_token,
        "callback_token_id": str(metadata.get("callback_token_id") or metadata.get("callbackTokenId") or ""),
        "stalled": False,
    }


def _is_resume_request(task_id: str) -> bool:
    record = run_manager.get(task_id)
    if record is None:
        return False
    metadata = record.metadata or {}
    state = str(metadata.get("a2a_status") or "")
    return state in _INTERRUPTED_STATES or record.status == RunStatus.waiting_for_input


def _resume_payload_from_message(query: str, metadata: dict[str, Any]) -> dict[str, Any]:
    for key in ("resume_payload", "resumePayload", "payload"):
        value = metadata.get(key)
        if isinstance(value, dict):
            return value
    if "decisions" in metadata and isinstance(metadata.get("decisions"), list):
        return {"decisions": metadata["decisions"]}
    if "tool_approved" in metadata:
        payload = {
            "tool_approved": bool(metadata.get("tool_approved")),
            "tool_calls": metadata.get("tool_calls") if isinstance(metadata.get("tool_calls"), list) else [],
        }
        if isinstance(metadata.get("message"), str):
            payload["message"] = metadata["message"]
        return payload
    stripped = str(query or "").strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    decision = str(metadata.get("decision") or metadata.get("action") or "").strip().lower()
    if decision:
        payload = {"action": decision}
        if stripped:
            payload["feedback"] = stripped
        return payload
    return {"action": "respond", "content": stripped}


def _hitl_payload(data: dict[str, Any], *, task_id: str, context_id: str, seq: int) -> dict[str, Any]:
    prompts = data.get("prompts") if isinstance(data.get("prompts"), list) else []
    first = prompts[0] if prompts and isinstance(prompts[0], dict) else {}
    allowed: set[str] = set()
    review_configs = data.get("review_configs") or first.get("review_configs")
    if isinstance(review_configs, list):
        for cfg in review_configs:
            if not isinstance(cfg, dict):
                continue
            allowed.update(str(item) for item in cfg.get("allowed_decisions", []) if str(item).strip())
    if not allowed:
        allowed.update(["approve", "edit", "reject"])
    return {
        "kind": "a2a_remote_hitl",
        "thread_id": task_id,
        "task_id": task_id,
        "context_id": context_id,
        "sequence": seq,
        "message": _extract_content(data) or "Research requires input before continuing.",
        "prompts": prompts or ([data] if data else []),
        "allowed_decisions": sorted(allowed),
        "action_requests": data.get("action_requests") or first.get("action_requests") or [],
        "review_configs": review_configs if isinstance(review_configs, list) else [],
    }


def _error_envelope(
    data: dict[str, Any],
    *,
    task_id: str,
    seq: int,
    stage: str,
    retryable: bool | None = None,
) -> dict[str, Any]:
    partial_artifacts = data.get("partial_artifacts") if isinstance(data.get("partial_artifacts"), list) else []
    classification = _classify_failure(data, stage=stage, retryable=retryable)
    return {
        "code": str(data.get("code") or "SOULSEARCHER_A2A_ERROR"),
        "message": _extract_content(data) or str(data.get("error") or "Research failed."),
        "stage": str(data.get("stage") or stage or "research"),
        "failure_class": classification["failure_class"],
        "retryable": classification["retryable"],
        "side_effectful": classification["side_effectful"],
        "ambiguous": classification["ambiguous"],
        "requires_reconcile": classification["requires_reconcile"],
        "trace_id": str(data.get("trace_id") or data.get("traceId") or task_id),
        "last_event_seq": int(data.get("last_event_seq") or data.get("lastEventSeq") or seq or 0),
        "partial_artifacts": partial_artifacts,
        "suggested_action": str(data.get("suggested_action") or data.get("suggestedAction") or classification["suggested_action"]),
    }


def _classify_failure(
    data: dict[str, Any],
    *,
    stage: str,
    retryable: bool | None,
) -> dict[str, Any]:
    explicit_class = str(data.get("failure_class") or data.get("failureClass") or "").strip().lower()
    if explicit_class:
        resolved_retryable = bool(data.get("retryable")) if retryable is None else bool(retryable)
        ambiguous = bool(data.get("ambiguous", False))
        side_effectful = bool(data.get("side_effectful") or data.get("sideEffectful") or ambiguous)
        return {
            "failure_class": explicit_class,
            "retryable": resolved_retryable,
            "side_effectful": side_effectful,
            "ambiguous": ambiguous,
            "requires_reconcile": bool(data.get("requires_reconcile") or data.get("requiresReconcile") or ambiguous),
            "suggested_action": "query_remote_state_before_retry" if ambiguous else ("retry_with_backoff" if resolved_retryable else "review_task_events"),
        }
    if retryable is not None:
        resolved_retryable = bool(retryable)
    else:
        resolved_retryable = bool(data.get("retryable", False))
    text = " ".join(
        str(data.get(key) or "")
        for key in ("message", "error", "code", "exception", "type")
    ).lower()
    transient_markers = ("timeout", "timed out", "connection", "temporar", "rate limit", "429", "503", "502", "504")
    deterministic_markers = ("invalid", "permission", "auth", "not found", "schema", "validation", "bad request")
    side_effectful = bool(data.get("side_effectful") or data.get("sideEffectful"))
    ambiguous = side_effectful and any(marker in text for marker in transient_markers)
    if ambiguous:
        return {
            "failure_class": "side_effectful",
            "retryable": False,
            "side_effectful": True,
            "ambiguous": True,
            "requires_reconcile": True,
            "suggested_action": "query_remote_state_before_retry",
        }
    if resolved_retryable or any(marker in text for marker in transient_markers):
        return {
            "failure_class": "transient",
            "retryable": True,
            "side_effectful": side_effectful,
            "ambiguous": False,
            "requires_reconcile": False,
            "suggested_action": "retry_with_backoff",
        }
    if any(marker in text for marker in deterministic_markers):
        return {
            "failure_class": "deterministic",
            "retryable": False,
            "side_effectful": side_effectful,
            "ambiguous": False,
            "requires_reconcile": False,
            "suggested_action": "fix_request",
        }
    return {
        "failure_class": str(data.get("failure_class") or "unknown"),
        "retryable": False,
        "side_effectful": side_effectful,
        "ambiguous": False,
        "requires_reconcile": False,
        "suggested_action": "review_task_events",
    }


async def _record_a2a_stream_event(
    task_id: str,
    *,
    seq: int,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    try:
        run_manager.append_event(
            run_id=task_id,
            thread_id=task_id,
            seq=seq,
            type=f"a2a.stream.{event_type or 'event'}",
            payload=payload,
            status=str(event_type or ""),
        )
    except Exception:
        logger.debug("[A2A] failed to persist stream event", exc_info=True)


def _record_last_seq(record: dict[str, Any]) -> int:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    return int(metadata.get("last_event_seq") or 0)


async def _deliver_callback(
    settings: Any,
    metadata: dict[str, Any],
    *,
    event: str,
    task: Task | None = None,
    task_id: str = "",
    context_id: str = "",
    status: str = "",
    message: str = "",
    artifact: dict[str, Any] | None = None,
    hitl: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> None:
    callback_url = str(metadata.get("callback_url") or "").strip()
    if not callback_url.startswith(("http://", "https://")):
        return
    payload = {
        "event": event,
        "task_id": task.id if task is not None else task_id,
        "context_id": task.context_id if task is not None else context_id,
        "status": _task_state_name(task) if task is not None else status,
        "message": message,
        "artifact": artifact or {},
        "hitl": hitl or {},
        "error": error or {},
        "metadata": {
            "client_request_id": metadata.get("client_request_id") or "",
            "soulclaw_task_id": metadata.get("soulclaw_task_id") or "",
            "callback_token_id": metadata.get("callback_token_id") or "",
        },
        "updated_at": datetime.now(UTC).isoformat(),
    }
    token = str(metadata.get("callback_token") or getattr(settings, "a2a_callback_token", "") or "").strip()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-SoulSearcher-Callback-Token"] = token
    attempts = max(1, int(getattr(settings, "a2a_callback_retry_attempts", 1) or 1))
    payload["callback_id"] = "cb_" + canonical_request_hash(
        {
            "event": payload["event"],
            "task_id": payload["task_id"],
            "context_id": payload["context_id"],
            "status": payload["status"],
            "artifact": payload["artifact"],
            "hitl": payload["hitl"],
            "error": payload["error"],
            "metadata": payload["metadata"],
        }
    )[:32]
    if not bool(getattr(settings, "a2a_callback_outbox_enabled", True)):
        ok, error_message = await deliver_callback_once(
            url=callback_url,
            payload=payload,
            headers=headers,
            attempts=attempts,
        )
        if not ok:
            logger.warning("[A2A] callback delivery failed for %s: %s", payload["task_id"], error_message)
        return
    callback_outbox.enqueue(
        settings,
        url=callback_url,
        payload=payload,
        headers=headers,
        max_attempts=attempts,
    )
    result = await callback_outbox.dispatch_ready(settings, limit=max(1, attempts))
    if result.get("failed"):
        logger.warning("[A2A] callback delivery pending/failed for %s: %s", payload["task_id"], result)


def _report_part(content: str, report_format: str) -> Part:
    media_type = "text/html" if str(report_format).lower() == "html" else "text/markdown"
    return Part(text=content, media_type=media_type)


def _extract_content(data: dict[str, Any]) -> str:
    for key in ("content", "text", "message", "answer", "report", "final_report"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    nested = data.get("data")
    if isinstance(nested, dict):
        return _extract_content(nested)
    return ""


def _progress_text(event_type: str, data: dict[str, Any]) -> str:
    text = _extract_content(data)
    if text:
        return text[:1000]
    if event_type == "brief_created":
        return "Research brief prepared."
    if event_type in {"task_create", "task_update"}:
        title = data.get("title") or data.get("task") or data.get("name")
        return f"Research task updated: {title}" if title else "Research task updated."
    if event_type == "search":
        query = data.get("query") or data.get("q") or data.get("tool")
        return f"Searching: {query}" if query else "Searching for evidence."
    if event_type == "sources":
        return "Research sources collected."
    if event_type == "research_tree_update":
        return "Research tree updated."
    if event_type == "research_node_start":
        return "Research step started."
    if event_type == "research_node_complete":
        return "Research step completed."
    return ""


def _struct_to_dict(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        return _dict_value(json_format.MessageToDict(value))
    except Exception:
        return {}


def _dict_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list_value(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _header_value(headers: dict[str, Any], name: str) -> str:
    if not headers:
        return ""
    target = name.lower()
    for key, value in headers.items():
        if str(key).lower() == target:
            return str(value or "").strip()
    return ""
