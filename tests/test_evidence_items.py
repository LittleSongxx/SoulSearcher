from agent.workflows.evidence import build_evidence_items, evidence_to_passages


def test_build_evidence_items_normalizes_and_dedupes_sources():
    items = build_evidence_items(
        search_runs=[
            {
                "query": "q1",
                "results": [
                    {
                        "title": "A",
                        "url": "https://example.com/?utm_source=x#frag",
                        "summary": "summary a",
                        "provider": "serper",
                        "score": 0.8,
                    }
                ],
            }
        ],
        sources=[{"title": "A duplicate", "url": "https://example.com/"}],
        fetched_pages=[{"url": "https://example.com/page", "title": "Page", "markdown": "body"}],
        passages=[{"url": "https://example.com/page", "page_title": "Page", "text": "passage", "snippet_hash": "abc"}],
    )

    assert items
    source_types = {item["source_type"] for item in items}
    assert "web" in source_types
    assert "fetched_page" in source_types
    assert "passage" in source_types
    assert any(item.get("provider") == "serper" for item in items)


def test_evidence_to_passages_for_claim_verifier():
    passages = evidence_to_passages(
        [
            {
                "id": "ev1",
                "source_type": "web",
                "url": "https://example.com",
                "title": "Example",
                "snippet": "The evidence text.",
            }
        ]
    )

    assert passages == [
        {
            "url": "https://example.com",
            "title": "Example",
            "text": "The evidence text.",
            "retrieved_at": "",
            "source_type": "web",
            "evidence_id": "ev1",
        }
    ]
