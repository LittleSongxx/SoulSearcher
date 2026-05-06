from agent.workflows.research_brief import build_research_brief
from agent.workflows.source_routing import (
    build_source_routing_policy,
    source_policy_from_routing,
)


def test_source_routing_defaults_to_web_only():
    brief = build_research_brief({"input": "research q"}, {})

    assert brief.source_routing["mode"] == "web_only"
    assert brief.source_routing["providers"] == ["web"]
    assert brief.source_policy == "web"


def test_source_routing_builds_hybrid_collection_and_access_policy():
    brief = build_research_brief(
        {"input": "research private docs", "user_id": "u1"},
        {
            "configurable": {
                "source_policy": "hybrid",
                "rag_collection_name": "team_docs",
            }
        },
    )

    routing = brief.source_routing

    assert routing["mode"] == "hybrid"
    assert routing["providers"] == ["web", "rag"]
    assert routing["collections"][0]["id"] == "team_docs"
    assert routing["access_policy"]["owner_id"] == "u1"
    assert source_policy_from_routing(routing) == "hybrid"


def test_source_routing_supports_mcp_governed_presets():
    routing = build_source_routing_policy(
        config={
            "configurable": {
                "source_policy": "mcp",
                "mcp_auth_required": True,
                "mcp_tools_to_include": "github_search,docs",
                "mcp_preset_ids": ["github_readonly"],
            }
        }
    )

    assert routing["mode"] == "mcp_only"
    assert routing["providers"] == ["mcp"]
    assert routing["mcp_governance"]["auth_required"] is True
    assert routing["mcp_governance"]["tool_whitelist"] == ["github_search", "docs"]
    assert routing["mcp_governance"]["preset_ids"] == ["github_readonly"]


def test_source_routing_supports_lightweight_connectors_and_index_attempts():
    routing = build_source_routing_policy(
        config={
            "configurable": {
                "source_policy": "hybrid",
                "source_connectors": [
                    {
                        "id": "drive",
                        "connector_type": "google_drive",
                        "auth_mode": "oauth",
                    }
                ],
                "source_index_attempts": [
                    {
                        "id": "idx1",
                        "collection_id": "team_docs",
                        "connector_id": "drive",
                        "status": "completed",
                        "document_count": 12,
                    }
                ],
            }
        }
    )

    assert routing["connectors"][0]["id"] == "drive"
    assert routing["connectors"][0]["auth_mode"] == "oauth"
    assert routing["index_attempts"][0]["status"] == "completed"
    assert routing["index_attempts"][0]["document_count"] == 12
