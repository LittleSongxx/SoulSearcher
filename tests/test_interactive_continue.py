import pytest
from httpx import ASGITransport, AsyncClient

import main
from agent.workflows.interactive_continue import build_continue_research_plan
from common.session_manager import SessionState


def test_build_continue_research_plan_for_claim_target():
    artifacts = {
        "research_brief": {"original_query": "AI agents"},
        "claims": [
            {"claim": "Framework A has better tool routing.", "status": "unsupported"}
        ],
    }

    plan = build_continue_research_plan(
        artifacts=artifacts,
        target_type="claim",
        target_index=1,
        instruction="find primary evidence",
    )
    payload = plan.to_dict()

    assert payload["target_type"] == "claim"
    assert "Framework A" in payload["target_text"]
    assert payload["generated_queries"]
    assert plan.update_state["missing_topics"] == ["Framework A has better tool routing."]
    assert plan.update_state["deepsearch_strategy_decision"]["strategy"] == "supervisor_workers"


def test_build_continue_research_plan_for_gap_target_from_quality_gates():
    artifacts = {
        "quality_gates": [
            {
                "epoch": 1,
                "stage": "final",
                "gates": [
                    {
                        "name": "query_coverage",
                        "status": "fail",
                        "details": {"missing_dimensions": ["freshness"]},
                    }
                ],
            }
        ]
    }

    plan = build_continue_research_plan(
        artifacts=artifacts,
        target_type="gap",
        target_index=1,
    )

    assert plan.target_text == "freshness"
    assert "freshness" in plan.resume_input
    assert plan.update_state["research_plan"]


def test_build_continue_research_plan_rejects_unknown_target_type():
    with pytest.raises(ValueError):
        build_continue_research_plan(
            artifacts={},
            target_type="unknown",
            target_text="x",
        )


@pytest.mark.asyncio
async def test_continue_research_session_returns_resume_payload(monkeypatch):
    artifacts = {
        "research_brief": {"original_query": "AI agents"},
        "claims": [{"claim": "Claim needs evidence", "status": "unsupported"}],
        "quality_gates": [],
    }
    state = SessionState(
        thread_id="thread-continue",
        state={"route": "deep", "deepsearch_artifacts": artifacts},
        checkpoint_ts="",
        parent_checkpoint_id=None,
        deepsearch_artifacts=artifacts,
    )

    class FakeManager:
        @staticmethod
        def get_session_state(thread_id: str):
            if thread_id != "thread-continue":
                return None
            return state

        @staticmethod
        def build_resume_state(thread_id: str, additional_input=None, update_state=None):
            if thread_id != "thread-continue":
                return None
            restored = dict(state.state)
            if update_state:
                restored.update(update_state)
            if additional_input:
                restored["resume_input"] = additional_input
            restored["resumed_from_checkpoint"] = True
            return restored

    monkeypatch.setattr(main, "checkpointer", object())
    monkeypatch.setattr("common.session_manager.get_session_manager", lambda checkpointer: FakeManager())

    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/sessions/thread-continue/continue-research",
            json={
                "target_type": "claim",
                "target_index": 1,
                "instruction": "补充证据",
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready_to_continue"
    assert data["continue_request"]["target_type"] == "claim"
    assert data["resume_state"]["resumed_from_checkpoint"] is True
    assert data["stream_payload"]["search_mode"]["useDeepSearch"] is True
    assert data["update_state"]["deepsearch_artifacts"]["continue_requests"]
