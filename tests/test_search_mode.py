import sys
from pathlib import Path

# Ensure project root is on sys.path for direct test execution
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main import SearchMode, _normalize_search_mode


def test_normalize_search_mode_direct_string():
    mode = _normalize_search_mode("direct")
    assert mode["mode"] == "direct"
    assert mode["use_web"] is False
    assert mode["use_agent"] is False
    assert mode["use_deep"] is False


def test_normalize_search_mode_deep_string():
    mode = _normalize_search_mode("deep")
    assert mode["mode"] == "deep"
    assert mode["use_web"] is True
    assert mode["use_agent"] is True
    assert mode["use_deep"] is True


def test_normalize_search_mode_web_flag_stays_direct():
    mode = _normalize_search_mode(
        SearchMode(useWebSearch=True, useAgent=False, useDeepSearch=False)
    )
    assert mode["mode"] == "direct"
    assert mode["use_web"] is True
    assert mode["use_agent"] is False
    assert mode["use_deep"] is False


def test_normalize_search_mode_deep_enables_web_and_agent():
    mode = _normalize_search_mode(
        {"useDeepSearch": True, "useAgent": False, "useWebSearch": False}
    )
    assert mode["use_web"] is True
    assert mode["use_agent"] is True
    assert mode["use_deep"] is True
    assert mode["mode"] == "deep"


def test_normalize_search_mode_ignores_agent_without_deep():
    mode = _normalize_search_mode(
        SearchMode(useWebSearch=False, useAgent=True, useDeepSearch=False)
    )
    assert mode["mode"] == "direct"
    assert mode["use_agent"] is False
    assert mode["use_deep"] is False


def test_research_graph_only_exposes_direct_and_deep_routes():
    from agent.core.graph import create_research_graph

    graph = create_research_graph().get_graph(xray=True)
    nodes = set(graph.nodes)
    # Deep-research path is now Plan-and-Execute with three HITL checkpoints:
    #   deepsearch_planner -> hitl_plan_review -> deepsearch_executor
    #     -> hitl_sources_review -> hitl_draft_review -> human_review
    assert {
        "router",
        "direct_answer",
        "deepsearch_planner",
        "hitl_plan_review",
        "deepsearch_executor",
        "hitl_sources_review",
        "hitl_draft_review",
        "human_review",
    } <= nodes
    # Legacy / removed paths must not be re-introduced
    assert "deepsearch" not in nodes
    assert "web_plan" not in nodes
    assert "agent" not in nodes
    assert "clarify" not in nodes
    assert "coordinator" not in nodes
    assert "tree_search" not in nodes


def test_direct_answer_node_uses_single_search_when_web_enabled(monkeypatch):
    from agent.workflows import nodes

    called = {"fast": False}

    def fake_fast(state, config):
        called["fast"] = True
        return {
            "final_report": "answer from web",
            "scraped_content": [{"query": state["input"], "results": []}],
            "messages": [],
        }

    monkeypatch.setattr(nodes, "_answer_simple_agent_query", fake_fast)

    result = nodes.direct_answer_node(
        {"input": "latest AI news", "messages": []},
        {"configurable": {"search_mode": {"use_web": True, "use_deep": False}}},
    )

    assert called["fast"] is True
    assert result["final_report"] == "answer from web"
    assert result["scraped_content"][0]["query"] == "latest AI news"
