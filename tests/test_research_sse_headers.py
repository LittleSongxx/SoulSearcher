import pytest
from httpx import ASGITransport, AsyncClient

import main


@pytest.mark.asyncio
async def test_research_sse_sets_thread_header_even_on_error(monkeypatch):
    # Force deterministic error path.
    monkeypatch.setattr(main.settings, "openai_api_key", "")

    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/research/sse", json={"query": "hi"})
        assert resp.status_code == 200
        assert resp.headers.get("X-Thread-ID")


@pytest.mark.asyncio
async def test_research_sse_emits_brief_event_before_error(monkeypatch):
    monkeypatch.setattr(main.settings, "openai_api_key", "")

    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/research/sse",
            json={
                "query": "compare private docs",
                "deepsearch_config": {"source_policy": "hybrid"},
            },
        )
        text = resp.text

    assert "event: brief_created" in text
    assert '"type": "brief.created"' in text
    assert '"mode": "hybrid"' in text
    assert "event: error" in text
