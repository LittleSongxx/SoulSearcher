import pytest
from httpx import ASGITransport, AsyncClient

import main


class _CheckpointTuple:
    def __init__(self, state):
        self.checkpoint = {"channel_values": state}
        self.metadata = {"created_at": "2026-02-21T00:00:00Z"}
        self.parent_config = None


class _FakeCheckpointer:
    def __init__(self, by_thread_id):
        self._by_thread_id = by_thread_id

    def get_tuple(self, config):
        thread_id = (config or {}).get("configurable", {}).get("thread_id")
        if not thread_id or thread_id not in self._by_thread_id:
            return None
        return _CheckpointTuple(self._by_thread_id[thread_id])


@pytest.mark.asyncio
async def test_export_forbidden_for_other_user_when_internal_auth_enabled(monkeypatch):
    monkeypatch.setitem(main.settings.__dict__, "internal_api_key", "test-key")
    monkeypatch.setitem(main.settings.__dict__, "auth_user_header", "X-Weaver-User")

    fake_checkpointer = _FakeCheckpointer(
        by_thread_id={
            "thread_alice": {
                "user_id": "alice",
                "final_report": "hello",
                "scraped_content": [],
                "deepsearch_artifacts": {},
            }
        }
    )
    monkeypatch.setattr(main, "checkpointer", fake_checkpointer)

    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        forbidden = await ac.get(
            "/api/export/thread_alice?format=json",
            headers={
                "Authorization": "Bearer test-key",
                "X-Weaver-User": "bob",
            },
        )
        assert forbidden.status_code == 403
