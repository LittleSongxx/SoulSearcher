from __future__ import annotations

import asyncio

from langchain_core.messages import HumanMessage, SystemMessage


def test_build_initial_state_preserves_user_query_with_system_messages():
    from agent.core.state import build_initial_state

    state = build_initial_state(
        input_text="Explain SoulSearcher architecture",
        messages=[SystemMessage(content="memory context")],
    )

    assert state["input"] == "Explain SoulSearcher architecture"
    assert isinstance(state["messages"][0], SystemMessage)
    assert isinstance(state["messages"][-1], HumanMessage)
    assert state["messages"][-1].content == "Explain SoulSearcher architecture"
    assert state["plan_graph"] == {}
    assert state["plan_events"] == []
    assert state["plan_version"] == 1
    assert "source_routing" not in state


def test_research_brief_and_evidence_store_do_not_emit_legacy_source_routing():
    from agent.workflows.research_brief import build_research_brief
    from common.evidence_store import build_evidence_store_snapshot

    policy = {
        "allowed_origins": ["public_web"],
        "channels": ["search_api"],
        "methods": ["web_search"],
    }
    brief = build_research_brief(
        {"input": "latest AI research", "retrieval_policy": policy},
        {"configurable": {"retrieval_policy": policy}},
    )
    brief_payload = brief.to_dict()
    snapshot = build_evidence_store_snapshot(
        thread_id="t1",
        artifacts={
            "retrieval_policy": policy,
            "source_routing": {"mode": "web_only"},
            "evidence_items": [{"id": "ev1", "content": "evidence"}],
        },
    )
    patch = snapshot.to_response_patch()

    assert "source_routing" not in brief_payload
    assert "source_routing" not in snapshot.to_dict()
    assert "source_routing" not in patch
    assert patch["retrieval_policy"]["allowed_origins"] == ["public_web"]


def test_evidence_store_response_patch_includes_plan_graph():
    from common.evidence_store import build_evidence_store_snapshot

    plan_graph = {
        "version": 2,
        "tasks": [{"id": "pt_a", "title": "Audit task", "status": "ready"}],
        "frontier": ["pt_a"],
        "events": [{"type": "graph_event"}],
        "summary": {"ready": 2},
    }
    snapshot = build_evidence_store_snapshot(
        thread_id="thread-plan",
        artifacts={
            "plan_graph": plan_graph,
            "plan_events": [{"type": "stale_artifact_event"}],
            "plan_summary": {"ready": 1},
        },
    )
    patch = snapshot.to_response_patch()

    assert patch["plan_graph"]["version"] == 2
    assert patch["plan_events"][0]["type"] == "graph_event"
    assert patch["plan_summary"]["ready"] == 2


def test_researcher_tool_name_map_accepts_pydantic_tool_classes():
    from agent.core.state import ResearchComplete, ThinkTool
    from agent.workflows.researcher import _build_tools_by_name

    tools_by_name = _build_tools_by_name([ThinkTool, ResearchComplete])

    assert tools_by_name["ThinkTool"] is ThinkTool
    assert tools_by_name["ResearchComplete"] is ResearchComplete


def test_retrieval_policy_defaults_and_rejects_legacy():
    import pytest

    from agent.retrieval.policy import (
        LegacySourceRoutingError,
        build_retrieval_policy,
        reject_legacy_source_routing,
    )

    policy = build_retrieval_policy(user_id="u1")

    assert "schema_version" not in policy
    assert policy["allowed_origins"] == ["public_web"]
    assert "search_api" in policy["channels"]
    assert "web_search" in policy["methods"]
    assert policy["corpus_policy"]["user_id"] == "u1"
    with pytest.raises(LegacySourceRoutingError):
        reject_legacy_source_routing({"mode": "web_only"})
    with pytest.raises(LegacySourceRoutingError):
        build_retrieval_policy({"mode": "web_only"})
    with pytest.raises(LegacySourceRoutingError):
        build_retrieval_policy({"schema_version": 1})


def test_retrieval_policy_private_and_external_expands_channels():
    from agent.retrieval.policy import build_retrieval_policy

    policy = build_retrieval_policy(
        {
            "allowed_origins": ["public_web", "private_corpus", "external_system"],
            "channels": ["search_api"],
            "methods": ["web_search"],
            "profiles": ["academic"],
        },
        user_id="u1",
    )

    assert "private_corpus" in policy["allowed_origins"]
    assert "file_upload" in policy["channels"]
    assert "mcp" in policy["channels"]
    assert "academic_search" in policy["methods"]
    assert "vector_search" in policy["methods"]
    assert "mcp_search" in policy["methods"]


