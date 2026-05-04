from agent.workflows.evidence_providers import (
    MCPEvidenceProvider,
    RAGEvidenceProvider,
    build_evidence_providers,
    build_provider_capability_artifact,
    merge_provider_evidence,
    merge_provider_results,
    search_with_evidence_providers,
    select_provider_names,
)
from agent.workflows.research_brief import build_research_brief


def test_select_provider_names_from_source_policy():
    hybrid = build_research_brief({"input": "q", "source_policy": "hybrid"}, {})
    private_first = build_research_brief({"input": "q", "source_policy": "private-first"}, {})
    local = build_research_brief({"input": "q", "source_policy": "local"}, {})

    assert select_provider_names(hybrid, {}) == ["web", "rag"]
    assert select_provider_names(private_first, {}) == ["rag", "web"]
    assert select_provider_names(local, {}) == ["rag"]


def test_web_provider_wraps_search_and_evidence():
    brief = build_research_brief({"input": "q"}, {})

    def fake_search(query, max_results, config, provider_profile=None):
        return [{"title": "A", "url": "https://example.com", "summary": "text", "score": 0.9}]

    providers = build_evidence_providers(
        brief=brief,
        config={},
        search_func=fake_search,
        provider_profile=None,
    )
    outputs = search_with_evidence_providers(
        providers=providers,
        query="q",
        max_results=3,
        config={},
    )

    assert outputs[0].provider == "web"
    assert merge_provider_results(outputs)[0]["provider"] == "web"
    assert merge_provider_evidence(outputs)[0]["source_type"] == "web"


def test_rag_provider_normalizes_results(monkeypatch):
    class FakeRAG:
        def search(self, query, n_results=5):
            return [
                {
                    "content": "local document evidence",
                    "score": 0.7,
                    "source": "docs/a.md",
                    "filename": "a.md",
                    "chunk_index": 2,
                }
            ]

    monkeypatch.setattr("tools.rag.rag_tool.get_rag_tool", lambda collection_name=None: FakeRAG())

    output = RAGEvidenceProvider(collection_name="test_collection").search("q", 2, {})

    assert output.provider == "rag"
    assert output.results[0]["query"] == "q"
    assert output.evidence_items[0]["source_type"] == "rag"
    assert output.evidence_items[0]["title"] == "a.md"


def test_build_provider_capability_artifact_describes_selected_providers():
    brief = build_research_brief({"input": "q", "source_policy": "hybrid"}, {})

    def fake_search(query, max_results, config, provider_profile=None):
        return []

    providers = build_evidence_providers(
        brief=brief,
        config={},
        search_func=fake_search,
        provider_profile=None,
    )
    artifact = build_provider_capability_artifact(
        providers,
        {"configurable": {"deepsearch_native_web_search": True}},
    )
    capabilities = {provider["name"]: provider for provider in artifact["providers"]}

    assert artifact["provider_count"] == 2
    assert capabilities["web"]["web_search"] is True
    assert capabilities["web"]["native_web_search"] is True
    assert capabilities["rag"]["local_docs"] is True
    assert capabilities["rag"]["raw_content"] is True


def test_mcp_provider_uses_configured_results_and_tool_whitelist():
    provider = MCPEvidenceProvider()
    output = provider.search(
        "q",
        5,
        {
            "configurable": {
                "mcp_tools_to_include": ["github_search"],
                "mcp_evidence_results": [
                    {
                        "tool": "github_search",
                        "title": "Issue evidence",
                        "content": "MCP issue content",
                        "document_id": "issue-1",
                    },
                    {
                        "tool": "private_db",
                        "title": "Filtered evidence",
                        "content": "Should not appear",
                    },
                ],
            }
        },
    )

    assert output.error == ""
    assert len(output.results) == 1
    assert output.results[0]["tool"] == "github_search"
    assert output.evidence_items[0]["source_type"] == "mcp"
    assert output.evidence_items[0]["document_id"] == "issue-1"


def test_mcp_provider_reports_auth_required_and_capability_whitelist():
    provider = MCPEvidenceProvider()
    output = provider.search(
        "q",
        5,
        {"configurable": {"mcp_auth_required": True, "mcp_tools_to_include": "github_search,docs"}},
    )
    artifact = build_provider_capability_artifact(
        [provider],
        {"configurable": {"mcp_auth_required": True, "mcp_tools_to_include": "github_search,docs"}},
    )

    assert output.error == "mcp_auth_required"
    assert artifact["providers"][0]["requires_auth"] is True
    assert artifact["providers"][0]["tool_whitelist"] == ["github_search", "docs"]
