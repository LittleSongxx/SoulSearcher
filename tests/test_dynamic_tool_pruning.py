"""Tests for dynamic tool pruning by route."""

from unittest.mock import MagicMock

from agent.workflows.agent_tools import _prune_tools_by_route, _resolve_pruning_route


def _make_tool(name: str) -> MagicMock:
    t = MagicMock()
    t.name = name
    return t


class TestPruneToolsByRoute:
    """Test the route-based tool pruning."""

    def test_no_route_no_pruning(self):
        tools = [_make_tool("tavily_search"), _make_tool("execute_python_code")]
        result = _prune_tools_by_route(tools, "")
        assert len(result) == 2

    def test_unknown_route_no_pruning(self):
        tools = [_make_tool("tavily_search"), _make_tool("custom_tool")]
        result = _prune_tools_by_route(tools, "unknown")
        assert len(result) == 2

    def test_web_route_keeps_search_crawl(self):
        tools = [
            _make_tool("tavily_search"),
            _make_tool("crawl_url"),
            _make_tool("execute_python_code"),
            _make_tool("ask_human"),
            _make_tool("plan_steps"),
        ]
        result = _prune_tools_by_route(tools, "web")
        names = {t.name for t in result}
        assert "tavily_search" in names
        assert "crawl_url" in names
        assert "plan_steps" in names
        assert "execute_python_code" not in names
        assert "ask_human" not in names

    def test_deep_route_keeps_search_crawl_code(self):
        tools = [
            _make_tool("tavily_search"),
            _make_tool("crawl_url"),
            _make_tool("execute_python_code"),
            _make_tool("ask_human"),
            _make_tool("plan_steps"),
        ]
        result = _prune_tools_by_route(tools, "deep")
        names = {t.name for t in result}
        assert "tavily_search" in names
        assert "execute_python_code" in names
        assert "ask_human" not in names

    def test_agent_route_keeps_core_tools(self):
        tools = [
            _make_tool("tavily_search"),
            _make_tool("crawl_url"),
            _make_tool("execute_python_code"),
            _make_tool("ask_human"),
            _make_tool("str_replace"),
            _make_tool("plan_steps"),
            _make_tool("sandbox_browser"),  # not in core
        ]
        result = _prune_tools_by_route(tools, "agent")
        names = {t.name for t in result}
        assert "tavily_search" in names
        assert "ask_human" in names
        assert "sandbox_browser" not in names

    def test_empty_tools_fallback(self):
        """If no tools match the route, return truncated original list."""
        tools = [_make_tool("obscure_tool")]
        result = _prune_tools_by_route(tools, "web")
        assert len(result) == 1  # fallback: returns original[:10]

    def test_max_pruned_tools_cap(self):
        """Even with matching tools, cap at 10."""
        tools = [_make_tool("tavily_search") for _ in range(20)]
        result = _prune_tools_by_route(tools, "web")
        assert len(result) <= 10


class TestResolvePruningRoute:
    def test_prefers_resolved_route(self):
        route = _resolve_pruning_route({}, {"resolved_route": "deep"})

        assert route == "deep"

    def test_falls_back_to_search_mode_route(self):
        route = _resolve_pruning_route({}, {"search_mode": {"route": "web"}})

        assert route == "web"

    def test_falls_back_to_search_mode_mode(self):
        route = _resolve_pruning_route({}, {"search_mode": {"mode": "agent"}})

        assert route == "agent"
