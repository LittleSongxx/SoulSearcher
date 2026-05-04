from agent.workflows.citation_artifacts import build_citation_annotations, build_timeline_artifacts


def test_build_citation_annotations_maps_markers_to_sources_and_evidence():
    report = "Revenue increased by 20% [1].\n\n## 参考来源（自动生成）\n\n- [1] Annual Report — https://example.com/annual"
    sources = [
        {
            "title": "Annual Report",
            "url": "https://example.com/annual?utm_source=test",
            "rawUrl": "https://example.com/annual?utm_source=test",
            "domain": "example.com",
            "provider": "web",
        }
    ]
    evidence_items = [
        {
            "id": "ev1",
            "source_type": "web",
            "url": "https://example.com/annual",
            "citation_id": "S1",
        }
    ]

    annotations = build_citation_annotations(
        report=report,
        sources=sources,
        evidence_items=evidence_items,
    )

    assert len(annotations) == 2
    body = annotations[0]
    references = annotations[1]
    assert body["section"] == "body"
    assert references["section"] == "references"
    assert body["marker"] == "[1]"
    assert body["source_index"] == 1
    assert body["url"] == "https://example.com/annual"
    assert "ev1" in body["evidence_ids"]


def test_build_timeline_artifacts_orders_search_source_evidence_and_quality_events():
    timeline = build_timeline_artifacts(
        search_runs=[
            {
                "query": "q",
                "timestamp": "2026-02-17T00:00:00+00:00",
                "results": [{"provider": "web", "url": "https://example.com"}],
            }
        ],
        sources=[{"title": "Example", "url": "https://example.com", "provider": "web"}],
        evidence_items=[
            {
                "id": "ev1",
                "source_type": "web",
                "url": "https://example.com",
                "retrieved_at": "2026-02-17T00:00:01+00:00",
            }
        ],
        quality_gates=[{"epoch": 1, "stage": "final", "gates": [{"name": "coverage", "passed": True}]}],
    )

    assert [event["order"] for event in timeline] == list(range(1, len(timeline) + 1))
    event_types = {event["event_type"] for event in timeline}
    assert {"search", "source", "evidence", "quality_gate"}.issubset(event_types)
    assert timeline[0]["event_type"] == "search"