def test_researcher_source_policy_uses_retrieval_policy():
    from agent.workflows.researcher import _researcher_source_policy

    policy = _researcher_source_policy({
        "configurable": {
            "retrieval_policy": {
                "allowed_origins": ["private_corpus"],
                "channels": ["file_upload"],
                "methods": ["vector_search"],
            }
        }
    })

    assert policy["mode"] == "retrieval"
    assert policy["include_web"] is False
    assert policy["include_rag"] is True


def test_strict_tool_policy_allows_only_retrieval_gateway_tools():
    from agent.workflows.researcher import _filter_tools_for_policy

    class Tool:
        def __init__(self, name):
            self.name = name

    tools = [
        Tool("retrieve_sources"),
        Tool("read_source"),
        Tool("tavily_search"),
        Tool("arxiv_search"),
        Tool("external_provider_tool"),
    ]
    filtered = _filter_tools_for_policy(
        tools,
        {"configurable": {"tool_policy_strict": True, "retrieval_policy_strict": True}},
        {
            "include_web": False,
            "include_academic": True,
            "include_rag": False,
            "include_mcp": False,
            "budget_policy": {},
        },
    )

    assert [tool.name for tool in filtered] == ["retrieve_sources", "read_source"]


def test_retrieval_policy_strict_false_keeps_raw_tools_available():
    from agent.workflows.researcher import _filter_tools_for_policy

    class Tool:
        def __init__(self, name):
            self.name = name

    tools = [Tool("tavily_search"), Tool("arxiv_search")]
    filtered = _filter_tools_for_policy(
        tools,
        {"configurable": {"tool_policy_strict": True, "retrieval_policy_strict": False}},
        {
            "include_web": False,
            "include_academic": True,
            "include_rag": False,
            "include_mcp": False,
            "budget_policy": {},
        },
    )

    assert [tool.name for tool in filtered] == ["tavily_search", "arxiv_search"]


def test_domain_policy_blocks_disallowed_tool_url_and_filters_results():
    from agent.workflows.researcher import (
        _apply_domain_policy_to_observation,
        _domain_policy_violation,
    )

    policy = {
        "allowed_domains": ["allowed.example"],
        "denied_domains": ["blocked.example"],
    }

    assert "Blocked by retrieval domain policy" in _domain_policy_violation(
        {"url": "https://blocked.example/report"},
        policy,
    )
    filtered = _apply_domain_policy_to_observation(
        (
            "Allowed result https://allowed.example/a has useful evidence.\n\n"
            "Blocked result https://blocked.example/b should disappear."
        ),
        policy,
    )

    assert "allowed.example" in filtered
    assert "blocked.example" not in filtered
    assert "filtered 1 result" in filtered


def test_document_library_parses_and_chunks_text():
    from agent.retrieval.documents import chunk_text, parse_document_bytes

    text = parse_document_bytes(b"# Title\n\nUseful private corpus evidence.", filename="note.md")
    chunks = chunk_text(text * 80, chunk_chars=500, overlap=50)

    assert "Useful private corpus evidence" in text
    assert len(chunks) > 1
    assert all(len(chunk) <= 500 for chunk in chunks)


def test_document_library_fallback_embedding_matches_configured_dimension():
    from agent.retrieval.documents import DocumentLibrary

    library = DocumentLibrary()
    vector = library.embed_text("fallback embedding dimension check")

    assert len(vector) == library.embedding_dim


