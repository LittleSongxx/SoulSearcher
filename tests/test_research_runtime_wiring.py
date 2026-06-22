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


def test_researcher_source_policy_drops_unknown_provider():
    from agent.workflows.researcher import _researcher_source_policy

    policy = _researcher_source_policy({
        "configurable": {
            "source_routing": {
                "mode": "web_only",
                "providers": ["removed_provider"],
            }
        }
    })

    assert policy["include_web"] is True
    assert policy["mode"] == "web_only"
    assert policy["providers"] == ["web"]


def test_parallel_researcher_configs_isolate_viewed_images():
    from agent.workflows.supervisor import (
        _isolated_researcher_config,
        _merge_researcher_viewed_images,
    )

    parent_config = {
        "configurable": {
            "thread_id": "thread-a",
            "viewed_images": {"existing": {"url": "https://example.com/existing.png"}},
        }
    }

    first = _isolated_researcher_config(parent_config)
    second = _isolated_researcher_config(parent_config)

    first["configurable"]["viewed_images"]["first"] = {
        "url": "https://example.com/first.png"
    }

    assert "first" not in second["configurable"]["viewed_images"]
    assert "first" not in parent_config["configurable"]["viewed_images"]

    second["configurable"]["viewed_images"]["second"] = {
        "url": "https://example.com/second.png"
    }
    _merge_researcher_viewed_images(parent_config, [first, second])

    assert set(parent_config["configurable"]["viewed_images"]) == {
        "existing",
        "first",
        "second",
    }


def test_skill_tool_policy_keeps_core_control_tools_with_allowlist():
    from agent.core.state import ResearchComplete, ThinkTool
    from agent.skills.tool_policy import filter_tools_by_skill_allowed_tools

    class Skill:
        name = "restricted"
        allowed_tools = {"tavily_search"}

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


def test_evidence_ledger_normalizes_canonical_source_ids():
    from agent.workflows.evidence_ledger import build_evidence_ledger

    evidence = build_evidence_ledger(
        {
            "evidence_items": [
                {
                    "url": "https://example.com/a?utm_source=x&b=2",
                    "title": "Example",
                    "content": "A sufficiently detailed passage for the ledger.",
                },
                {
                    "url": "https://example.com/a?b=2",
                    "title": "Duplicate",
                    "content": "A sufficiently detailed passage for the ledger.",
                },
            ]
        }
    )

    assert len(evidence) == 1
    assert evidence[0]["canonical_url"] == "https://example.com/a?b=2"
    assert evidence[0]["source_id"].startswith("src_")
    assert evidence[0]["snippet_hash"]


def test_citation_gate_detects_missing_source_mapping():
    from agent.workflows.evidence_ledger import evaluate_citation_gate

    result = evaluate_citation_gate(
        "Claim with good citation [2].",
        sources=[{"url": "https://example.com/one"}],
        evidence_items=[{"content": "supporting text"}],
        passages=[{"text": "supporting text"}],
    )

    assert result["passed"] is False
    assert result["missing_markers"] == [2]


def test_citation_gate_requires_traceable_binding():
    from agent.workflows.evidence_ledger import evaluate_citation_gate

    result = evaluate_citation_gate(
        "Claim with binding [1].",
        sources=[{
            "url": "https://example.com/traceable",
            "title": "Traceable",
            "source_id": "src_traceable",
            "snippet_hash": "abc123",
        }],
        evidence_items=[{
            "url": "https://example.com/traceable",
            "content": "traceable evidence passage",
            "source_id": "src_traceable",
            "snippet_hash": "abc123",
        }],
        passages=[{
            "url": "https://example.com/traceable",
            "text": "traceable evidence passage",
            "source_id": "src_traceable",
            "snippet_hash": "abc123",
        }],
    )

    assert result["passed"] is True
    assert result["citation_bindings"][0]["traceable"] is True


def test_deep_read_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("WEAVER_RESEARCH_WORKSPACE_PATH", str(tmp_path))

    from agent.workflows.source_cache import cache_source_text
    from tools.crawl.deep_read_tool import deep_read_cached_source

    config = {"configurable": {"thread_id": "thread-deep-read"}}
    cached = cache_source_text(
        text="heading\n\n" + "useful content\n" * 100,
        config=config,
        source_hint="example",
        min_chars=10,
    )
    assert cached
    assert "useful content" in deep_read_cached_source(
        cached["cached_path"],
        section_query="useful",
        config=config,
    )

    outside = tmp_path / "not-cache.txt"
    outside.write_text("secret", encoding="utf-8")
    try:
        deep_read_cached_source(str(outside), config=config)
    except PermissionError:
        pass
    else:
        raise AssertionError("deep_read should reject paths outside source-cache")


