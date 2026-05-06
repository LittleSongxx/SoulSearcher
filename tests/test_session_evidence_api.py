from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

import main
from common.session_manager import SessionState


@pytest.mark.asyncio
async def test_session_evidence_unknown_session_returns_404():
    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/sessions/nope/evidence")
        assert resp.status_code == 404
        error = (resp.json() or {}).get("error", "")
        assert "Session not found" in error


@pytest.mark.asyncio
async def test_session_evidence_includes_fetched_pages_and_passages(monkeypatch):
    artifacts = {
        "sources": [],
        "claims": [],
        "quality_summary": {"summary_count": 1},
        "research_brief": {"original_query": "q", "clarified_goal": "q"},
        "source_routing": {"mode": "hybrid", "providers": ["web", "rag"]},
        "source_collections": [{"id": "team_docs", "name": "team_docs"}],
        "quality_gates": [{"epoch": 1, "stage": "final", "gates": []}],
        "evidence_items": [
            {
                "id": "ev1",
                "source_type": "web",
                "url": "https://example.com/",
                "title": "Example",
                "snippet": "hello",
            }
        ],
        "citation_annotations": [
            {
                "id": "cite1",
                "citation_id": "1",
                "marker": "[1]",
                "source_index": 1,
                "start_char": 0,
                "end_char": 3,
                "section": "body",
                "url": "https://example.com/",
                "evidence_ids": ["ev1"],
                "occurrence": 1,
            }
        ],
        "timeline": [
            {
                "id": "tl1",
                "order": 1,
                "event_type": "source",
                "title": "Example",
                "url": "https://example.com/",
            }
        ],
        "supervisor_decisions": [
            {"round_index": 1, "action": "synthesize", "reason": "enough"}
        ],
        "worker_runs": [
            {
                "worker_id": "worker_1",
                "context_id": "ctx_worker_1",
                "topic": "q",
                "focus": "evidence",
                "queries": ["q evidence"],
                "round_index": 1,
                "result_count": 1,
                "evidence_count": 1,
                "status": "completed",
            }
        ],
        "intermediate_steps": [
            {"id": "step1", "order": 1, "type": "worker_run", "worker_id": "worker_1"}
        ],
        "continue_requests": [
            {"request_id": "continue_1", "target_type": "claim", "target_text": "claim"}
        ],
        "fetched_pages": [
            {
                "url": "https://example.com/",
                "raw_url": "https://example.com/?utm=1",
                "method": "direct_http",
                "text": "hello",
                "http_status": 200,
                "attempts": 1,
            }
        ],
        "passages": [
            {
                "url": "https://example.com/",
                "text": "hello",
                "start_char": 0,
                "end_char": 5,
                "heading": "Intro",
                "heading_path": ["Intro"],
                "page_title": "Example Title",
                "retrieved_at": "2026-02-17T00:00:00+00:00",
                "method": "direct_http",
                "quote": "hello",
                "snippet_hash": "deadbeef",
            }
        ],
        "research_pipeline": {"stage_count": 3},
        "stage_runtime": {"stage_count": 4, "resumable": True},
        "source_quality": {"source_diversity_score": 1.0},
        "browser_reader_plan": {"action_count": 1},
        "worker_orchestration": {"dispatch_model": "parallel_batch"},
        "branch_diagnostics": {"branch_count": 1},
        "brief_review": {"status": "approved"},
        "fallback": {"source_strategy": "supervisor_workers", "fallback_strategy": "linear"},
    }
    state = SessionState(
        thread_id="thread-evidence",
        state={"route": "deep", "deepsearch_artifacts": artifacts},
        checkpoint_ts="",
        parent_checkpoint_id=None,
        deepsearch_artifacts=artifacts,
    )

    class FakeManager:
        @staticmethod
        def get_session_state(thread_id: str):
            if thread_id != "thread-evidence":
                return None
            return state

    monkeypatch.setattr(main, "checkpointer", object())
    monkeypatch.setattr(
        "common.session_manager.get_session_manager", lambda checkpointer: FakeManager()
    )

    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/sessions/thread-evidence/evidence")

    assert resp.status_code == 200
    data = resp.json() or {}
    assert data.get("quality_summary", {}).get("summary_count") == 1
    assert data.get("research_brief", {}).get("original_query") == "q"
    assert data.get("source_routing", {}).get("mode") == "hybrid"
    assert data.get("source_collections", [{}])[0].get("id") == "team_docs"
    assert data.get("evidence_store", {}).get("thread_id") == "thread-evidence"
    assert data.get("access_policy", {}).get("visibility") == "private"
    assert data.get("quality_gates", [{}])[0].get("stage") == "final"
    assert data.get("evidence_items", [{}])[0].get("id") == "ev1"
    assert data.get("citation_annotations", [{}])[0].get("id") == "cite1"
    assert data.get("timeline", [{}])[0].get("event_type") == "source"
    assert data.get("supervisor_decisions", [{}])[0].get("action") == "synthesize"
    assert data.get("worker_runs", [{}])[0].get("worker_id") == "worker_1"
    assert data.get("intermediate_steps", [{}])[0].get("type") == "worker_run"
    assert data.get("continue_requests", [{}])[0].get("request_id") == "continue_1"
    assert len(data.get("fetched_pages", [])) == 1
    assert len(data.get("passages", [])) == 1
    passage = (data.get("passages", []) or [None])[0] or {}
    assert passage.get("heading") == "Intro"
    assert passage.get("heading_path") == ["Intro"]
    assert passage.get("page_title") == "Example Title"
    assert passage.get("retrieved_at") == "2026-02-17T00:00:00+00:00"
    assert passage.get("method") == "direct_http"
    assert passage.get("quote") == "hello"
    assert passage.get("snippet_hash") == "deadbeef"
    assert data.get("research_pipeline", {}).get("stage_count") == 3
    assert data.get("stage_runtime", {}).get("resumable") is True
    assert data.get("source_quality", {}).get("source_diversity_score") == 1.0
    assert data.get("browser_reader_plan", {}).get("action_count") == 1
    assert data.get("worker_orchestration", {}).get("dispatch_model") == "parallel_batch"
    assert data.get("branch_diagnostics", {}).get("branch_count") == 1
    assert data.get("brief_review", {}).get("status") == "approved"
    assert data.get("fallback", {}).get("fallback_strategy") == "linear"


