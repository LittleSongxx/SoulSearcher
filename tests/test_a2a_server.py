from __future__ import annotations

import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.types import Message, Part, Role, SendMessageRequest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.api import a2a as a2a_api
from agent.api.a2a import SoulSearcherA2AExecutor, mount_a2a_routes


class RecordingEventQueue(EventQueue):
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def enqueue_event(self, event: Any) -> None:
        self.events.append(event)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        public_base_url="http://soulsearcher.test",
        port=8001,
        internal_api_key="",
        auth_user_header="X-SoulSearcher-User",
        memory_user_id="test-user",
        primary_model="",
    )


def _app(stream_factory) -> TestClient:
    app = FastAPI()
    mount_a2a_routes(app, settings=_settings(), stream_factory=stream_factory)
    return TestClient(app)


def _message(query: str = "research this") -> dict[str, Any]:
    return {
        "messageId": "msg-1",
        "role": "ROLE_USER",
        "parts": [{"text": query}],
        "metadata": {"options": {"model": "test-model"}},
    }


def _stream_payload(query: str = "research this") -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": "req-1",
        "method": "SendStreamingMessage",
        "params": {
            "message": _message(query),
            "configuration": {"acceptedOutputModes": ["text/markdown"]},
        },
    }


def _events_from_sse(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = json.loads(line.split(":", 1)[1].strip())
        events.append(payload["result"])
    return events


def test_agent_card_exposes_a2a_1_supported_interface() -> None:
    async def fake_stream(*_args: Any, **_kwargs: Any) -> AsyncIterator[str]:
        if False:
            yield ""

    client = _app(fake_stream)

    response = client.get("/.well-known/agent-card.json")

    assert response.status_code == 200
    card = response.json()
    assert card["name"] == "SoulSearcher DeepResearch"
    assert card["capabilities"]["streaming"] is True
    assert card["capabilities"]["pushNotifications"] is False
    assert card["capabilities"]["extendedAgentCard"] is False
    assert card["supportedInterfaces"] == [
        {
            "url": "http://soulsearcher.test/api/a2a",
            "protocolBinding": "JSONRPC",
            "protocolVersion": "1.0",
        }
    ]
    skill = next(item for item in card["skills"] if item["id"] == "deep-research")
    assert "text/markdown" in skill["outputModes"]
    assert "application/json" in skill["inputModes"]


def test_strict_jsonrpc_route_rejects_legacy_message_send() -> None:
    async def fake_stream(*_args: Any, **_kwargs: Any) -> AsyncIterator[str]:
        if False:
            yield ""

    client = _app(fake_stream)

    response = client.post(
        "/api/a2a",
        json={
            "jsonrpc": "2.0",
            "id": "legacy-1",
            "method": "message/send",
            "params": {"message": _message()},
        },
        headers={"A2A-Version": "1.0"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["error"]["code"] == -32601


def test_jsonrpc_route_requires_a2a_1_version_header() -> None:
    async def fake_stream(*_args: Any, **_kwargs: Any) -> AsyncIterator[str]:
        if False:
            yield ""

    client = _app(fake_stream)

    response = client.post("/api/a2a", json=_stream_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["error"]["code"] == -32009
    assert "Expected version '1.0'" in body["error"]["message"]


def test_send_streaming_message_maps_progress_artifact_and_completed() -> None:
    calls: list[dict[str, Any]] = []

    async def fake_stream(*args: Any, **kwargs: Any) -> AsyncIterator[str]:
        calls.append({"args": args, "kwargs": kwargs})
        for frame in [
            {"type": "search", "data": {"query": "research this"}},
            {"type": "completion", "data": {"content": "final report", "format": "markdown"}},
            {"type": "done", "data": {}},
        ]:
            yield f"0:{json.dumps(frame)}\n"

    client = _app(fake_stream)

    response = client.post(
        "/api/a2a",
        json=_stream_payload(),
        headers={"A2A-Version": "1.0"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events_from_sse(response.text)
    assert "task" in events[0]
    status_states = [
        item["statusUpdate"]["status"]["state"]
        for item in events
        if "statusUpdate" in item
    ]
    assert "TASK_STATE_WORKING" in status_states
    assert status_states[-1] == "TASK_STATE_COMPLETED"
    artifacts = [item["artifactUpdate"]["artifact"] for item in events if "artifactUpdate" in item]
    assert artifacts[0]["parts"][0]["text"] == "final report"
    assert artifacts[0]["parts"][0]["mediaType"] == "text/markdown"
    assert calls[0]["args"][0] == "research this"
    assert calls[0]["kwargs"]["thread_id"] == events[0]["task"]["id"]
    assert calls[0]["kwargs"]["run_id"] == events[0]["task"]["id"]


def test_send_streaming_message_uses_trusted_user_header_when_internal_auth_enabled() -> None:
    settings = _settings()
    settings.internal_api_key = "secret"
    calls: list[dict[str, Any]] = []

    async def fake_stream(*args: Any, **kwargs: Any) -> AsyncIterator[str]:
        calls.append({"args": args, "kwargs": kwargs})
        yield f"0:{json.dumps({'type': 'done', 'data': {}})}\n"

    app = FastAPI()
    mount_a2a_routes(app, settings=settings, stream_factory=fake_stream)
    client = TestClient(app)

    response = client.post(
        "/api/a2a",
        json=_stream_payload(),
        headers={"A2A-Version": "1.0", "X-SoulSearcher-User": "soulclaw"},
    )

    assert response.status_code == 200
    assert calls[0]["kwargs"]["user_id"] == "soulclaw"


def test_send_streaming_message_rejects_user_spoofing_when_internal_auth_enabled() -> None:
    settings = _settings()
    settings.internal_api_key = "secret"

    async def fake_stream(*_args: Any, **_kwargs: Any) -> AsyncIterator[str]:
        yield f"0:{json.dumps({'type': 'done', 'data': {}})}\n"

    app = FastAPI()
    mount_a2a_routes(app, settings=settings, stream_factory=fake_stream)
    client = TestClient(app)
    payload = _stream_payload()
    payload["params"]["message"]["metadata"]["options"]["user_id"] = "other-user"

    response = client.post(
        "/api/a2a",
        json=payload,
        headers={"A2A-Version": "1.0", "X-SoulSearcher-User": "soulclaw"},
    )

    assert response.status_code == 200
    events = _events_from_sse(response.text)
    status_states = [
        item["statusUpdate"]["status"]["state"]
        for item in events
        if "statusUpdate" in item
    ]
    assert status_states[-1] == "TASK_STATE_REJECTED"


@pytest.mark.parametrize(
    ("frame", "state"),
    [
        ({"type": "error", "data": {"message": "boom"}}, "TASK_STATE_FAILED"),
        ({"type": "interrupt", "data": {"message": "approve plan"}}, "TASK_STATE_INPUT_REQUIRED"),
    ],
)
def test_send_streaming_message_maps_failed_and_input_required(
    frame: dict[str, Any],
    state: str,
) -> None:
    async def fake_stream(*_args: Any, **_kwargs: Any) -> AsyncIterator[str]:
        yield f"0:{json.dumps(frame)}\n"

    client = _app(fake_stream)

    response = client.post(
        "/api/a2a",
        json=_stream_payload(),
        headers={"A2A-Version": "1.0"},
    )

    assert response.status_code == 200
    events = _events_from_sse(response.text)
    status_states = [
        item["statusUpdate"]["status"]["state"]
        for item in events
        if "statusUpdate" in item
    ]
    assert status_states[-1] == state


@pytest.mark.asyncio
async def test_cancel_task_calls_cancellation_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    cancelled: list[tuple[str, str]] = []

    async def fake_cancel(task_id: str, reason: str) -> None:
        cancelled.append((task_id, reason))

    async def fake_stream(*_args: Any, **_kwargs: Any) -> AsyncIterator[str]:
        if False:
            yield ""

    monkeypatch.setattr(a2a_api.cancellation_manager, "cancel", fake_cancel)
    executor = SoulSearcherA2AExecutor(settings=_settings(), stream_factory=fake_stream)
    context = RequestContext(
        call_context=ServerCallContext(state={"headers": {}}),
        task_id="task-1",
        context_id="ctx-1",
        request=SendMessageRequest(
            message=Message(
                message_id="msg-1",
                role=Role.ROLE_USER,
                parts=[Part(text="research this")],
            )
        ),
    )
    queue = RecordingEventQueue()

    await executor.cancel(context, queue)

    assert cancelled == [("task-1", "A2A client requested cancellation")]
    assert getattr(queue.events[-1].status, "state") == a2a_api.TaskState.TASK_STATE_CANCELED