def test_report_quality_followup_routes_back_to_supervisor():
    from agent.core.graph import _route_after_report

    assert _route_after_report({"quality_followup_required": True}, {}) == "research_supervisor"
    assert _route_after_report({"quality_followup_required": False}, {}) == "__end__"


def test_curate_sources_hybrid_ranking_prefers_authority(monkeypatch):
    from agent.workflows import report

    sources = [
        {"url": "https://blog.example.com/post", "title": "Blog post", "snippet": "short"},
        {"url": "https://www.nature.com/article", "title": "Nature article", "snippet": "substantial authoritative evidence" * 20},
    ]

    async def fake_ainvoke(_messages):
        raise RuntimeError("force fallback to hybrid ranking")

    monkeypatch.setattr(report.configurable_model, "with_config", lambda _config: type("X", (), {"ainvoke": fake_ainvoke})())

    curated = asyncio.run(
        report.curate_sources(
            "evidence-backed research",
            sources,
            {"configurable": {"thread_id": "thread-curate"}},
            max_sources=2,
        )
    )

    assert curated[0]["url"].startswith("https://www.nature.com/")


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


def test_run_manager_memory_fallback_roundtrip(monkeypatch):
    from agent.runtime.runs import RunStatus, run_manager

    monkeypatch.setattr(run_manager, "_backend", "memory")
    monkeypatch.setattr(run_manager, "_db_ready", False)
    record = run_manager.start(
        run_id="run_a",
        thread_id="thread_a",
        model="model-x",
        route="standard",
        user_id="u1",
        workspace={"root": "/tmp/work"},
        metadata={"preview": "hello"},
    )
    finished = run_manager.finish(
        "thread_a",
        status=RunStatus.completed,
        token_summary={"total_tokens": 10},
        quality_summary={"publish_ready": True},
    )

    assert record.run_id == "run_a"
    assert finished is not None
    assert finished.status == RunStatus.completed
    assert run_manager.get("thread_a").quality_summary["publish_ready"] is True


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
    assert bundle.config["configurable"]["runtime_context"].thread_id == "thread_1"
    assert bundle.config["configurable"]["runtime_context"].workspace_path


def test_research_runtime_builder_promotes_memory_source_candidates(tmp_path, monkeypatch):
    monkeypatch.setenv("WEAVER_RESEARCH_WORKSPACE_PATH", str(tmp_path))

    from agent.runtime.request_builder import (
        ResearchRuntimeRequest,
        build_research_runtime,
    )

    candidate = {
        "url": "https://example.com/memory-source",
        "title": "Remembered source",
        "source": "memory",
        "requires_current_run_verification": True,
    }
    bundle = build_research_runtime(
        ResearchRuntimeRequest(
            input_text="query",
            thread_id="thread_memory_candidate",
            model="test-model",
            mode_info={"mode": "deep"},
            user_id="u1",
            images=[],
            research_brief=None,
            context_messages=[],
            deepsearch_config={
                "memory_source_candidates": [candidate],
                "memory_retrieval": {
                    "record_ids": ["mem_1"],
                    "source_candidates": [candidate],
                    "usage": "verify before citation",
                },
            },
            base_configurable={"thread_id": "thread_memory_candidate"},
        )
    )

    assert bundle.initial_state["sources"] == [candidate]
    assert bundle.initial_state["deepsearch_artifacts"]["memory_retrieval"]["record_ids"] == ["mem_1"]
    assert bundle.config["configurable"]["memory_source_candidates"] == [candidate]


def test_runtime_token_tracker_is_run_scoped():
    from agent.core.middleware import get_token_tracker
    from agent.runtime.context import RuntimeContext

    first = {"configurable": {"runtime_context": RuntimeContext(thread_id="a")}}
    second = {"configurable": {"runtime_context": RuntimeContext(thread_id="b")}}

    get_token_tracker(first).record("research", 1, 2)

    assert get_token_tracker(first).get_summary()["total_tokens"] == 3
    assert get_token_tracker(second).get_summary()["total_tokens"] == 0


def test_deferred_mcp_default_off_and_enabled_filters():
    from tools.core.deferred_tools import assemble_deferred_tools

    class Tool:
        def __init__(self, name, mcp=False):
            self.name = name
            self.description = name
            self.is_mcp_tool = mcp

    web = Tool("web_search")
    mcp = Tool("mcp_lookup", mcp=True)

    assert assemble_deferred_tools([web, mcp], enabled=False).final_tools == [web, mcp]
    enabled = assemble_deferred_tools([web, mcp], enabled=True, config={"configurable": {}})
    assert [tool.name for tool in enabled.final_tools] == ["web_search", "tool_search"]
    assert enabled.deferred_names == {"mcp_lookup"}