@pytest.mark.asyncio
async def test_session_evidence_enriches_claims_from_passages(monkeypatch):
    state = {
        "final_report": "The company's revenue increased in 2024 according to the annual report.",
        "scraped_content": [],
        "deepsearch_artifacts": {
            "passages": [
                {
                    "url": "https://example.com/earnings?utm_source=test",
                    "text": "In 2024, the company's revenue increased by 5% year over year.",
                    "start_char": 0,
                    "end_char": 66,
                    "snippet_hash": "passage_123",
                    "quote": "In 2024, the company's revenue increased by 5% year over year.",
                    "heading_path": ["Results"],
                }
            ]
        },
    }

    checkpoint = SimpleNamespace(
        checkpoint={"channel_values": state}, metadata={}, parent_config=None
    )
    monkeypatch.setattr(
        main, "checkpointer", SimpleNamespace(get_tuple=lambda config: checkpoint)
    )

    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/sessions/thread-claims/evidence")

    assert resp.status_code == 200
    data = resp.json() or {}
    assert isinstance(data.get("claims"), list)
    assert data.get("claims"), "expected claim verifier to enrich claims from passages"
    claim = (data.get("claims") or [None])[0] or {}
    assert isinstance(claim.get("evidence_passages"), list)
    assert claim.get("evidence_passages"), "expected passage evidence to be present"
    passage = (claim.get("evidence_passages") or [None])[0] or {}
    assert passage.get("snippet_hash") == "passage_123"
    assert "utm_source" not in str(passage.get("url") or "")
