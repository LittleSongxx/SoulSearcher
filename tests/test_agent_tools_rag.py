from agent.workflows.agent_tools import build_agent_tools
from common.config import settings


def _names(tools):
    return sorted([getattr(t, "name", "") for t in tools if getattr(t, "name", "")])


def test_agent_tools_includes_rag_search_when_enabled_and_configured():
    original = settings.rag_enabled
    settings.rag_enabled = True
    try:
        cfg = {"configurable": {"thread_id": "rag1", "agent_profile": {"enabled_tools": {"rag": True}}}}
        names = _names(build_agent_tools(cfg))
    finally:
        settings.rag_enabled = original

    assert "rag_search" in names


def test_agent_rag_tool_uses_scoped_collection_from_config():
    original = settings.rag_enabled
    settings.rag_enabled = True
    try:
        cfg = {
            "configurable": {
                "thread_id": "rag2",
                "rag_collection_name": "weaver_documents__u_test",
                "agent_profile": {"enabled_tools": {"rag": True, "web_search": False}},
            }
        }
        tools = build_agent_tools(cfg)
    finally:
        settings.rag_enabled = original

    rag_tools = [tool for tool in tools if getattr(tool, "name", "") == "rag_search"]
    assert len(rag_tools) == 1
    original = getattr(rag_tools[0], "original", rag_tools[0])
    assert getattr(original, "collection_name", None) == "weaver_documents__u_test"