def test_tool_search_promotes_tools_into_config():
    import asyncio

    from agent.workflows.researcher import _execute_tool_safely
    from tools.core.deferred_tools import DeferredToolCatalog, build_tool_search_tool

    def mcp_lookup(query: str) -> str:
        """Lookup via MCP."""
        return query

    mcp_lookup.name = "mcp_lookup"
    mcp_lookup.is_mcp_tool = True
    tool_search = build_tool_search_tool(DeferredToolCatalog((mcp_lookup,)))
    config = {"configurable": {}}

    result = asyncio.run(
        _execute_tool_safely(
            tool_search,
            {"query": "select:mcp_lookup"},
            config,
        )
    )

    assert "mcp_lookup" in result
    assert config["configurable"]["promoted_tools"]["names"] == ["mcp_lookup"]


def test_slash_skill_activation_injects_hidden_context():
    from agent.runtime.request_builder import ResearchRuntimeRequest, build_research_runtime

    bundle = build_research_runtime(
        ResearchRuntimeRequest(
            input_text="/deep-research compare systems",
            thread_id="thread_skill",
            model="test-model",
            mode_info={},
            user_id="u1",
            images=[],
            research_brief=None,
            context_messages=[],
            deepsearch_config={"skill_ids": []},
            base_configurable={"thread_id": "thread_skill"},
        )
    )

    assert "deep-research" in bundle.initial_state["skill_ids"]
    assert "slash_skill_activation" in bundle.initial_state["messages"][0].content


def test_memory_profile_updates_and_injection_are_structured():
    from agent.memory import InMemoryMemoryStore, MemoryRecord, MemoryService
    from agent.memory.models import MemoryScope, MemoryType

    service = MemoryService(InMemoryMemoryStore(), embedding_dim=8)
    service.upsert_record(
        MemoryRecord(
            user_id="u1",
            scope=MemoryScope.user.value,
            type=MemoryType.profile.value,
            content="User role is research engineer.",
            summary="role: research engineer",
            confidence=0.95,
        )
    )
    service.upsert_record(
        MemoryRecord(
            user_id="u1",
            scope=MemoryScope.user.value,
            type=MemoryType.preference.value,
            content="User prefers code-first architecture analysis and official sources.",
            summary="prefers code-first analysis",
            confidence=0.95,
        )
    )

    injected = service.retrieve(
        user_id="u1",
        query="architecture analysis preferences",
        include_context=True,
    ).context

    assert "<memory_context>" in injected
    assert "<user_profile_memory>" in injected
    assert "role: research engineer" in injected
    assert "prefers code-first analysis" in injected
    assert "Final report citations must come from the current run evidence ledger." in injected


def test_search_cache_stats_include_policy_and_evictions():
    from agent.core.search_cache import SearchCache

    cache = SearchCache(max_size=1, ttl_seconds=60, similarity_threshold=0.8)
    cache.set("weaver search", [{"title": "a"}])
    assert cache.get("weaver search") == [{"title": "a"}]
    cache.set("other query", [{"title": "b"}])

    stats = cache.stats()

    assert stats["hits"] == 1
    assert stats["sets"] == 2
    assert stats["evictions"] == 1
    assert stats["total_requests"] == 1
    assert stats["ttl_seconds"] == 60.0
    assert stats["similarity_threshold"] == 0.8


def test_provider_reliability_snapshot_tracks_retries_and_open_circuit():
    from tools.search.reliability import ProviderReliabilityManager, ReliabilityPolicy

    manager = ProviderReliabilityManager(
        ReliabilityPolicy(
            max_retries=1,
            retry_backoff_seconds=0,
            circuit_breaker_failures=2,
            circuit_breaker_reset_seconds=60,
        )
    )

    def failing_call():
        raise RuntimeError("provider down")

    assert manager.call("provider_a", failing_call) == []
    snapshot = manager.snapshot("provider_a")

    assert snapshot["is_open"] is True
    assert snapshot["total_calls"] == 1
    assert snapshot["attempted_calls"] == 2
    assert snapshot["failure_count"] == 2
    assert snapshot["retry_count"] == 1
    assert snapshot["circuit_open_count"] == 1

    assert manager.call("provider_a", lambda: [{"ok": True}]) == []
    assert manager.snapshot("provider_a")["skipped_open_count"] == 1
