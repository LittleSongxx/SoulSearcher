from agent.workflows.claim_ledger import build_claim_ledger, format_claim_ledger_for_writer
from agent.workflows.research_brief import build_research_brief
from agent.workflows.research_reflection import gap_queries_from_quality_gates
from agent.workflows.source_curator import curate_sources


def test_source_curator_prioritizes_authoritative_relevant_sources():
    brief = build_research_brief({"input": "AI policy regulation report"}, {})
    sources = [
        {"title": "Forum post", "url": "https://reddit.com/r/ai/comments/1", "summary": "AI policy"},
        {"title": "Official AI regulation guidance", "url": "https://example.gov/ai", "summary": "AI policy regulation guidance"},
    ]

    curated = curate_sources(sources, brief=brief)

    assert curated[0]["url"] == "https://example.gov/ai"
    assert curated[0]["reliability_score"] > curated[1]["reliability_score"]
    assert "authoritative_domain" in curated[0]["reliability_reasons"]


def test_claim_ledger_links_verified_claims_to_source_indices_and_evidence_ids():
    summary_notes = ["The market grew 20% in 2025 according to the official report."]
    search_runs = [
        {
            "query": "market official report",
            "results": [
                {
                    "title": "Official report",
                    "url": "https://example.gov/report",
                    "summary": "The market grew 20% in 2025 according to the official report.",
                }
            ],
        }
    ]
    sources = [{"title": "Official report", "url": "https://example.gov/report"}]
    evidence_items = [
        {
            "id": "ev1",
            "source_type": "web",
            "url": "https://example.gov/report",
            "title": "Official report",
            "snippet": "The market grew 20% in 2025 according to the official report.",
        }
    ]

    ledger = build_claim_ledger(
        summary_notes=summary_notes,
        search_runs=search_runs,
        sources=sources,
        evidence_items=evidence_items,
        max_claims=4,
    )

    assert ledger
    assert ledger[0]["status"] == "verified"
    assert ledger[0]["source_indices"] == [1]
    assert "ev1" in ledger[0]["evidence_ids"]
    assert "声明-证据账本" in format_claim_ledger_for_writer(ledger)


def test_gap_queries_from_quality_gates_include_failed_claims_and_missing_dimensions():
    brief = build_research_brief({"input": "battery market analysis"}, {})
    queries = gap_queries_from_quality_gates(
        brief=brief,
        quality_gates=[
            {
                "name": "query_coverage",
                "status": "fail",
                "details": {"missing_dimensions": ["freshness"]},
            },
            {"name": "claim_verifier", "status": "fail", "details": {}},
        ],
        claims=[{"claim": "Unsupported claim", "status": "unsupported"}],
        max_queries=3,
    )

    assert len(queries) == 3
    assert any("freshness" in query for query in queries)
    assert any("Unsupported claim" in query for query in queries)