def test_retrieval_gateway_binds_origin_channel_method(monkeypatch):
    from agent.retrieval import gateway

    class Result:
        title = "Result"
        url = "https://allowed.example/report"
        snippet = "Useful source snippet"
        content = "Useful source content"
        provider = "test"
        score = 0.9
        published_date = "2026-01-01"

    class Orchestrator:
        def search(self, **kwargs):
            return [Result()]

    monkeypatch.setattr(gateway, "get_search_orchestrator", lambda: Orchestrator(), raising=False)
    monkeypatch.setattr(
        "tools.search.multi_search.get_search_orchestrator",
        lambda: Orchestrator(),
    )

    result = asyncio.run(
        gateway.retrieve_sources(
            "test query",
            config={
                "configurable": {
                    "user_id": "u1",
                    "retrieval_policy": {
                        "allowed_origins": ["public_web"],
                        "channels": ["search_api"],
                        "methods": ["web_search"],
                        "domain_policy": {"allowed_domains": ["allowed.example"]},
                    },
                }
            },
        )
    )

    assert result["sources"][0]["source_origin"] == "public_web"
    assert result["sources"][0]["access_channel"] == "search_api"
    assert result["sources"][0]["retrieval_method"] == "web_search"
    assert result["evidence_items"][0]["metadata"]["source_origin"] == "public_web"


def test_conduct_research_budget_uses_deep_complexity():
    from agent.core.configuration import ResearchConfiguration
    from agent.workflows.supervisor import _research_budget_for_call

    budget = _research_budget_for_call(
        {"args": {}},
        {"complexity": "deep", "estimated_depth": 3, "estimated_breadth": 6},
        ResearchConfiguration(max_concurrent_research_units=8, max_react_tool_calls=8),
    )

    assert budget["research_effort"] == "exhaustive"
    assert budget["thoroughness"] == "very_thorough"
    assert budget["depth"] >= 3
    assert budget["breadth"] >= 6
    assert budget["max_tool_calls"] >= 16


def test_conduct_research_effort_maps_legacy_values():
    from agent.core.configuration import ResearchConfiguration
    from agent.workflows.supervisor import _research_budget_for_call

    budget = _research_budget_for_call(
        {"args": {"thoroughness": "deep"}},
        {"complexity": "deep", "estimated_depth": 2, "estimated_breadth": 4},
        ResearchConfiguration(max_concurrent_research_units=8, max_react_tool_calls=8),
    )

    assert budget["research_effort"] == "thorough"
    assert budget["thoroughness"] == "deep"


def test_conduct_research_effort_caps_overbudget_standard_task():
    from agent.core.configuration import ResearchConfiguration
    from agent.workflows.supervisor import _research_budget_for_call

    budget = _research_budget_for_call(
        {"args": {"research_effort": "exhaustive"}},
        {"complexity": "standard", "estimated_depth": 1, "estimated_breadth": 2},
        ResearchConfiguration(max_concurrent_research_units=8, max_react_tool_calls=8),
    )

    assert budget["research_effort"] == "normal"
    assert budget["thoroughness"] == "medium"
    assert budget["max_tool_calls"] == 8


def test_conduct_research_effort_caps_simple_task_to_quick():
    from agent.core.configuration import ResearchConfiguration
    from agent.workflows.supervisor import _research_budget_for_call

    budget = _research_budget_for_call(
        {"args": {"research_effort": "exhaustive"}},
        {"complexity": "simple", "estimated_depth": 3, "estimated_breadth": 6},
        ResearchConfiguration(max_concurrent_research_units=8, max_react_tool_calls=8),
    )

    assert budget["research_effort"] == "quick"
    assert budget["max_tool_calls"] == 4


def test_run_manager_appends_and_replays_memory_events(monkeypatch):
    from agent.runtime import runs
    from agent.runtime.runs import RunManager

    monkeypatch.setattr(runs.settings, "database_url", "")
    monkeypatch.setattr(runs.settings, "memory_database_url", "")
    manager = RunManager()
    manager.start(run_id="run_a", thread_id="thread_a")

    manager.append_event(
        run_id="run_a",
        thread_id="thread_a",
        seq=1,
        type="plan_graph_update",
        payload={"type": "plan_graph_update", "data": {"ready": 1}},
    )
    manager.append_event(
        run_id="run_a",
        thread_id="thread_a",
        seq=2,
        type="done",
        payload={"type": "done"},
    )
    manager.append_event(
        run_id="run_b",
        thread_id="thread_a",
        seq=1,
        type="resume",
        payload={
            "type": "resume",
            "research_event": {"type": "resume", "sequence": 1},
        },
    )

    events = manager.events_after("thread_a", after_seq=1)
    assert [event["seq"] for event in events] == [2, 3]
    assert events[0]["type"] == "done"
    assert events[1]["run_id"] == "run_b"
    assert events[1]["payload"]["research_event"]["sequence"] == 3


