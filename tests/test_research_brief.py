from agent.workflows.research_brief import brief_topic, build_research_brief


def test_build_research_brief_derives_expected_fields_and_freshness():
    brief = build_research_brief(
        {
            "input": "latest comparison of agentic RAG frameworks",
            "constraints": {"freshness_days": 30},
        },
        {},
    )

    assert brief.original_query == "latest comparison of agentic RAG frameworks"
    assert brief.clarified_goal == brief.original_query
    assert "comparison_dimensions" in brief.expected_fields
    assert "recent_updates" in brief.expected_fields
    assert brief.freshness_requirement == "within_30_days"
    assert brief.complexity == "broad"
    assert brief.citation_policy == "required"
    assert brief.source_routing["mode"] == "web_only"
    assert "Expected fields:" in brief_topic(brief)


def test_build_research_brief_accepts_existing_dict():
    brief = build_research_brief(
        {
            "input": "ignored",
            "research_brief": {
                "original_query": "q",
                "clarified_goal": "goal",
                "expected_fields": ["a", "a", "b"],
                "source_policy": "hybrid",
            },
        },
        {},
    )

    assert brief.original_query == "q"
    assert brief.clarified_goal == "goal"
    assert brief.expected_fields == ["a", "b"]
    assert brief.source_policy == "hybrid"
    assert brief.source_routing["providers"] == ["web", "rag"]
