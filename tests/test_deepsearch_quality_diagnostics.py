from datetime import datetime, timedelta, timezone

from agent.workflows import deepsearch_optimized


def _patch_basics(monkeypatch, search_results):
    monkeypatch.setattr(
        deepsearch_optimized, "_model_for_task", lambda task, config: "fake-model"
    )
    monkeypatch.setattr(
        deepsearch_optimized, "_chat_model", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(
        deepsearch_optimized, "_resolve_provider_profile", lambda state: None
    )
    monkeypatch.setattr(
        deepsearch_optimized, "_generate_queries", lambda *args, **kwargs: [args[1]]
    )
    monkeypatch.setattr(
        deepsearch_optimized, "_search_query", lambda *args, **kwargs: search_results
    )
    monkeypatch.setattr(
        deepsearch_optimized,
        "_pick_relevant_urls",
        lambda *args, **kwargs: [r["url"] for r in search_results[:3]],
    )
    monkeypatch.setattr(
        deepsearch_optimized,
        "_summarize_new_knowledge",
        lambda *args, **kwargs: (True, "summary"),
    )
    monkeypatch.setattr(
        deepsearch_optimized, "_final_report", lambda *args, **kwargs: "final report"
    )
    monkeypatch.setattr(
        deepsearch_optimized, "_save_deepsearch_data", lambda *args, **kwargs: ""
    )

    monkeypatch.setattr(
        deepsearch_optimized.settings, "deepsearch_enable_crawler", False, raising=False
    )
    monkeypatch.setattr(
        deepsearch_optimized.settings,
        "deepsearch_use_gap_analysis",
        False,
        raising=False,
    )
    monkeypatch.setattr(
        deepsearch_optimized.settings, "deepsearch_max_epochs", 2, raising=False
    )
    monkeypatch.setattr(
        deepsearch_optimized.settings, "deepsearch_query_num", 1, raising=False
    )
    monkeypatch.setattr(
        deepsearch_optimized.settings, "deepsearch_results_per_query", 3, raising=False
    )
    monkeypatch.setattr(
        deepsearch_optimized.settings, "deepsearch_max_tokens", 0, raising=False
    )
    monkeypatch.setattr(
        deepsearch_optimized.settings, "deepsearch_max_seconds", 0.0, raising=False
    )
    monkeypatch.setattr(
        deepsearch_optimized.settings, "agent_reflexion_enabled", False, raising=False
    )
    monkeypatch.setattr(
        deepsearch_optimized.settings, "tree_backtrack_enabled", False, raising=False
    )


def test_quality_summary_exposes_query_and_freshness_diagnostics(monkeypatch):
    now = datetime.now(timezone.utc)
    search_results = [
        {
            "title": "Recent",
            "url": "https://example.com/recent",
            "summary": "recent",
            "score": 0.8,
            "published_date": (now - timedelta(days=3)).isoformat(),
        },
        {
            "title": "Old",
            "url": "https://example.com/old",
            "summary": "old",
            "score": 0.6,
            "published_date": (now - timedelta(days=90)).isoformat(),
        },
        {
            "title": "Unknown",
            "url": "https://example.com/unknown",
            "summary": "unknown",
            "score": 0.5,
        },
    ]

    _patch_basics(monkeypatch, search_results)

    result = deepsearch_optimized.run_deepsearch_optimized(
        {"input": "enterprise knowledge management"},
        config={},
    )

    quality = result["quality_summary"]

    assert "query_coverage_score" in quality
    assert "query_dimensions_covered" in quality
    assert "freshness_summary" in quality
    assert quality["freshness_summary"]["total_results"] == 3
    assert quality["freshness_warning"] == ""
    assert (
        result["deepsearch_artifacts"]["query_coverage"]["score"]
        == quality["query_coverage_score"]
    )


def test_time_sensitive_topics_trigger_low_freshness_warning(monkeypatch):
    now = datetime.now(timezone.utc)
    search_results = [
        {
            "title": "Old1",
            "url": "https://example.com/old1",
            "summary": "old1",
            "score": 0.8,
            "published_date": (now - timedelta(days=320)).isoformat(),
        },
        {
            "title": "Old2",
            "url": "https://example.com/old2",
            "summary": "old2",
            "score": 0.7,
            "published_date": (now - timedelta(days=480)).isoformat(),
        },
        {
            "title": "Old3",
            "url": "https://example.com/old3",
            "summary": "old3",
            "score": 0.65,
            "published_date": (now - timedelta(days=220)).isoformat(),
        },
    ]

    _patch_basics(monkeypatch, search_results)

    result = deepsearch_optimized.run_deepsearch_optimized(
        {"input": "latest ai regulation updates"},
        config={},
    )

    quality = result["quality_summary"]
    messages = result["messages"]

    assert quality["time_sensitive_query"] is True
    assert quality["freshness_summary"]["known_count"] == 3
    assert quality["freshness_warning"] == "low_freshness_for_time_sensitive_query"
    assert any("新鲜来源占比较低" in (msg.content or "") for msg in messages)


def test_freshness_warning_respects_min_known_threshold(monkeypatch):
    now = datetime.now(timezone.utc)
    search_results = [
        {
            "title": "Old1",
            "url": "https://example.com/old1",
            "summary": "old1",
            "score": 0.8,
            "published_date": (now - timedelta(days=320)).isoformat(),
        },
        {
            "title": "Old2",
            "url": "https://example.com/old2",
            "summary": "old2",
            "score": 0.7,
            "published_date": (now - timedelta(days=480)).isoformat(),
        },
        {
            "title": "Old3",
            "url": "https://example.com/old3",
            "summary": "old3",
            "score": 0.65,
            "published_date": (now - timedelta(days=220)).isoformat(),
        },
    ]

    _patch_basics(monkeypatch, search_results)
    monkeypatch.setattr(
        deepsearch_optimized.settings,
        "deepsearch_freshness_warning_min_known",
        5,
        raising=False,
    )

    result = deepsearch_optimized.run_deepsearch_optimized(
        {"input": "latest ai regulation updates"},
        config={},
    )

    quality = result["quality_summary"]

    assert quality["time_sensitive_query"] is True
    assert quality["freshness_summary"]["known_count"] == 3
    assert quality["freshness_warning"] == ""


def test_deepsearch_emits_search_and_quality_update_events(monkeypatch):
    now = datetime.now(timezone.utc)
    search_results = [
        {
            "title": "Recent",
            "url": "https://example.com/recent",
            "summary": "recent",
            "score": 0.8,
            "provider": "serper",
            "published_date": (now - timedelta(days=3)).isoformat(),
        },
        {
            "title": "Older",
            "url": "https://example.com/older",
            "summary": "older",
            "score": 0.7,
            "provider": "serper",
            "published_date": (now - timedelta(days=90)).isoformat(),
        },
    ]

    _patch_basics(monkeypatch, search_results)

    emitted = []

    class DummyEmitter:
        def emit_sync(self, event_type, data):
            event_name = (
                event_type.value if hasattr(event_type, "value") else str(event_type)
            )
            emitted.append((event_name, data))

    monkeypatch.setattr(
        deepsearch_optimized,
        "_resolve_event_emitter",
        lambda state, config: DummyEmitter(),
    )

    result = deepsearch_optimized.run_deepsearch_optimized(
        {"input": "latest ai policy updates"},
        config={"configurable": {"thread_id": "thread_test"}},
    )

    event_types = [name for name, _ in emitted]
    search_events = [data for name, data in emitted if name == "search"]
    quality_events = [data for name, data in emitted if name == "quality_update"]

    assert result["quality_summary"]["time_sensitive_query"] is True
    assert "search" in event_types
    assert "quality_update" in event_types
    assert search_events
    assert search_events[0]["count"] == len(search_results)
    assert search_events[0]["provider"] == "serper"
    assert "query_coverage_score" in quality_events[-1]
    assert any(event.get("stage") == "epoch" for event in quality_events)


def test_deepsearch_emits_epoch_lifecycle_events(monkeypatch):
    now = datetime.now(timezone.utc)
    search_results = [
        {
            "title": "Recent",
            "url": "https://example.com/recent",
            "summary": "recent",
            "score": 0.8,
            "provider": "serper",
            "published_date": (now - timedelta(days=3)).isoformat(),
        }
    ]

    _patch_basics(monkeypatch, search_results)

    emitted = []

    class DummyEmitter:
        def emit_sync(self, event_type, data):
            event_name = (
                event_type.value if hasattr(event_type, "value") else str(event_type)
            )
            emitted.append((event_name, data))

    monkeypatch.setattr(
        deepsearch_optimized,
        "_resolve_event_emitter",
        lambda state, config: DummyEmitter(),
    )

    deepsearch_optimized.run_deepsearch_optimized(
        {"input": "latest ai policy updates"},
        config={"configurable": {"thread_id": "thread_test"}},
    )

    start_events = [data for name, data in emitted if name == "research_node_start"]
    complete_events = [
        data for name, data in emitted if name == "research_node_complete"
    ]

    assert start_events
    assert complete_events
    assert start_events[0]["node_id"] == "deepsearch_epoch_1"
    assert complete_events[0]["node_id"] == "deepsearch_epoch_1"


def test_deepsearch_emits_quality_update_even_when_epoch_has_no_results(monkeypatch):
    _patch_basics(monkeypatch, [])

    emitted = []

    class DummyEmitter:
        def emit_sync(self, event_type, data):
            event_name = (
                event_type.value if hasattr(event_type, "value") else str(event_type)
            )
            emitted.append((event_name, data))

    monkeypatch.setattr(
        deepsearch_optimized,
        "_resolve_event_emitter",
        lambda state, config: DummyEmitter(),
    )

    deepsearch_optimized.run_deepsearch_optimized(
        {"input": "latest ai policy updates"},
        config={"configurable": {"thread_id": "thread_test"}},
    )

    quality_events = [data for name, data in emitted if name == "quality_update"]

    assert quality_events
    assert quality_events[0]["epoch"] == 1


def test_deepsearch_tree_emits_search_quality_and_tree_events(monkeypatch):
    _patch_basics(monkeypatch, [])
    monkeypatch.setattr(
        deepsearch_optimized.settings, "tree_parallel_branches", 0, raising=False
    )

    class FakeNode:
        def __init__(self):
            self.id = "n1"
            self.topic = "subtopic"
            self.queries = ["tree query"]
            self.findings = [
                {
                    "query": "tree query",
                    "result": {
                        "title": "Tree source",
                        "url": "https://example.com/tree",
                        "provider": "serper",
                        "published_date": datetime.now(timezone.utc).isoformat(),
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            ]

    class FakeTree:
        def __init__(self):
            self.nodes = {"n1": FakeNode()}

        def to_dict(self):
            return {"id": "root", "children": ["n1"]}

    class FakeTreeExplorer:
        def __init__(self, *args, **kwargs):
            self._tree = FakeTree()

        def run(self, topic, state, decompose_root=True):
            return self._tree

        def get_final_summary(self):
            return "tree summary"

        def get_all_sources(self):
            return ["https://example.com/tree"]

        def get_all_findings(self):
            return self._tree.nodes["n1"].findings

        def get_backtrack_events(self):
            return []

    emitted = []

    class DummyEmitter:
        def emit_sync(self, event_type, data):
            event_name = (
                event_type.value if hasattr(event_type, "value") else str(event_type)
            )
            emitted.append((event_name, data))

    monkeypatch.setattr(deepsearch_optimized, "TreeExplorer", FakeTreeExplorer)
    monkeypatch.setattr(
        deepsearch_optimized,
        "_resolve_event_emitter",
        lambda state, config: DummyEmitter(),
    )

    result = deepsearch_optimized.run_deepsearch_tree(
        {"input": "latest ai policy updates"},
        config={"configurable": {"thread_id": "thread_test"}},
    )

    event_types = [name for name, _ in emitted]
    assert result["deepsearch_mode"] == "tree"
    assert "search" in event_types
    assert "quality_update" in event_types
    assert "research_tree_update" in event_types


def test_deepsearch_tree_emits_search_during_tree_execution(monkeypatch):
    _patch_basics(monkeypatch, [])
    monkeypatch.setattr(
        deepsearch_optimized.settings, "tree_parallel_branches", 0, raising=False
    )

    emitted = []
    search_seen_during_run = {"value": False}

    class DummyEmitter:
        def emit_sync(self, event_type, data):
            event_name = (
                event_type.value if hasattr(event_type, "value") else str(event_type)
            )
            emitted.append((event_name, data))

    class FakeNode:
        def __init__(self):
            self.id = "n1"
            self.topic = "subtopic"
            self.queries = ["tree query"]
            self.findings = [
                {
                    "query": "tree query",
                    "result": {
                        "title": "Tree source",
                        "url": "https://example.com/tree",
                        "provider": "serper",
                        "published_date": datetime.now(timezone.utc).isoformat(),
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            ]

    class FakeTree:
        def __init__(self):
            self.nodes = {"n1": FakeNode()}

        def to_dict(self):
            return {"id": "root", "children": ["n1"]}

    class FakeTreeExplorer:
        def __init__(self, *args, **kwargs):
            self._tree = FakeTree()
            self.search_func = kwargs["search_func"]

        def run(self, topic, state, decompose_root=True):
            self.search_func({"query": "tree query", "max_results": 1}, config={})
            search_seen_during_run["value"] = any(
                name == "search" for name, _ in emitted
            )
            return self._tree

        def get_final_summary(self):
            return "tree summary"

        def get_all_sources(self):
            return ["https://example.com/tree"]

        def get_all_findings(self):
            return self._tree.nodes["n1"].findings

        def get_backtrack_events(self):
            return []

    monkeypatch.setattr(
        deepsearch_optimized,
        "_search_query",
        lambda *args, **kwargs: [
            {
                "title": "Tree source",
                "url": "https://example.com/tree",
                "provider": "serper",
                "published_date": datetime.now(timezone.utc).isoformat(),
            }
        ],
    )
    monkeypatch.setattr(deepsearch_optimized, "TreeExplorer", FakeTreeExplorer)
    monkeypatch.setattr(
        deepsearch_optimized,
        "_resolve_event_emitter",
        lambda state, config: DummyEmitter(),
    )

    deepsearch_optimized.run_deepsearch_tree(
        {"input": "latest ai policy updates"},
        config={"configurable": {"thread_id": "thread_test"}},
    )

    assert search_seen_during_run["value"] is True


def test_deepsearch_linear_respects_single_query_budget(monkeypatch):
    _patch_basics(monkeypatch, [])
    searched_queries = []

    monkeypatch.setattr(
        deepsearch_optimized,
        "_generate_queries",
        lambda *args, **kwargs: ["capital of France official name and history"],
    )
    monkeypatch.setattr(
        deepsearch_optimized,
        "_search_query",
        lambda query, *args, **kwargs: searched_queries.append(query)
        or [
            {
                "title": "Paris",
                "url": "https://example.com/paris",
                "summary": "Paris",
                "score": 0.9,
                "provider": "serper",
                "published_date": datetime.now(timezone.utc).isoformat(),
            }
        ],
    )

    deepsearch_optimized.run_deepsearch_optimized(
        {"input": "What is the capital of France?"},
        config={
            "configurable": {"deepsearch_query_num": 1, "deepsearch_max_epochs": 1}
        },
    )

    assert searched_queries == ["capital of France official name and history"]


def test_deepsearch_linear_can_disable_browser_visualization(monkeypatch):
    _patch_basics(monkeypatch, [])
    browser_calls = []

    monkeypatch.setattr(
        deepsearch_optimized,
        "_generate_queries",
        lambda *args, **kwargs: ["capital of France official name and history"],
    )
    monkeypatch.setattr(
        deepsearch_optimized,
        "_search_query",
        lambda *args, **kwargs: [
            {
                "title": "Paris",
                "url": "https://example.com/paris",
                "summary": "Paris",
                "score": 0.9,
                "provider": "serper",
                "published_date": datetime.now(timezone.utc).isoformat(),
            }
        ],
    )

    from agent.workflows import browser_visualizer

    monkeypatch.setattr(
        browser_visualizer,
        "show_browser_status_page",
        lambda *args, **kwargs: browser_calls.append("status"),
    )
    monkeypatch.setattr(
        browser_visualizer,
        "visualize_urls_from_results",
        lambda *args, **kwargs: browser_calls.append("results"),
    )
    monkeypatch.setattr(
        browser_visualizer,
        "visualize_urls",
        lambda *args, **kwargs: browser_calls.append("selected"),
    )

    deepsearch_optimized.run_deepsearch_optimized(
        {"input": "What is the capital of France?"},
        config={
            "configurable": {
                "deepsearch_query_num": 1,
                "deepsearch_max_epochs": 1,
                "deepsearch_visualize_browser": False,
            }
        },
    )

    assert browser_calls == []


def test_deepsearch_search_event_respects_result_limit_setting(monkeypatch):
    now = datetime.now(timezone.utc)
    search_results = [
        {
            "title": "S1",
            "url": "https://example.com/1",
            "summary": "s1",
            "score": 0.9,
            "provider": "serper",
            "published_date": (now - timedelta(days=1)).isoformat(),
        },
        {
            "title": "S2",
            "url": "https://example.com/2",
            "summary": "s2",
            "score": 0.8,
            "provider": "serper",
            "published_date": (now - timedelta(days=2)).isoformat(),
        },
        {
            "title": "S3",
            "url": "https://example.com/3",
            "summary": "s3",
            "score": 0.7,
            "provider": "serper",
            "published_date": (now - timedelta(days=3)).isoformat(),
        },
    ]

    _patch_basics(monkeypatch, search_results)
    monkeypatch.setattr(
        deepsearch_optimized.settings,
        "deepsearch_event_results_limit",
        1,
        raising=False,
    )

    emitted = []

    class DummyEmitter:
        def emit_sync(self, event_type, data):
            event_name = (
                event_type.value if hasattr(event_type, "value") else str(event_type)
            )
            emitted.append((event_name, data))

    monkeypatch.setattr(
        deepsearch_optimized,
        "_resolve_event_emitter",
        lambda state, config: DummyEmitter(),
    )

    deepsearch_optimized.run_deepsearch_optimized(
        {"input": "latest ai policy updates"},
        config={"configurable": {"thread_id": "thread_test"}},
    )

    search_events = [data for name, data in emitted if name == "search"]

    assert search_events
    assert len(search_events[0]["results"]) == 1


def test_deepsearch_linear_complete_event_dedupes_canonical_source_urls(monkeypatch):
    now = datetime.now(timezone.utc)
    search_results = [
        {
            "title": "S1",
            "url": "https://example.com/a?utm_source=mail",
            "summary": "s1",
            "score": 0.9,
            "provider": "serper",
            "published_date": (now - timedelta(days=1)).isoformat(),
        },
        {
            "title": "S2",
            "url": "https://EXAMPLE.com/a/",
            "summary": "s2",
            "score": 0.8,
            "provider": "serper",
            "published_date": (now - timedelta(days=2)).isoformat(),
        },
    ]

    _patch_basics(monkeypatch, search_results)

    emitted = []

    class DummyEmitter:
        def emit_sync(self, event_type, data):
            event_name = (
                event_type.value if hasattr(event_type, "value") else str(event_type)
            )
            emitted.append((event_name, data))

    monkeypatch.setattr(
        deepsearch_optimized,
        "_resolve_event_emitter",
        lambda state, config: DummyEmitter(),
    )

    deepsearch_optimized.run_deepsearch_optimized(
        {"input": "latest ai policy updates"},
        config={"configurable": {"thread_id": "thread_test"}},
    )

    complete_events = [
        data for name, data in emitted if name == "research_node_complete"
    ]
    assert complete_events
    urls = [src.get("url") for src in complete_events[-1].get("sources", [])]
    assert urls == ["https://example.com/a"]


def test_deepsearch_tree_complete_event_dedupes_canonical_source_urls(monkeypatch):
    _patch_basics(monkeypatch, [])
    monkeypatch.setattr(
        deepsearch_optimized.settings, "tree_parallel_branches", 0, raising=False
    )

    class FakeNode:
        def __init__(self):
            self.id = "n1"
            self.topic = "subtopic"
            self.queries = ["tree query"]
            self.findings = [
                {
                    "query": "tree query",
                    "result": {
                        "title": "Tree source A",
                        "url": "https://example.com/tree?utm_source=feed",
                        "provider": "serper",
                        "published_date": datetime.now(timezone.utc).isoformat(),
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                {
                    "query": "tree query",
                    "result": {
                        "title": "Tree source B",
                        "url": "https://EXAMPLE.com/tree/",
                        "provider": "serper",
                        "published_date": datetime.now(timezone.utc).isoformat(),
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            ]

    class FakeTree:
        def __init__(self):
            self.nodes = {"n1": FakeNode()}

        def to_dict(self):
            return {"id": "root", "children": ["n1"]}

    class FakeTreeExplorer:
        def __init__(self, *args, **kwargs):
            self._tree = FakeTree()

        def run(self, topic, state, decompose_root=True):
            return self._tree

        def get_final_summary(self):
            return "tree summary"

        def get_all_sources(self):
            return [
                "https://example.com/tree?utm_source=feed",
                "https://EXAMPLE.com/tree/",
            ]

        def get_all_findings(self):
            return self._tree.nodes["n1"].findings

        def get_backtrack_events(self):
            return []

    emitted = []

    class DummyEmitter:
        def emit_sync(self, event_type, data):
            event_name = (
                event_type.value if hasattr(event_type, "value") else str(event_type)
            )
            emitted.append((event_name, data))

    monkeypatch.setattr(deepsearch_optimized, "TreeExplorer", FakeTreeExplorer)
    monkeypatch.setattr(
        deepsearch_optimized,
        "_resolve_event_emitter",
        lambda state, config: DummyEmitter(),
    )

    deepsearch_optimized.run_deepsearch_tree(
        {"input": "latest ai policy updates"},
        config={"configurable": {"thread_id": "thread_test"}},
    )

    complete_events = [
        data for name, data in emitted if name == "research_node_complete"
    ]
    assert complete_events
    urls = [src.get("url") for src in complete_events[-1].get("sources", [])]
    assert urls == ["https://example.com/tree"]


def test_deepsearch_linear_quality_counts_use_canonical_url_dedup(monkeypatch):
    now = datetime.now(timezone.utc)
    search_results = [
        {
            "title": "S1",
            "url": "https://example.com/a?utm_source=mail",
            "summary": "s1",
            "score": 0.9,
            "provider": "serper",
            "published_date": (now - timedelta(days=1)).isoformat(),
        },
        {
            "title": "S2",
            "url": "https://EXAMPLE.com/a/",
            "summary": "s2",
            "score": 0.8,
            "provider": "serper",
            "published_date": (now - timedelta(days=2)).isoformat(),
        },
    ]

    _patch_basics(monkeypatch, search_results)

    result = deepsearch_optimized.run_deepsearch_optimized(
        {"input": "latest ai policy updates"},
        config={},
    )

    quality = result["quality_summary"]

    assert quality["source_count"] == 1
    assert quality["selected_url_count"] == 1


def test_deepsearch_tree_quality_source_count_uses_canonical_dedup(monkeypatch):
    _patch_basics(monkeypatch, [])
    monkeypatch.setattr(
        deepsearch_optimized.settings, "tree_parallel_branches", 0, raising=False
    )

    class FakeNode:
        def __init__(self):
            self.id = "n1"
            self.topic = "subtopic"
            self.queries = ["tree query"]
            self.findings = [
                {
                    "query": "tree query",
                    "result": {
                        "title": "Tree source A",
                        "url": "https://example.com/tree?utm_source=feed",
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                {
                    "query": "tree query",
                    "result": {
                        "title": "Tree source B",
                        "url": "https://EXAMPLE.com/tree/",
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            ]

    class FakeTree:
        def __init__(self):
            self.nodes = {"n1": FakeNode()}

        def to_dict(self):
            return {"id": "root", "children": ["n1"]}

    class FakeTreeExplorer:
        def __init__(self, *args, **kwargs):
            self._tree = FakeTree()

        def run(self, topic, state, decompose_root=True):
            return self._tree

        def get_final_summary(self):
            return "tree summary"

        def get_all_sources(self):
            return [
                "https://example.com/tree?utm_source=feed",
                "https://EXAMPLE.com/tree/",
            ]

        def get_all_findings(self):
            return self._tree.nodes["n1"].findings

        def get_backtrack_events(self):
            return []

    monkeypatch.setattr(deepsearch_optimized, "TreeExplorer", FakeTreeExplorer)

    result = deepsearch_optimized.run_deepsearch_tree(
        {"input": "latest ai policy updates"},
        config={},
    )

    assert result["quality_summary"]["source_count"] == 1


def test_deepsearch_tree_budget_stop_includes_diagnostics(monkeypatch):
    _patch_basics(monkeypatch, [])
    monkeypatch.setattr(
        deepsearch_optimized.settings, "deepsearch_max_seconds", 0.0, raising=False
    )
    monkeypatch.setattr(
        deepsearch_optimized.settings, "deepsearch_max_tokens", 1, raising=False
    )

    result = deepsearch_optimized.run_deepsearch_tree(
        {"input": "latest ai policy updates"},
        config={"configurable": {"thread_id": "thread_test"}},
    )

    quality = result["quality_summary"]
    artifacts = result["deepsearch_artifacts"]

    assert result["deepsearch_mode"] == "tree"
    assert quality["budget_stop_reason"] == "token_budget_exceeded"
    assert "query_coverage_score" in quality
    assert "freshness_summary" in quality
    assert "query_coverage" in artifacts
    assert "freshness_summary" in artifacts


def test_deepsearch_tree_budget_stop_emits_quality_event(monkeypatch):
    _patch_basics(monkeypatch, [])
    monkeypatch.setattr(
        deepsearch_optimized.settings, "deepsearch_max_seconds", 0.0, raising=False
    )
    monkeypatch.setattr(
        deepsearch_optimized.settings, "deepsearch_max_tokens", 1, raising=False
    )

    emitted = []

    class DummyEmitter:
        def emit_sync(self, event_type, data):
            event_name = (
                event_type.value if hasattr(event_type, "value") else str(event_type)
            )
            emitted.append((event_name, data))

    monkeypatch.setattr(
        deepsearch_optimized,
        "_resolve_event_emitter",
        lambda state, config: DummyEmitter(),
    )

    deepsearch_optimized.run_deepsearch_tree(
        {"input": "latest ai policy updates"},
        config={"configurable": {"thread_id": "thread_test"}},
    )

    event_types = [name for name, _ in emitted]
    quality_events = [data for name, data in emitted if name == "quality_update"]

    assert "quality_update" in event_types
    assert "research_node_complete" in event_types
    assert quality_events
    assert quality_events[0].get("stage") == "budget_stop"


def test_merge_focus_hints_dedupes_and_limits_items():
    merged = deepsearch_optimized._merge_focus_hints(
        ["近期更新", "监管审批"],
        ["监管审批", "交易估值"],
        "交易估值",
        max_items=3,
    )

    assert merged == ["近期更新", "监管审批", "交易估值"]


def test_build_feature_trace_includes_reflexion_and_backtrack_flags(monkeypatch):
    monkeypatch.setattr(
        deepsearch_optimized.settings, "tree_exploration_enabled", True, raising=False
    )
    monkeypatch.setattr(
        deepsearch_optimized.settings, "observation_masking", True, raising=False
    )
    monkeypatch.setattr(
        deepsearch_optimized.settings, "context_offloading", True, raising=False
    )
    monkeypatch.setattr(
        deepsearch_optimized.settings, "agent_reflexion_enabled", True, raising=False
    )
    monkeypatch.setattr(
        deepsearch_optimized.settings, "dynamic_tool_pruning", True, raising=False
    )
    monkeypatch.setattr(
        deepsearch_optimized.settings, "tree_backtrack_enabled", True, raising=False
    )

    trace = deepsearch_optimized._build_feature_trace(
        {"route": "deep"},
        {
            "configurable": {
                "deepsearch_mode": "auto",
                "resolved_route": "deep",
                "search_mode": {"route": "deep"},
            }
        },
        executed_mode="tree",
        reflexion_feedbacks=["feedback"],
        reflexion_focus=["监管审批", "估值趋势"],
        backtrack_events=[{"node_id": "n1"}],
    )

    assert trace["configured_mode"] == "auto"
    assert trace["executed_mode"] == "tree"
    assert trace["resolved_route"] == "deep"
    assert trace["reflexion_triggered"] is True
    assert trace["reflexion_rounds"] == 1
    assert trace["reflexion_focus_preview"] == ["监管审批", "估值趋势"]
    assert trace["tree_backtrack_triggered"] is True
    assert trace["tree_backtrack_events"] == 1
    assert trace["observation_masking_enabled"] is True
    assert trace["context_offloading_enabled"] is True
    assert trace["dynamic_tool_pruning_enabled"] is True