def test_run_manager_start_preserves_existing_run_metadata(monkeypatch):
    from agent.runtime import runs
    from agent.runtime.runs import RunManager

    monkeypatch.setattr(runs.settings, "database_url", "")
    monkeypatch.setattr(runs.settings, "memory_database_url", "")
    manager = RunManager()
    first = manager.start(
        run_id="run_a",
        thread_id="thread_a",
        workspace={"root": "/tmp/a"},
        metadata={"background_request": {"thread_id": "thread_a"}},
    )
    second = manager.start(
        run_id="run_a",
        thread_id="thread_a",
        metadata={"input_preview": "hello"},
    )

    assert second.created_at == first.created_at
    assert second.workspace == {"root": "/tmp/a"}
    assert second.metadata["background_request"]["thread_id"] == "thread_a"
    assert second.metadata["input_preview"] == "hello"


def test_supervisor_completion_guard_blocks_open_todos_without_evidence():
    from agent.workflows.supervisor import _research_completion_guard

    guard = _research_completion_guard(
        {
            "complexity": "standard",
            "research_todos": [
                {
                    "id": "todo_1",
                    "title": "Check primary sources",
                    "status": "pending",
                }
            ],
            "notes": [],
            "raw_notes": [],
            "evidence_items": [],
        },
        [],
    )

    assert guard["allowed"] is False
    assert "Check primary sources" in guard["gaps"]
    assert any("pending or running" in reason for reason in guard["reasons"])


def test_supervisor_completion_guard_allows_completed_todos_with_evidence():
    from agent.workflows.supervisor import _research_completion_guard

    guard = _research_completion_guard(
        {
            "complexity": "deep",
            "research_todos": [
                {
                    "id": "todo_1",
                    "title": "Check primary sources",
                    "status": "completed",
                }
            ],
            "notes": [
                "The primary source confirms the important finding."
            ],
            "raw_notes": [],
            "evidence_items": [
                {
                    "url": "https://example.com/source",
                    "content": "Primary source evidence.",
                }
            ],
        },
        [],
    )

    assert guard["allowed"] is True
    assert guard["reasons"] == []
    assert guard["evidence_count"] == 1


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


def test_claim_citation_matrix_flags_missing_and_traceable_claims():
    from agent.workflows.citation_agent import build_claim_citation_matrix

    matrix = build_claim_citation_matrix(
        (
            "Revenue increased 20% in 2025 without a citation. "
            "The audited filing confirms operating margin improved [1]."
        ),
        [
            {
                "citation_index": 1,
                "traceable": True,
                "source_id": "src_1",
                "canonical_url": "https://example.com/filing",
                "matched_passages": [{"evidence_id": "ev_1"}],
            }
        ],
    )

    assert matrix["summary"]["claim_citation_total"] == 2
    assert matrix["summary"]["claim_citation_traceable"] == 1
    assert any(claim["status"] == "missing_citation" for claim in matrix["claims"])


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


def test_citation_gate_strict_requires_current_run_citations():
    from agent.workflows.evidence_ledger import evaluate_citation_gate

    result = evaluate_citation_gate(
        "Claim without citation.",
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
        require_citations=True,
    )

    assert result["passed"] is False
    assert "no numbered citations" in result["issues"][0]


def test_citation_gate_rejects_memory_only_binding():
    from agent.workflows.evidence_ledger import evaluate_citation_gate

    result = evaluate_citation_gate(
        "Claim with remembered source [1].",
        sources=[{
            "url": "https://example.com/memory",
            "title": "Memory",
            "source_id": "src_memory",
            "snippet_hash": "abc123",
            "source": "memory",
            "requires_current_run_verification": True,
        }],
        evidence_items=[{
            "url": "https://example.com/memory",
            "content": "memory evidence passage",
            "source_id": "src_memory",
            "snippet_hash": "abc123",
            "source": "memory",
            "requires_current_run_verification": True,
        }],
        passages=[{
            "url": "https://example.com/memory",
            "text": "memory evidence passage",
            "source_id": "src_memory",
            "snippet_hash": "abc123",
        }],
    )

    assert result["passed"] is False
    assert any("memory-only" in issue for issue in result["issues"])


