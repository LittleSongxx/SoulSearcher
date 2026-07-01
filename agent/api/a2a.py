from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from typing import Any

from google.protobuf import json_format

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    Message,
    Part,
    Task,
    TaskState,
    TaskStatus,
)
from a2a.utils.constants import PROTOCOL_VERSION_1_0, TransportProtocol
from fastapi import FastAPI

from common.cancellation import cancellation_manager
from common.stream_translate import data_stream_line_to_payload
from common.thread_ownership import set_thread_owner

logger = logging.getLogger(__name__)

StreamFactory = Callable[..., AsyncIterator[str]]

_DEFAULT_DEEP_SEARCH_MODE = {
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


def mount_a2a_routes(
    app: FastAPI,
    *,
    settings: Any,
    stream_factory: StreamFactory,
) -> AgentCard:
    """Mount SoulSearcher's A2A 1.0 JSON-RPC server routes on the FastAPI app."""
    agent_card = build_agent_card(settings)
    executor = SoulSearcherA2AExecutor(settings=settings, stream_factory=stream_factory)
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
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
    def __init__(self, *, settings: Any, stream_factory: StreamFactory) -> None:
        self.settings = settings
        self.stream_factory = stream_factory

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = str(context.task_id or f"a2a_{uuid.uuid4().hex}")
        context_id = str(context.context_id or task_id)
        updater = TaskUpdater(event_queue, task_id, context_id)

        query = (context.get_user_input() or "").strip()
        metadata = _merged_metadata(context)
        options = _dict_value(metadata.get("options"))
        request_context = _dict_value(metadata.get("context"))

        await _enqueue_initial_task(event_queue, task_id, context_id, context.message)

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
        set_thread_owner(task_id, owner_id)

        await updater.start_work(
            _agent_message(updater, "DeepResearch task accepted.")
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

        try:
            async for line in self.stream_factory(
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
            ):
                if not isinstance(line, str) or line.startswith(":"):
                    continue
                seq += 1
                payload = data_stream_line_to_payload(line, seq=seq)
                if not payload:
                    continue
                event_type = str(payload.get("type") or "").strip()
                data = payload.get("data") if isinstance(payload.get("data"), dict) else {}

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
                            },
                            last_chunk=True,
                        )
                        if event_type in {"completion", "report_written"}:
                            final_artifact_sent = True
                    continue

                if event_type == "interrupt":
                    terminal_sent = True
                    await updater.requires_input(
                        _agent_message(
                            updater,
                            _extract_content(data) or "Research requires input before continuing.",
                        )
                    )
                    return

                if event_type == "error":
                    terminal_sent = True
                    await updater.failed(
                        _agent_message(updater, _extract_content(data) or "Research failed.")
                    )
                    return

                if event_type in {"cancelled", "canceled"}:
                    terminal_sent = True
                    await updater.cancel(
                        _agent_message(updater, _extract_content(data) or "Research canceled.")
                    )
                    return

                if event_type == "done":
                    terminal_sent = True
                    await updater.complete(
                        _agent_message(updater, final_content or "Research completed.")
                    )
                    return

                if event_type in _PROGRESS_EVENTS:
                    progress = _progress_text(event_type, data)
                    if progress:
                        await updater.start_work(
                            _agent_message(updater, progress, metadata={"event_type": event_type})
                        )

            if not terminal_sent:
                await updater.complete(
                    _agent_message(updater, final_content or "Research completed.")
                )
        except asyncio.CancelledError:
            with suppress(Exception):
                await cancellation_manager.cancel(task_id, "A2A task cancelled")
                await updater.cancel(_agent_message(updater, "Research canceled."))
            raise
        except Exception as exc:
            logger.exception("[A2A] DeepResearch task failed: %s", task_id)
            with suppress(Exception):
                await updater.failed(_agent_message(updater, str(exc)))

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
) -> None:
    task = Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
    )
    if message is not None:
        task.history.append(message)
    await event_queue.enqueue_event(task)


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
