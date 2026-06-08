from __future__ import annotations

import asyncio

from langchain_core.messages import HumanMessage, SystemMessage


def test_build_initial_state_preserves_user_query_with_system_messages():
    from agent.core.state import build_initial_state

    state = build_initial_state(
        input_text="Explain Weaver architecture",
        messages=[SystemMessage(content="memory context")],
    )

    assert state["input"] == "Explain Weaver architecture"
    assert isinstance(state["messages"][0], SystemMessage)
    assert isinstance(state["messages"][-1], HumanMessage)
    assert state["messages"][-1].content == "Explain Weaver architecture"


def test_researcher_tool_name_map_accepts_pydantic_tool_classes():
    from agent.core.state import ResearchComplete, ThinkTool
    from agent.workflows.researcher import _build_tools_by_name

    tools_by_name = _build_tools_by_name([ThinkTool, ResearchComplete])

    assert tools_by_name["ThinkTool"] is ThinkTool
    assert tools_by_name["ResearchComplete"] is ResearchComplete


def test_researcher_source_policy_enables_rag_without_web_for_local_docs():
    from agent.workflows.researcher import _researcher_source_policy

    policy = _researcher_source_policy({
        "configurable": {
            "source_routing": {
                "mode": "local_docs_only",
                "providers": ["rag"],
                "collections": [{"id": "user_docs"}],
            }
        }
    })

    assert policy["include_rag"] is True
    assert policy["include_web"] is False
    assert policy["rag_collection_name"] == "user_docs"


def test_skill_tool_policy_keeps_core_control_tools_with_allowlist():
    from agent.core.state import ResearchComplete, ThinkTool
    from agent.skills.tool_policy import filter_tools_by_skill_allowed_tools

    class Skill:
        name = "restricted"
        allowed_tools = {"rag_search"}

    filtered = filter_tools_by_skill_allowed_tools([ThinkTool, ResearchComplete], [Skill()])

    assert ThinkTool in filtered
    assert ResearchComplete in filtered


def test_report_evidence_ledger_builds_passages_from_notes_and_sources():
    from agent.workflows.report import _build_evidence_ledger, _evidence_passages

    evidence = _build_evidence_ledger(
        {"sources": [{"url": "https://example.com/a", "title": "Example"}]},
        notes=[
            "Detailed finding from https://example.com/a with enough supporting "
            "context to become a research-note evidence item."
        ],
    )
    passages = _evidence_passages(evidence)

    assert evidence
    assert any(item.get("url") == "https://example.com/a" for item in evidence)
    assert passages
    assert any("Detailed finding" in passage["text"] for passage in passages)


def test_report_quality_followup_routes_back_to_supervisor():
    from agent.core.graph import _route_after_report

    assert _route_after_report({"quality_followup_required": True}, {}) == "research_supervisor"
    assert _route_after_report({"quality_followup_required": False}, {}) == "__end__"


def test_researcher_extracts_evidence_from_tool_observation():
    from agent.workflows.researcher import _extract_evidence_from_observation

    kwargs = dict(
        tool_name="tavily_search",
        args={"query": "weaver"},
        observation=(
            "[1] Source: https://example.com/weaver\n"
            "Weaver is a LangGraph-based research system with citations."
        ),
        research_topic="weaver architecture",
    )
    evidence = _extract_evidence_from_observation(**kwargs)
    evidence_again = _extract_evidence_from_observation(**kwargs)

    assert evidence
    assert evidence[0]["id"] == evidence_again[0]["id"]
    assert evidence[0]["tool"] == "tavily_search"
    assert evidence[0]["query"] == "weaver"
    assert evidence[0]["url"] == "https://example.com/weaver"


def test_quality_summary_uses_l3_when_l1_is_unavailable():
    from agent.workflows.report import _build_quality_summary

    class L3Result:
        overall_score = 0.82
        overall_passed = True
        metadata = {}

    summary = _build_quality_summary("markdown", None, L3Result())

    assert summary["publish_ready"] is True
    assert summary["overall_verdict"] == "pass"
    assert summary["overall_score"] == 0.82


def test_report_generation_error_clears_quality_followup(monkeypatch):
    from agent.workflows import report

    class FailingModel:
        def with_config(self, _config):
            return self

        async def ainvoke(self, _messages):
            raise RuntimeError("synthetic report failure")

    monkeypatch.setattr(report, "configurable_model", FailingModel())

    result = asyncio.run(
        report.final_report_generation(
            {
                "research_brief": "brief",
                "complexity": "standard",
                "notes": ["Enough research notes for prompt construction."],
                "messages": [HumanMessage(content="query")],
                "sources": [],
                "curated_sources": [],
                "deepsearch_artifacts": {},
                "report_format": "markdown",
                "skill_ids": [],
                "quality_followup_required": True,
            },
            {"configurable": {"thread_id": "test_report_error"}},
        )
    )

    assert result["quality_followup_required"] is False
    assert result["final_report"].startswith("Error generating final report")


def test_research_workspace_writes_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("WEAVER_RESEARCH_WORKSPACE_PATH", str(tmp_path))

    from agent.runtime.workspace import get_research_workspace

    workspace = get_research_workspace("thread/test")
    workspace.write_json("quality.json", {"passed": True})
    workspace.write_jsonl("evidence.jsonl", [{"id": "e1"}])

    assert (workspace.root / "quality.json").exists()
    assert (workspace.root / "evidence.jsonl").read_text(encoding="utf-8").strip()


def test_research_runtime_builder_creates_state_config_and_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("WEAVER_RESEARCH_WORKSPACE_PATH", str(tmp_path))

    from agent.runtime.request_builder import (
        ResearchRuntimeRequest,
        build_research_runtime,
    )

    bundle = build_research_runtime(
        ResearchRuntimeRequest(
            input_text="query",
            thread_id="thread_1",
            model="test-model",
            mode_info={"mode": "deep"},
            user_id="u1",
            images=[],
            research_brief=None,
            context_messages=[],
            deepsearch_config={"skill_ids": ["deep-research"]},
            base_configurable={"thread_id": "thread_1"},
        )
    )

    assert bundle.initial_state["messages"][-1].content == "query"
    assert bundle.initial_state["skill_ids"] == ["deep-research"]
    assert bundle.config["configurable"]["workspace_path"]
    assert bundle.initial_state["deepsearch_artifacts"]["workspace"]["path"]
