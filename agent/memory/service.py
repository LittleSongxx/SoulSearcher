from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from agent.memory.formatting import build_memory_context
from agent.memory.ingestion import build_memory_records_from_run
from agent.memory.models import (
    MemoryEntity,
    MemoryRecord,
    MemoryRelation,
    MemoryRetrievalResult,
    MemoryType,
)
from agent.memory.retrieval import rank_records, tokenize
from agent.memory.skill_evolution import maybe_evolve_skill
from agent.memory.store import InMemoryMemoryStore, MemoryStore, PostgresMemoryStore
from common.config import settings

logger = logging.getLogger(__name__)

ENTITY_RE = re.compile(r"\b[A-Z][A-Za-z0-9&_.-]{2,}(?:\s+[A-Z][A-Za-z0-9&_.-]{2,}){0,3}\b")


class MemoryUnavailableError(RuntimeError):
    pass


class MemoryService:
    def __init__(
        self,
        store: MemoryStore,
        *,
        embedding_model: str = "text-embedding-3-small",
        embedding_dim: int = 1536,
        retrieval_top_k: int = 12,
        retrieval_max_tokens: int = 2500,
        write_min_confidence: float = 0.75,
        write_require_evidence: bool = True,
        auto_skill_evolution: bool = True,
        auto_skill_min_support: int = 3,
        sensitive_write_policy: str = "reject",
    ) -> None:
        self.store = store
        self.embedding_model = embedding_model
        self.embedding_dim = int(embedding_dim or 1536)
        self.retrieval_top_k = int(retrieval_top_k or 12)
        self.retrieval_max_tokens = int(retrieval_max_tokens or 2500)
        self.write_min_confidence = float(write_min_confidence or 0.75)
        self.write_require_evidence = bool(write_require_evidence)
        self.auto_skill_evolution = bool(auto_skill_evolution)
        self.auto_skill_min_support = int(auto_skill_min_support or 3)
        self.sensitive_write_policy = str(sensitive_write_policy or "reject")

    def setup(self) -> None:
        self.store.setup()

    def embed_text(self, text: str) -> list[float]:
        value = str(text or "").strip()
        if not value:
            return []
        try:
            from langchain_openai import OpenAIEmbeddings

            embeddings = OpenAIEmbeddings(model=self.embedding_model)
            vector = embeddings.embed_query(value[:8000])
            return [float(x) for x in vector[: self.embedding_dim]]
        except Exception:
            # Deterministic local fallback: enough for ranking tests and offline dev.
            digest = hashlib.sha256(value.encode("utf-8")).digest()
            dims = min(self.embedding_dim, 64)
            out = []
            for index in range(dims):
                byte = digest[index % len(digest)]
                out.append((byte / 255.0) * 2.0 - 1.0)
            return out

    def upsert_record(self, record: MemoryRecord) -> MemoryRecord:
        if not record.embedding:
            record.embedding = self.embed_text(f"{record.summary}\n{record.content}")
        saved = self.store.upsert_record(record)
        self._index_entities(saved)
        return saved

    def delete_record(self, record_id: str, *, user_id: str = "") -> bool:
        return self.store.soft_delete_record(record_id, user_id=user_id)

    def list_records(
        self,
        *,
        user_id: str,
        query: str = "",
        type: str = "",
        scope: str = "",
        limit: int = 50,
    ) -> list[MemoryRecord]:
        return self.store.list_records(
            user_id=user_id,
            query=query,
            type=type,
            scope=scope,
            limit=limit,
        )

    def retrieve(
        self,
        *,
        user_id: str,
        query: str,
        limit: int | None = None,
        type: str = "",
        scope: str = "",
        include_context: bool = True,
    ) -> MemoryRetrievalResult:
        self.setup()
        broad_limit = max(50, int(limit or self.retrieval_top_k) * 4)
        candidates = self.store.list_records(
            user_id=user_id,
            query="",
            type=type,
            scope=scope,
            limit=broad_limit,
        )
        query_embedding = self.embed_text(query)
        ranked, scoring = rank_records(
            query,
            candidates,
            query_embedding=query_embedding,
            limit=limit or self.retrieval_top_k,
        )
        graph_payload = self._graph_for_query(user_id=user_id, query=query, records=ranked)
        result = MemoryRetrievalResult(
            records=ranked,
            entities=[
                MemoryEntity(
                    id=str(item.get("id")),
                    user_id=str(item.get("user_id") or user_id),
                    name=str(item.get("name") or ""),
                    type=str(item.get("type") or "entity"),
                    aliases=[str(x) for x in (item.get("aliases") or [])],
                    metadata=dict(item.get("metadata") or {}),
                    created_at=str(item.get("created_at") or ""),
                    updated_at=str(item.get("updated_at") or ""),
                )
                for item in graph_payload.get("entities", [])
                if isinstance(item, dict)
            ],
            relations=[
                MemoryRelation(
                    id=str(item.get("id")),
                    user_id=str(item.get("user_id") or user_id),
                    source_entity_id=str(item.get("source_entity_id") or ""),
                    target_entity_id=str(item.get("target_entity_id") or ""),
                    relation=str(item.get("relation") or ""),
                    source_record_id=str(item.get("source_record_id") or ""),
                    confidence=float(item.get("confidence") or 0.0),
                    status=str(item.get("status") or "active"),
                    metadata=dict(item.get("metadata") or {}),
                    created_at=str(item.get("created_at") or ""),
                    updated_at=str(item.get("updated_at") or ""),
                )
                for item in graph_payload.get("relations", [])
                if isinstance(item, dict)
            ],
            scoring=scoring,
        )
        if include_context and result.records:
            result.context = build_memory_context(
                result,
                max_tokens=self.retrieval_max_tokens,
            )
        return result

    async def ingest_research_run(
        self,
        *,
        user_id: str,
        thread_id: str,
        run_id: str,
        research_brief: str,
        final_report: str,
        artifacts: dict[str, Any],
        notes: list[str] | None = None,
    ) -> dict[str, Any]:
        self.setup()
        records, rejected = build_memory_records_from_run(
            user_id=user_id,
            thread_id=thread_id,
            run_id=run_id,
            research_brief=research_brief,
            final_report=final_report,
            artifacts=artifacts,
            notes=notes or [],
            min_confidence=self.write_min_confidence,
            require_evidence=self.write_require_evidence,
            sensitive_write_policy=self.sensitive_write_policy,
        )
        saved = [self.upsert_record(record) for record in records]
        for item in rejected:
            self.store.record_audit("reject_memory_candidate", item)

        procedural = [record for record in saved if record.type == MemoryType.procedure.value]
        proposal = await maybe_evolve_skill(
            store=self.store,
            user_id=user_id,
            procedural_records=procedural,
            min_support=self.auto_skill_min_support,
            enabled=self.auto_skill_evolution,
        )
        return {
            "saved": [record.to_dict() for record in saved],
            "rejected": rejected,
            "skill_evolution": proposal.to_dict() if proposal else None,
        }

    def graph(self, *, user_id: str, entity: str = "", limit: int = 50) -> dict[str, Any]:
        self.setup()
        return self.store.graph(user_id=user_id, entity=entity, limit=limit)

    def status(self) -> dict[str, Any]:
        try:
            return self.store.status()
        except Exception as exc:
            return {
                "backend": getattr(settings, "memory_backend", "postgres"),
                "available": False,
                "error": str(exc),
                "pgvector_available": False,
                "record_count": 0,
                "entity_count": 0,
                "relation_count": 0,
                "skill_evolution_count": 0,
            }

    def list_skill_evolution(self, *, user_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        self.setup()
        return [
            proposal.to_dict()
            for proposal in self.store.list_skill_evolution(user_id=user_id, limit=limit)
        ]

    def _graph_for_query(
        self,
        *,
        user_id: str,
        query: str,
        records: list[MemoryRecord],
    ) -> dict[str, Any]:
        names = list(_extract_entities(query))
        for record in records[:5]:
            names.extend(_extract_entities(record.content))
        seen = set()
        for name in names[:12]:
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            graph = self.store.graph(user_id=user_id, entity=name, limit=30)
            if graph.get("entities"):
                return graph
        return self.store.graph(user_id=user_id, entity="", limit=30)

    def _index_entities(self, record: MemoryRecord) -> None:
        names = list(_extract_entities(record.content))
        if not names:
            return
        entities = [
            self.store.upsert_entity(
                MemoryEntity(
                    user_id=record.user_id,
                    name=name,
                    aliases=[],
                    metadata={"source_record_id": record.id},
                )
            )
            for name in names[:6]
        ]
        if len(entities) < 2:
            return
        root = entities[0]
        for target in entities[1:4]:
            self.store.upsert_relation(
                MemoryRelation(
                    user_id=record.user_id,
                    source_entity_id=root.id,
                    target_entity_id=target.id,
                    relation="co_occurs_with",
                    source_record_id=record.id,
                    confidence=record.confidence,
                    metadata={"memory_record_type": record.type},
                )
            )


def _extract_entities(text: str) -> list[str]:
    result = []
    seen = set()
    for match in ENTITY_RE.findall(str(text or "")):
        value = " ".join(match.split()).strip()
        for candidate in [value, *value.split()]:
            if len(candidate) < 3:
                continue
            key = candidate.casefold()
            if key in seen:
                continue
            if key in tokenize("Research Report Source Evidence"):
                continue
            seen.add(key)
            result.append(candidate[:120])
    return result


_memory_service: MemoryService | None = None


def create_memory_service(store: MemoryStore | None = None) -> MemoryService:
    if store is None:
        backend = str(getattr(settings, "memory_backend", "postgres") or "postgres").lower()
        if backend != "postgres":
            store = InMemoryMemoryStore()
        else:
            database_url = (
                getattr(settings, "memory_database_url", "")
                or getattr(settings, "database_url", "")
            )
            if not database_url:
                raise MemoryUnavailableError(
                    "Memory backend is postgres but MEMORY_DATABASE_URL/DATABASE_URL is not configured"
                )
            store = PostgresMemoryStore(
                database_url,
                embedding_dim=int(getattr(settings, "memory_embedding_dim", 1536)),
            )
    return MemoryService(
        store,
        embedding_model=str(getattr(settings, "memory_embedding_model", "text-embedding-3-small")),
        embedding_dim=int(getattr(settings, "memory_embedding_dim", 1536)),
        retrieval_top_k=int(getattr(settings, "memory_retrieval_top_k", 12)),
        retrieval_max_tokens=int(getattr(settings, "memory_retrieval_max_tokens", 2500)),
        write_min_confidence=float(getattr(settings, "memory_write_min_confidence", 0.75)),
        write_require_evidence=bool(getattr(settings, "memory_write_require_evidence", True)),
        auto_skill_evolution=bool(getattr(settings, "memory_auto_skill_evolution", True)),
        auto_skill_min_support=int(getattr(settings, "memory_auto_skill_min_support", 3)),
        sensitive_write_policy=str(getattr(settings, "memory_sensitive_write_policy", "reject")),
    )


def get_memory_service() -> MemoryService:
    global _memory_service
    if _memory_service is None:
        _memory_service = create_memory_service()
    return _memory_service


def set_memory_service(service: MemoryService | None) -> None:
    global _memory_service
    _memory_service = service
