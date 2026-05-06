import asyncio

from agent.workflows.research_tree import TreeExplorationBudgetExceeded, TreeExplorer
from common.config import settings


class _NoopLLM:
    def invoke(self, _msg, config=None):
        _ = config
        return type("Resp", (), {"content": ""})()


def test_tree_explorer_run_async_does_not_deadlock(monkeypatch):
    """
    Regression test: TreeExplorer.run_async() previously deadlocked when
    `tree_parallel_branches` was fully utilized at depth=1 and the code tried to
    re-acquire the same semaphore while exploring grandchildren.
    """

    # Make the deadlock condition deterministic:
    # - two first-level children
    # - parallel limit == 2 (fully utilized)
    monkeypatch.setattr(settings, "tree_parallel_branches", 2, raising=False)

    explorer = TreeExplorer(
        planner_llm=_NoopLLM(),
        researcher_llm=_NoopLLM(),
        writer_llm=_NoopLLM(),
        search_func=lambda *_args, **_kwargs: [],
        config={},
        max_depth=2,
        max_branches=2,
        queries_per_branch=1,
    )

    # Avoid real LLM/search work; keep only the concurrency structure.
    def _fake_decompose(
        topic: str, existing_knowledge: str = "", num_subtopics: int = 4
    ):
        _ = existing_knowledge
        _ = num_subtopics
        if topic == "Root Topic":
            return [("Child A", 1.0), ("Child B", 1.0)]
        return [("Grandchild", 1.0)]

    explorer.decompose_topic = _fake_decompose  # type: ignore[assignment]

    def _fake_explore_branch(node, state, per_query_results: int = 5):
        _ = state
        _ = per_query_results
        node.mark_complete(summary=f"done:{node.topic}")

    async def _fake_explore_branch_async(node, state, per_query_results: int = 5):
        _ = state
        _ = per_query_results
        node.mark_complete(summary=f"done:{node.topic}")

    explorer.explore_branch = _fake_explore_branch  # type: ignore[assignment]
    explorer.explore_branch_async = _fake_explore_branch_async  # type: ignore[assignment]

    async def _run():
        return await asyncio.wait_for(
            explorer.run_async("Root Topic", state={}, decompose_root=True),
            timeout=1.0,
        )

    tree = asyncio.run(_run())
    assert tree.root_id is not None
    assert len(tree.nodes) >= 1


def test_tree_explorer_run_async_propagates_budget_stop_without_hanging(monkeypatch):
    monkeypatch.setattr(settings, "tree_parallel_branches", 2, raising=False)

    explorer = TreeExplorer(
        planner_llm=_NoopLLM(),
        researcher_llm=_NoopLLM(),
        writer_llm=_NoopLLM(),
        search_func=lambda *_args, **_kwargs: [],
        config={},
        max_depth=1,
        max_branches=2,
        queries_per_branch=1,
    )

    explorer.decompose_topic = lambda *args, **kwargs: [("Child A", 1.0), ("Child B", 1.0)]  # type: ignore[assignment]

    def _fake_explore_branch(node, state, per_query_results: int = 5):
        _ = state, per_query_results
        node.mark_complete(summary=f"done:{node.topic}")

    async def _fake_explore_branch_async(node, state, per_query_results: int = 5):
        _ = state, per_query_results
        if node.topic == "Child B":
            raise TreeExplorationBudgetExceeded("search_budget_exceeded")
        node.mark_complete(summary=f"done:{node.topic}")

    explorer.explore_branch = _fake_explore_branch  # type: ignore[assignment]
    explorer.explore_branch_async = _fake_explore_branch_async  # type: ignore[assignment]

    async def _run():
        return await asyncio.wait_for(
            explorer.run_async("Root Topic", state={}, decompose_root=True),
            timeout=1.0,
        )

    try:
        asyncio.run(_run())
    except TreeExplorationBudgetExceeded as exc:
        assert exc.reason == "search_budget_exceeded"
    else:
        raise AssertionError("Expected TreeExplorationBudgetExceeded")
