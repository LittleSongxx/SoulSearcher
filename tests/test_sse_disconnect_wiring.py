import asyncio

import pytest

import main


class _DummyState:
    principal_id = "alice"


class _DummyRequest:
    headers = {}
    state = _DummyState()

    async def is_disconnected(self) -> bool:
        return True


async def _blocked_stream(*_args, **_kwargs):
    # Simulate an upstream generator that would otherwise keep running forever.
    await asyncio.Event().wait()
    yield '0:{"type":"text","data":{"content":"hi"}}\\n'


@pytest.mark.asyncio
async def test_research_sse_aborts_when_request_is_disconnected(monkeypatch):
    monkeypatch.setitem(main.settings.__dict__, "openai_api_key", "test-key")
    monkeypatch.setattr(main, "stream_agent_events", _blocked_stream)

    payload = main.ResearchRequest(query="hi")
    resp = await main.research_sse(_DummyRequest(), payload)

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(resp.body_iterator), timeout=1.0)
