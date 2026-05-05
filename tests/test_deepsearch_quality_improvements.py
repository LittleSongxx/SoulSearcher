from agent.workflows import deepsearch_optimized
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


def test_claim_grounding_gate_removes_unsupported_claim_sentences():
    report = "## Findings\n\nThe market grew 20% in 2025 according to the official report. A supported sentence remains [1]."
    claims = [
        {
            "claim": "The market grew 20% in 2025 according to the official report.",
            "status": "unsupported",
        }
    ]

    revised, gate = deepsearch_optimized._apply_claim_grounding_gate(report, claims, {"configurable": {}})

    assert gate["removed_claim_count"] == 1
    assert "market grew 20%" not in revised
    assert "supported sentence remains" in revised


def test_claim_grounding_gate_does_not_restore_fully_removed_report():
    report = "Unsupported revenue grew 90% in 2026."
    claims = [{"claim": "Unsupported revenue grew 90% in 2026.", "status": "unsupported"}]

    revised, gate = deepsearch_optimized._apply_claim_grounding_gate(report, claims, {"configurable": {}})

    assert gate["removed_claim_count"] == 1
    assert "Unsupported revenue grew" not in revised
    assert revised == "未保留可验证的声明。"


def test_chinese_citation_coverage_splits_adjacent_sentences():
    missing, coverage = deepsearch_optimized._estimate_citation_coverage(
        "数据显示市场增长20%[1]。监管报告显示风险上升。"
    )

    assert coverage == 0.5
    assert missing == ["监管报告显示风险上升。"]


def test_filter_and_cap_evidence_prefers_passages_and_drops_weak_text():
    evidence = [
        {
            "id": "weak",
            "source_type": "web",
            "url": "https://example.com/cookie",
            "snippet": "Cookie preferences accept consent manage cookies.",
        },
        {
            "id": "web",
            "source_type": "web",
            "url": "https://example.com/web",
            "snippet": "A usable web snippet with enough concrete evidence text for a claim.",
        },
        {
            "id": "passage",
            "source_type": "passage",
            "url": "https://example.com/passage",
            "snippet": "A stronger passage with direct claim-level evidence text for citation repair.",
            "content_ref": "hash",
        },
    ]

    filtered = deepsearch_optimized._filter_and_cap_evidence_items(
        evidence,
        {"configurable": {"deepsearch_evidence_item_cap": 1}},
    )

    assert [item["id"] for item in filtered] == ["passage"]


def test_filter_and_cap_evidence_uses_short_items_only_as_fallback():
    evidence = [
        {
            "id": "short",
            "source_type": "web",
            "url": "https://example.com/short",
            "snippet": "Brief.",
        },
        {
            "id": "strong",
            "source_type": "web",
            "url": "https://example.com/strong",
            "snippet": "A strong and sufficiently detailed evidence snippet for a factual claim.",
        },
    ]

    filtered = deepsearch_optimized._filter_and_cap_evidence_items(
        evidence,
        {"configurable": {"deepsearch_evidence_item_cap": 1}},
    )

    assert [item["id"] for item in filtered] == ["strong"]


def test_cap_passages_filters_low_value_text_even_below_cap():
    passages = [
        {
            "url": "https://example.com/cookie",
            "text": "Cookie preferences accept consent manage cookies.",
        },
        {
            "url": "https://example.com/claim",
            "text": "The official report states the benchmark improved 20% in 2025.",
        },
    ]

    filtered = deepsearch_optimized._cap_passages(
        passages,
        {"configurable": {"deepsearch_passage_cap": 10}},
    )

    assert len(filtered) == 1
    assert filtered[0]["url"] == "https://example.com/claim"


def test_grounding_evidence_block_uses_source_indices_and_evidence_ids():
    block = deepsearch_optimized._format_grounding_evidence_block(
        evidence_items=[
            {
                "id": "ev1",
                "source_type": "passage",
                "url": "https://example.com/report",
                "title": "Official report",
                "snippet": "The official report states the benchmark improved 20% in 2025.",
            }
        ],
        sources=[{"url": "https://example.com/report", "title": "Official report"}],
        config={"configurable": {}},
    )

    assert "[1] Official report" in block
    assert "evidence_id=ev1" in block
