from types import SimpleNamespace

from agent.workflows.research_brief import build_research_brief
from agent.workflows.strategy_selector import select_deepsearch_strategy


def test_strategy_selector_maps_linear_runtime_mode_to_supervisor_workers():
    brief = build_research_brief({"input": "broad market analysis"}, {})
    decision = select_deepsearch_strategy(
        brief=brief,
        config={"configurable": {"deepsearch_mode": "linear"}},
        settings=SimpleNamespace(deepsearch_mode="tree", tree_exploration_enabled=True),
    )

    assert decision.strategy == "supervisor_workers"
    assert decision.reason == "runtime mode override"


def test_strategy_selector_preserves_tree_runtime_mode_override():
    brief = build_research_brief({"input": "broad market analysis"}, {})
    decision = select_deepsearch_strategy(
        brief=brief,
        config={"configurable": {"deepsearch_mode": "tree"}},
        settings=SimpleNamespace(deepsearch_mode="supervisor_workers"),
    )

    assert decision.strategy == "tree"
    assert decision.reason == "runtime mode override"


def test_strategy_selector_accepts_supervisor_workers_override():
    brief = build_research_brief({"input": "broad market analysis"}, {})
    decision = select_deepsearch_strategy(
        brief=brief,
        config={"configurable": {"deepsearch_strategy": "supervisor"}},
        settings=SimpleNamespace(deepsearch_mode="auto", tree_exploration_enabled=True),
    )

    assert decision.strategy == "supervisor_workers"
    assert decision.reason == "runtime strategy override"


def test_strategy_selector_keeps_supervisor_for_simple_query():
    brief = build_research_brief({"input": "What is the capital of France?"}, {})
    decision = select_deepsearch_strategy(
        brief=brief,
        config={"configurable": {}},
        settings=SimpleNamespace(deepsearch_mode="auto", tree_exploration_enabled=True),
        simple_query_detector=lambda text: True,
    )

    assert decision.strategy == "supervisor_workers"


def test_strategy_selector_maps_reflection_request_to_supervisor_workers():
    brief = build_research_brief({"input": "Summarize local notes"}, {})
    decision = select_deepsearch_strategy(
        brief=brief,
        config={
            "configurable": {
                "deepsearch_max_epochs": 2,
                "deepsearch_query_num": 2,
                "use_reflection_loop": True,
            }
        },
        settings=SimpleNamespace(deepsearch_mode="auto", tree_exploration_enabled=True),
    )

    assert decision.strategy == "supervisor_workers"


def test_strategy_selector_uses_supervisor_workers_for_private_policy_by_default():
    brief = build_research_brief({"input": "q", "source_policy": "private-first"}, {})
    decision = select_deepsearch_strategy(
        brief=brief,
        config={"configurable": {}},
        settings=SimpleNamespace(
            deepsearch_mode="auto", tree_exploration_enabled=False
        ),
    )

    assert decision.strategy == "supervisor_workers"


def test_strategy_selector_defaults_to_supervisor_workers_for_broad_queries():
    brief = build_research_brief({"input": "broad market analysis"}, {})
    decision = select_deepsearch_strategy(
        brief=brief,
        config={"configurable": {}},
        settings=SimpleNamespace(deepsearch_mode="auto", tree_exploration_enabled=True),
    )

    assert decision.strategy == "supervisor_workers"
