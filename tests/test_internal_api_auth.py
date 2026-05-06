import pytest
from httpx import ASGITransport, AsyncClient

import main


@pytest.mark.asyncio
async def test_internal_api_key_required_when_configured(monkeypatch):
    monkeypatch.setitem(main.settings.__dict__, "internal_api_key", "test-key")

    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/memory/status")

    assert resp.status_code == 401
    payload = resp.json()
    assert payload.get("status_code") == 401
    assert payload.get("error")


@pytest.mark.asyncio
async def test_internal_api_key_allows_access_when_provided(monkeypatch):
    monkeypatch.setitem(main.settings.__dict__, "internal_api_key", "test-key")

    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/memory/status",
            headers={"Authorization": "Bearer test-key"},
        )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload.get("backend") is not None


@pytest.mark.asyncio
async def test_thread_cancel_forbidden_for_other_user_when_isolation_enabled(
    monkeypatch,
):
    monkeypatch.setitem(main.settings.__dict__, "internal_api_key", "test-key")
    monkeypatch.setitem(main.settings.__dict__, "auth_user_header", "X-Weaver-User")
    monkeypatch.setattr(main.settings, "openai_api_key", "")

    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        create_resp = await ac.post(
            "/api/research/sse",
            headers={
                "Authorization": "Bearer test-key",
                "X-Weaver-User": "alice",
            },
            json={"query": "hi"},
        )

        assert create_resp.status_code == 200
        thread_id = create_resp.headers.get("x-thread-id")
        assert thread_id

        forbidden = await ac.post(
            f"/api/research/cancel/{thread_id}",
            headers={
                "Authorization": "Bearer test-key",
                "X-Weaver-User": "bob",
            },
        )

        assert forbidden.status_code == 403
