from types import SimpleNamespace

from agent.workflows.context_budget import build_context_budget_manager
from agent.workflows.research_budget import (
    ResearchBudgetRuntime,
    build_deepsearch_budget,
)
from agent.workflows.research_loop_guard import ResearchLoopGuard, build_loop_guard_policy
from tools.core.mcp_policy import build_mcp_tool_policy, filter_mcp_tools


def test_research_budget_runtime_caps_units_queries_and_tools():
    budget = build_deepsearch_budget(
        config={
            "configurable": {
                "deepsearch_max_research_units": 1,
                "deepsearch_max_search_queries": 1,
                "deepsearch_max_tool_calls_per_unit": 1,
            }
        }
    )
    runtime = ResearchBudgetRuntime(budget)
    tasks = [SimpleNamespace(worker_id="w1"), SimpleNamespace(worker_id="w2")]

    accepted, rejected = runtime.reserve_research_units(tasks)

    assert [task.worker_id for task in accepted] == ["w1"]
    assert [task.worker_id for task in rejected] == ["w2"]
    assert runtime.reserve_search_query("q1") is True
    assert runtime.reserve_search_query("q2") is False
    assert runtime.reserve_tool_call("w1", "search") is True
    assert runtime.reserve_tool_call("w1", "search") is False
    assert runtime.to_artifact()["usage"]["research_units_started"] == 1


def test_loop_guard_blocks_repeated_queries_tools_and_empty_streaks():
    guard = ResearchLoopGuard(
        build_loop_guard_policy(
            {
                "configurable": {
                    "deepsearch_loop_max_repeated_query": 1,
                    "deepsearch_loop_max_repeated_tool_call": 1,
                    "deepsearch_loop_max_empty_result_streak": 2,
                }
            }
        )
    )

    assert guard.before_search_query("Same Query").allowed is True
    assert guard.before_search_query(" same   query ").allowed is False
    assert guard.before_tool_call("search", {"q": "a"}).allowed is True
    assert guard.before_tool_call("search", {"q": "a"}).allowed is False
    assert guard.record_search_results("x", []).allowed is True
    assert guard.record_search_results("y", []).allowed is False
    assert guard.to_artifact()["denied_count"] >= 3


def test_context_budget_manager_truncates_text_while_preserving_metadata():
    manager = build_context_budget_manager(
        models={"research": "unknown-model", "writing": "unknown-model"},
        max_context_tokens=10,
    )
    results = manager.cap_results(
        [{"title": "A", "url": "https://example.com", "content": "x" * 200}],
        stage="research",
    )
    notes = manager.cap_text_list(["a" * 30, "b" * 30], stage="writing")

    assert results[0]["url"] == "https://example.com"
    assert len(results[0]["content"]) <= 40
    assert len("".join(notes)) <= 40
    assert manager.to_artifact()["usage"]["research"]["truncated_items"] >= 1


def test_mcp_policy_filters_by_strategy_whitelist_and_cap():
    tools = [
        SimpleNamespace(name="alpha"),
        SimpleNamespace(name="beta"),
        SimpleNamespace(name="gamma"),
    ]
    policy = build_mcp_tool_policy(
        {"configurable": {"mcp_strategy": "fast", "mcp_tool_whitelist": "alpha,beta", "mcp_max_tools": 1}}
    )
    disabled = build_mcp_tool_policy({"configurable": {"mcp_strategy": "disabled"}})

    assert policy.strategy == "fast_once"
    assert [tool.name for tool in filter_mcp_tools(tools, policy)] == ["alpha"]
    assert filter_mcp_tools(tools, disabled) == []
