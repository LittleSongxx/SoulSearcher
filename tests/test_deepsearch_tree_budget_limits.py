from agent.workflows import deepsearch_optimized


def test_deepsearch_tree_stops_after_search_budget(monkeypatch):
    # Avoid real LLM calls and file writes.
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
        deepsearch_optimized, "_final_report", lambda *args, **kwargs: "final report"
    )
    monkeypatch.setattr(
        deepsearch_optimized, "_save_deepsearch_data", lambda *args, **kwargs: ""
    )

    monkeypatch.setattr(
        deepsearch_optimized.settings, "tree_parallel_branches", 0, raising=False
    )
    monkeypatch.setattr(
        deepsearch_optimized.settings, "deepsearch_max_seconds", 0.0, raising=False
    )
    monkeypatch.setattr(
        deepsearch_optimized.settings, "deepsearch_max_tokens", 0, raising=False
    )
    monkeypatch.setattr(
        deepsearch_optimized.settings, "deepsearch_tree_max_searches", 2, raising=False
    )
    monkeypatch.setattr(
        deepsearch_optimized.settings, "agent_reflexion_enabled", False, raising=False
    )

    search_calls: list[str] = []

    def fake_search_query(query, max_results, config, provider_profile=None):
        _ = max_results, config, provider_profile
        search_calls.append(str(query))
        return [
            {
                "title": "Result",
                "url": f"https://example.com/{len(search_calls)}",
                "summary": "snippet",
                "provider": "serper",
            }
        ]

    monkeypatch.setattr(deepsearch_optimized, "_search_query", fake_search_query)

    class FakeNode:
        def __init__(self, node_id: str):
            self.id = node_id
            self.topic = "subtopic"
            self.queries = []
            self.findings = []

    class FakeTree:
        def __init__(self):
            self.nodes = {"n1": FakeNode("n1")}

        def to_dict(self):
            return {"id": "root", "children": ["n1"]}

    class FakeTreeExplorer:
        def __init__(self, *args, **kwargs):
            self.search_func = kwargs["search_func"]
            self.tree = FakeTree()

        def run(self, topic, state, decompose_root=True):
            _ = topic, state, decompose_root
            # Simulate a planner that issues far more queries than the budget.
            for i in range(5):
                self.search_func({"query": f"q{i}", "max_results": 1}, config={})
            return self.tree

        def get_final_summary(self):
            return "tree summary"

        def get_all_sources(self):
            return []

        def get_all_findings(self):
            return []

        def get_backtrack_events(self):
            return []

    monkeypatch.setattr(deepsearch_optimized, "TreeExplorer", FakeTreeExplorer)

    result = deepsearch_optimized.run_deepsearch_tree(
        {"input": "Analyze the current state of AI Agent frameworks in 2024"},
        config={"configurable": {"thread_id": "thread_test"}},
    )

    assert len(search_calls) == 2
    assert result["budget_stop_reason"] == "search_budget_exceeded"


def test_deepsearch_tree_late_failure_returns_partial_result_without_linear_fallback(
    monkeypatch,
):
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
        deepsearch_optimized, "_save_deepsearch_data", lambda *args, **kwargs: ""
    )
    monkeypatch.setattr(
        deepsearch_optimized.settings, "tree_parallel_branches", 0, raising=False
    )
    monkeypatch.setattr(
        deepsearch_optimized.settings, "deepsearch_max_seconds", 0.0, raising=False
    )
    monkeypatch.setattr(
        deepsearch_optimized.settings, "deepsearch_max_tokens", 0, raising=False
    )
    monkeypatch.setattr(
        deepsearch_optimized.settings, "deepsearch_tree_max_searches", 10, raising=False
    )
    monkeypatch.setattr(
        deepsearch_optimized.settings, "agent_reflexion_enabled", False, raising=False
    )

    search_calls = []
    linear_fallback_calls = {"count": 0}

    def fake_search_query(query, max_results, config, provider_profile=None):
        _ = max_results, config, provider_profile
        search_calls.append(str(query))
        return [
            {
                "title": "Tree Result",
                "url": "https://example.com/tree",
                "summary": "snippet",
                "provider": "serper",
            }
        ]

    def fake_linear(*args, **kwargs):
        linear_fallback_calls["count"] += 1
        return {"deepsearch_mode": "linear"}

    monkeypatch.setattr(deepsearch_optimized, "_search_query", fake_search_query)
    monkeypatch.setattr(deepsearch_optimized, "run_deepsearch_optimized", fake_linear)

    class FakeNode:
        def __init__(self):
            self.id = "n1"
            self.topic = "subtopic"
            self.queries = ["q0"]
            self.findings = []

    class FakeTree:
        def __init__(self):
            self.nodes = {"n1": FakeNode()}

        def to_dict(self):
            return {"id": "root", "children": ["n1"]}

    class FakeTreeExplorer:
        def __init__(self, *args, **kwargs):
            self.search_func = kwargs["search_func"]
            self.tree = FakeTree()

        def run(self, topic, state, decompose_root=True):
            _ = topic, state, decompose_root
            self.search_func({"query": "q0", "max_results": 1}, config={})
            return self.tree

        def get_final_summary(self):
            raise RuntimeError("late tree finalize failure")

        def get_all_sources(self):
            return []

        def get_all_findings(self):
            return []

        def get_backtrack_events(self):
            return []

    monkeypatch.setattr(deepsearch_optimized, "TreeExplorer", FakeTreeExplorer)

    result = deepsearch_optimized.run_deepsearch_tree(
        {"input": "Analyze the current state of AI Agent frameworks in 2024"},
        config={"configurable": {"thread_id": "thread_test"}},
    )

    assert search_calls == ["q0"]
    assert linear_fallback_calls["count"] == 0
    assert result["deepsearch_mode"] == "tree"
    assert result["budget_stop_reason"] == "tree_runtime_error"
    assert result["errors"] == ["late tree finalize failure"]
    assert result["research_plan"] == ["q0"]
    assert result["scraped_content"]
    assert result["quality_summary"]["tree_error"] == "late tree finalize failure"
