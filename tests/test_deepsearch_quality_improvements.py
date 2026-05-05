from agent.workflows import deepsearch_optimized
from agent.workflows.brief_coverage import build_brief_coverage_artifact
from agent.workflows.claim_ledger import build_claim_ledger, format_claim_ledger_for_writer
from agent.workflows.fact_cards import (
    build_fact_cards,
    format_fact_cards_for_writer,
    repair_citations_with_fact_cards,
)
from agent.workflows.research_brief import build_research_brief
from agent.workflows.research_reflection import gap_queries_from_quality_gates
from agent.workflows.source_curator import curate_sources
from agent.workflows.stage_metrics import StageMetricsRecorder, warn_slow_stages


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


def test_source_curator_rewards_quantitative_sources():
    brief = build_research_brief({"input": "market benchmark report"}, {})
    sources = [
        {"title": "Opinion", "url": "https://example.com/opinion", "summary": "market discussion"},
        {"title": "Benchmark report 2025", "url": "https://example.com/report", "summary": "benchmark improved 20% in 2025"},
    ]

    curated = curate_sources(sources, brief=brief)

    assert curated[0]["title"] == "Benchmark report 2025"
    assert "quantitative_signal" in curated[0]["reliability_reasons"]


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


def test_evidence_reliability_is_enriched_from_curated_sources():
    enriched = deepsearch_optimized._enrich_evidence_reliability_from_sources(
        [{"id": "ev1", "url": "https://example.gov/report", "snippet": "The official report states growth was 20%."}],
        [{"url": "https://example.gov/report", "reliability_score": 0.9, "reliability_reasons": ["authoritative_domain"]}],
    )

    assert enriched[0]["reliability_score"] == 0.9
    assert enriched[0]["reliability_reasons"] == ["authoritative_domain"]


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


def test_fact_cards_bind_evidence_to_numbered_sources():
    cards = build_fact_cards(
        evidence_items=[
            {
                "id": "ev1",
                "source_type": "passage",
                "url": "https://example.gov/report",
                "title": "Official report",
                "snippet": "The official report states the benchmark improved 20% in 2025.",
            }
        ],
        sources=[{"url": "https://example.gov/report", "title": "Official report", "reliability_score": 0.9}],
    )

    assert cards
    assert cards[0]["source_index"] == 1
    assert cards[0]["evidence_id"] == "ev1"
    assert cards[0]["confidence"] > 0.6
    assert "Fact Cards" in format_fact_cards_for_writer(cards)


def test_fact_card_repair_adds_matching_source_index():
    cards = build_fact_cards(
        evidence_items=[
            {
                "id": "ev1",
                "source_type": "passage",
                "url": "https://example.gov/report",
                "snippet": "The official report states the benchmark improved 20% in 2025.",
            }
        ],
        sources=[{"url": "https://example.gov/report", "title": "Official report"}],
    )
    report = "## Findings\n\nThe official report states the benchmark improved 20% in 2025."

    repaired, payload = repair_citations_with_fact_cards(
        report,
        ["The official report states the benchmark improved 20% in 2025."],
        cards,
    )

    assert payload["repaired_count"] == 1
    assert "2025 [1]." in repaired


def test_brief_coverage_reports_missing_expected_fields():
    artifact = build_brief_coverage_artifact(
        research_brief={"expected_fields": ["architecture", "latency"]},
        fact_cards=[{"claim": "The architecture uses a supervisor worker pattern.", "quote": "architecture supervisor worker"}],
    )

    assert artifact["score"] == 0.5
    assert artifact["missing_fields"] == ["latency"]


def test_sources_for_writer_includes_fact_cards():
    block = deepsearch_optimized._format_sources_for_writer(
        [{"url": "https://example.gov/report", "title": "Official report"}],
        [],
        limit=1,
        fact_cards=[
            {
                "source_index": 1,
                "claim": "The official report states the benchmark improved 20% in 2025.",
                "evidence_id": "ev1",
            }
        ],
    )

    assert "事实卡1" in block
    assert "evidence_id=ev1" in block


def test_stage_metrics_recorder_artifact_and_warnings():
    recorder = StageMetricsRecorder()
    stage = recorder.start("writer")
    recorder.finish(stage, report_chars=120)
    artifact = recorder.artifact()

    assert artifact["stage_count"] == 1
    assert artifact["stages"][0]["metadata"]["report_chars"] == 120
    assert warn_slow_stages({"stages": [{"name": "writer", "duration_s": 2.0}]}, warn_after_s=1.0)
