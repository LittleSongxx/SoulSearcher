import sys
import types

from agent.workflows.research_tree import ResearchTreeNode, TreeExplorer


class _DummyResponse:
    def __init__(self, content: str):
        self.content = content


class _DummyPlannerLLM:
    def __init__(self, calls: list[str]):
        self._calls = calls

    def invoke(self, _msg, config=None):
        _ = config
        self._calls.append("llm")
        return _DummyResponse('["extra query"]')


class _NoopLLM:
    def invoke(self, _msg, config=None):
        _ = config
        return _DummyResponse("")


def test_tree_explorer_searches_raw_topic_before_query_generation(monkeypatch):
    # Avoid importing the real browser visualizer (sandbox deps) during unit tests.
    dummy = types.ModuleType("agent.workflows.browser_visualizer")
    dummy.show_browser_status_page = lambda *args, **kwargs: None
    dummy.visualize_urls_from_results = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "agent.workflows.browser_visualizer", dummy)

    calls: list[str] = []
    planner = _DummyPlannerLLM(calls)
    noop = _NoopLLM()

    def _search(payload, config=None, **_kwargs):
        _ = config
        calls.append(f"search:{payload.get('query')}")
        return []

    explorer = TreeExplorer(
        planner_llm=planner,
        researcher_llm=noop,
        writer_llm=noop,
        search_func=_search,
        config={},
        max_depth=0,
        max_branches=1,
        queries_per_branch=2,
    )

    # Disable LATS backtracking so the test asserts exact call sequence
    from common.config import settings

    monkeypatch.setattr(settings, "tree_backtrack_enabled", False)

    node = ResearchTreeNode(topic="Test Topic")
    explorer.explore_branch(node, state={})

    assert calls == ["search:Test Topic", "llm", "search:extra query"]
