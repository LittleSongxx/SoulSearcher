from __future__ import annotations

import asyncio


def _service(*, auto_skill_evolution: bool = False, min_support: int = 3):
    from agent.memory import InMemoryMemoryStore, MemoryService

    store = InMemoryMemoryStore()
    service = MemoryService(
        store,
        embedding_dim=8,
        retrieval_top_k=5,
        write_min_confidence=0.75,
        write_require_evidence=True,
        auto_skill_evolution=auto_skill_evolution,
        auto_skill_min_support=min_support,
    )
    return service, store


def test_memory_record_upsert_dedupes_and_soft_deletes():
    from agent.memory.models import MemoryRecord, MemoryStatus

    service, store = _service()
    first = service.upsert_record(
        MemoryRecord(user_id="u1", content="SoulSearcher uses LangGraph for orchestration.")
    )
    second = service.upsert_record(
        MemoryRecord(user_id="u1", content="SoulSearcher uses LangGraph for orchestration.")
    )

    assert first.id != second.id
    assert store.records[first.id].status == MemoryStatus.superseded.value
    assert second.metadata["supersedes"] == first.id

    assert service.delete_record(second.id, user_id="u1") is True
    assert service.list_records(user_id="u1") == []
    assert store.records[second.id].status == MemoryStatus.deleted.value


def test_ingest_research_run_gates_evidence_quality_and_sensitive_content():
    service, store = _service()
    good_artifacts = {
        "quality_summary": {"publish_ready": True, "overall_score": 0.9},
        "evidence_items": [
            {
                "id": "ev1",
                "url": "https://example.com/soulsearcher",
                "content": "SoulSearcher evidence passage with enough content for memory.",
            }
        ],
        "claims": [{"claim": "SoulSearcher keeps citations evidence-backed.", "status": "supported"}],
    }

    result = asyncio.run(
        service.ingest_research_run(
            user_id="u1",
            thread_id="thread_1",
            run_id="run_1",
            research_brief="Analyze SoulSearcher memory architecture",
            final_report="Final report with source-backed synthesis.",
            artifacts=good_artifacts,
            notes=[],
        )
    )

    assert result["saved"]
    assert not result["rejected"]
    assert any(item.type == "research_finding" for item in store.records.values())
    assert any(item.source_evidence_ids == ["ev1"] for item in store.records.values())

    missing = asyncio.run(
        service.ingest_research_run(
            user_id="u1",
            thread_id="thread_2",
            run_id="run_2",
            research_brief="No evidence",
            final_report="Report",
            artifacts={"quality_summary": {"publish_ready": True}},
            notes=["This note is intentionally long enough to become a candidate memory."],
        )
    )
    assert missing["saved"] == []
    assert missing["rejected"][0]["reason"] == "missing_evidence"

    failed = asyncio.run(
        service.ingest_research_run(
            user_id="u1",
            thread_id="thread_3",
            run_id="run_3",
            research_brief="Failed quality",
            final_report="Report",
            artifacts={
                "quality_summary": {"publish_ready": False},
                "evidence_items": [{"id": "ev2", "url": "https://example.com/fail"}],
            },
            notes=[],
        )
    )
    assert failed["saved"] == []
    assert failed["rejected"][0]["reason"] == "quality_gate_failed"

    sensitive = asyncio.run(
        service.ingest_research_run(
            user_id="u1",
            thread_id="thread_4",
            run_id="run_4",
            research_brief="Sensitive",
            final_report="Report",
            artifacts={
                "quality_summary": {"publish_ready": True},
                "evidence_items": [{"id": "ev3", "url": "https://example.com/secret"}],
                "claims": [{"claim": "The api key is sk-1234567890abcdef123456.", "status": "supported"}],
            },
            notes=[],
        )
    )
    assert sensitive["saved"]
    assert any(item["reason"] == "sensitive_content" for item in sensitive["rejected"])


def test_ingest_research_run_can_redact_sensitive_content():
    from agent.memory import InMemoryMemoryStore, MemoryService

    service = MemoryService(
        InMemoryMemoryStore(),
        embedding_dim=8,
        write_require_evidence=True,
        sensitive_write_policy="redact",
    )
    result = asyncio.run(
        service.ingest_research_run(
            user_id="u1",
            thread_id="thread_sensitive",
            run_id="run_sensitive",
            research_brief="Sensitive redaction",
            final_report="Report",
            artifacts={
                "quality_summary": {"publish_ready": True, "overall_score": 0.9},
                "evidence_items": [{"id": "ev1", "url": "https://example.com/secret"}],
                "claims": [{"claim": "The api key is sk-1234567890abcdef123456.", "status": "supported"}],
            },
            notes=[],
        )
    )

    saved_contents = " ".join(item["content"] for item in result["saved"])
    assert "[REDACTED]" in saved_contents
    assert "sk-1234567890abcdef123456" not in saved_contents


def test_memory_status_and_manual_record_shape():
    from agent.memory.models import MemoryRecord

    service, _store = _service()
    saved = service.upsert_record(
        MemoryRecord(
            user_id="u1",
            content="OpenAI and LangGraph are relevant entities for SoulSearcher.",
            confidence=0.9,
        )
    )
    status = service.status()

    assert saved.embedding
    assert status["available"] is True
    assert status["record_count"] == 1
    assert status["entity_count"] >= 1