def test_deep_read_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("SOULSEARCHER_RESEARCH_WORKSPACE_PATH", str(tmp_path))

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
        args={"query": "soulsearcher"},
        observation=(
            "[1] Source: https://example.com/soulsearcher\n"
            "SoulSearcher is a LangGraph-based research system with citations."
        ),
        research_topic="soulsearcher architecture",
    )
    evidence = _extract_evidence_from_observation(**kwargs)
    evidence_again = _extract_evidence_from_observation(**kwargs)

    assert evidence
    assert evidence[0]["id"] == evidence_again[0]["id"]
    assert evidence[0]["tool"] == "tavily_search"
    assert evidence[0]["query"] == "soulsearcher"
    assert evidence[0]["url"] == "https://example.com/soulsearcher"


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
    monkeypatch.setenv("SOULSEARCHER_RESEARCH_WORKSPACE_PATH", str(tmp_path))

    from agent.runtime.workspace import get_research_workspace

    workspace = get_research_workspace("thread/test")
    workspace.write_json("quality.json", {"passed": True})
    workspace.write_jsonl("evidence.jsonl", [{"id": "e1"}])

    assert (workspace.root / "quality.json").exists()
    assert (workspace.root / "evidence.jsonl").read_text(encoding="utf-8").strip()


def test_research_runtime_builder_creates_state_config_and_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("SOULSEARCHER_RESEARCH_WORKSPACE_PATH", str(tmp_path))

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
    monkeypatch.setenv("SOULSEARCHER_RESEARCH_WORKSPACE_PATH", str(tmp_path))

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


def test_research_runtime_builder_injects_user_sources_into_hidden_context(tmp_path, monkeypatch):
    monkeypatch.setenv("SOULSEARCHER_RESEARCH_WORKSPACE_PATH", str(tmp_path))

    from agent.runtime.request_builder import (
        ResearchRuntimeRequest,
        build_research_runtime,
    )

    source = {
        "url": "https://example.com/user-source",
        "title": "User source",
        "source": "user",
    }
    bundle = build_research_runtime(
        ResearchRuntimeRequest(
            input_text="query",
            thread_id="thread_user_source",
            model="test-model",
            mode_info={"mode": "deep"},
            user_id="u1",
            images=[],
            research_brief=None,
            context_messages=[],
            deepsearch_config={"user_injected_sources": [source]},
            base_configurable={"thread_id": "thread_user_source"},
        )
    )

    assert bundle.initial_state["sources"] == [source]
    assert bundle.initial_state["deepsearch_artifacts"]["user_injected_sources"] == [source]
    assert any(
        getattr(message, "additional_kwargs", {}).get("user_injected_sources")
        for message in bundle.initial_state["messages"]
    )


def test_runtime_token_tracker_is_run_scoped():
    from agent.core.middleware import get_token_tracker
    from agent.runtime.context import RuntimeContext

    first = {"configurable": {"runtime_context": RuntimeContext(thread_id="a")}}
    second = {"configurable": {"runtime_context": RuntimeContext(thread_id="b")}}

    get_token_tracker(first).record("research", 1, 2)

    assert get_token_tracker(first).get_summary()["total_tokens"] == 3
    assert get_token_tracker(second).get_summary()["total_tokens"] == 0


def test_run_status_accepts_background_states():
    from agent.runtime.runs import RunRecord, RunStatus

    record = RunRecord.from_dict(
        {
            "run_id": "r1",
            "thread_id": "t1",
            "status": "queued",
        }
    )

    assert record.status is RunStatus.queued
    assert record.to_dict()["status"] == "queued"


def test_background_run_request_roundtrip_and_webhook_url_validation():
    from agent.runtime.background_runs import BackgroundRunRequest, _safe_webhook_url

    request = BackgroundRunRequest(
        input_text="query",
        thread_id="thread_bg",
        model="model",
        search_mode={"mode": "deep"},
        deepsearch_config={"webhook_url": "https://example.com/hook"},
    )
    restored = BackgroundRunRequest.from_dict(request.to_dict())

    assert restored.input_text == "query"
    assert restored.search_mode == {"mode": "deep"}
    assert _safe_webhook_url("https://example.com/hook") is True
    assert _safe_webhook_url("file:///tmp/hook") is False


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
    cache.set("soulsearcher search", [{"title": "a"}])
    assert cache.get("soulsearcher search") == [{"title": "a"}]
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
