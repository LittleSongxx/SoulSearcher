from __future__ import annotations


def test_hybrid_retrieval_ranks_keyword_confidence_and_evidence():
    from agent.memory import InMemoryMemoryStore, MemoryRecord, MemoryService
    from agent.memory.models import MemoryScope, MemoryType

    service = MemoryService(
        InMemoryMemoryStore(),
        embedding_dim=8,
        retrieval_top_k=3,
        retrieval_max_tokens=200,
    )
    high = service.upsert_record(
        MemoryRecord(
            user_id="u1",
            scope=MemoryScope.research.value,
            type=MemoryType.research_finding.value,
            content="SoulSearcher memory retrieval combines pgvector, full-text search, recency, and evidence-backed confidence.",
            summary="SoulSearcher hybrid memory retrieval",
            confidence=0.95,
            importance=0.9,
            quality_score=0.9,
            source_urls=["https://example.com/memory"],
        )
    )
    service.upsert_record(
        MemoryRecord(
            user_id="u1",
            scope=MemoryScope.research.value,
            type=MemoryType.research_finding.value,
            content="A loosely related architecture note without evidence.",
            summary="architecture note",
            confidence=0.4,
            importance=0.2,
        )
    )

    result = service.retrieve(
        user_id="u1",
        query="SoulSearcher hybrid memory retrieval evidence",
        include_context=True,
    )

    assert result.records[0].id == high.id
    assert result.scoring[0]["record_id"] == high.id
    assert result.scoring[0]["keyword"] > 0
    assert result.scoring[0]["evidence"] == 1.0
    assert "<relevant_research_memory>" in result.context
    assert "Final report citations must come from the current run evidence ledger." in result.context


def test_memory_context_obeys_budget_and_stable_sections():
    from agent.memory.formatting import build_memory_context
    from agent.memory.models import MemoryRecord, MemoryRetrievalResult, MemoryType

    records = [
        MemoryRecord(
            user_id="u1",
            type=MemoryType.preference.value,
            content="User prefers concise architecture summaries with source links.",
            summary="prefers concise architecture summaries",
        ),
        MemoryRecord(
            user_id="u1",
            type=MemoryType.procedure.value,
            content="Preserve source-backed evidence passages before drafting the final report.",
            summary="preserve evidence passages",
        ),
        MemoryRecord(
            user_id="u1",
            type=MemoryType.research_finding.value,
            content="A" * 4000,
            summary="large research finding",
        ),
    ]
    context = build_memory_context(MemoryRetrievalResult(records=records), max_tokens=160)

    assert context.startswith("<memory_context>")
    assert "<user_profile_memory>" in context
    assert "<procedural_memory>" in context
    assert context.endswith("</memory_context>")
    assert len(context) <= 700


def test_entity_graph_is_indexed_and_recalled():
    from agent.memory import InMemoryMemoryStore, MemoryRecord, MemoryService

    service = MemoryService(InMemoryMemoryStore(), embedding_dim=8)
    service.upsert_record(
        MemoryRecord(
            user_id="u1",
            content="OpenAI Agents and LangGraph both influence SoulSearcher memory orchestration.",
            summary="OpenAI Agents LangGraph SoulSearcher",
            confidence=0.9,
        )
    )

    graph = service.graph(user_id="u1", entity="OpenAI")
    result = service.retrieve(
        user_id="u1",
        query="OpenAI LangGraph SoulSearcher",
        include_context=True,
    )

    assert any(entity["name"].startswith("OpenAI") for entity in graph["entities"])
    assert graph["relations"]
    assert result.entities
    assert "<entity_graph_memory>" in result.context
