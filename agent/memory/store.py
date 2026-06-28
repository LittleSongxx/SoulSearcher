from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from agent.memory.models import (
    MemoryEntity,
    MemoryRecord,
    MemoryRelation,
    MemoryStatus,
    SkillEvolutionProposal,
    utc_now,
)

logger = logging.getLogger(__name__)


class MemoryStore(Protocol):
    def setup(self) -> None:
        ...

    def upsert_record(self, record: MemoryRecord) -> MemoryRecord:
        ...

    def list_records(
        self,
        *,
        user_id: str,
        query: str = "",
        type: str = "",
        scope: str = "",
        limit: int = 50,
        include_deleted: bool = False,
    ) -> list[MemoryRecord]:
        ...

    def soft_delete_record(self, record_id: str, *, user_id: str = "") -> bool:
        ...

    def upsert_entity(self, entity: MemoryEntity) -> MemoryEntity:
        ...

    def upsert_relation(self, relation: MemoryRelation) -> MemoryRelation:
        ...

    def graph(self, *, user_id: str, entity: str = "", limit: int = 50) -> dict[str, Any]:
        ...

    def record_audit(self, action: str, payload: dict[str, Any]) -> None:
        ...

    def add_skill_evolution(self, proposal: SkillEvolutionProposal) -> SkillEvolutionProposal:
        ...

    def list_skill_evolution(
        self, *, user_id: str = "", limit: int = 50
    ) -> list[SkillEvolutionProposal]:
        ...

    def status(self) -> dict[str, Any]:
        ...


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


class InMemoryMemoryStore:
    """Deterministic store used by unit tests and dependency injection."""

    def __init__(self) -> None:
        self.records: dict[str, MemoryRecord] = {}
        self.entities: dict[str, MemoryEntity] = {}
        self.relations: dict[str, MemoryRelation] = {}
        self.audit: list[dict[str, Any]] = []
        self.skill_evolution: dict[str, SkillEvolutionProposal] = {}

    def setup(self) -> None:
        return None

    def upsert_record(self, record: MemoryRecord) -> MemoryRecord:
        existing = next(
            (
                item
                for item in self.records.values()
                if item.user_id == record.user_id
                and item.dedupe_key == record.dedupe_key
                and item.status == MemoryStatus.active.value
                and item.id != record.id
            ),
            None,
        )
        if existing:
            existing.status = MemoryStatus.superseded.value
            existing.updated_at = utc_now()
            record.metadata.setdefault("supersedes", existing.id)
        record.updated_at = utc_now()
        self.records[record.id] = record
        self.record_audit("upsert_record", {"record_id": record.id})
        return record

    def list_records(
        self,
        *,
        user_id: str,
        query: str = "",
        type: str = "",
        scope: str = "",
        limit: int = 50,
        include_deleted: bool = False,
    ) -> list[MemoryRecord]:
        result = []
        query_l = query.casefold().strip()
        for record in self.records.values():
            if user_id and record.user_id != user_id:
                continue
            if not include_deleted and record.status != MemoryStatus.active.value:
                continue
            if type and record.type != type:
                continue
            if scope and record.scope != scope:
                continue
            if query_l and query_l not in f"{record.content} {record.summary}".casefold():
                continue
            result.append(record)
        result.sort(key=lambda item: item.updated_at, reverse=True)
        return result[: max(0, int(limit or 0))]

    def soft_delete_record(self, record_id: str, *, user_id: str = "") -> bool:
        record = self.records.get(record_id)
        if not record or (user_id and record.user_id != user_id):
            return False
        record.status = MemoryStatus.deleted.value
        record.updated_at = utc_now()
        self.record_audit("delete_record", {"record_id": record_id})
        return True

    def upsert_entity(self, entity: MemoryEntity) -> MemoryEntity:
        existing = next(
            (
                item
                for item in self.entities.values()
                if item.user_id == entity.user_id
                and item.name.casefold() == entity.name.casefold()
            ),
            None,
        )
        if existing:
            aliases = []
            seen = set()
            for value in list(existing.aliases) + list(entity.aliases):
                key = value.casefold()
                if key not in seen:
                    aliases.append(value)
                    seen.add(key)
            existing.aliases = aliases
            existing.metadata.update(entity.metadata)
            existing.updated_at = utc_now()
            return existing
        self.entities[entity.id] = entity
        return entity

    def upsert_relation(self, relation: MemoryRelation) -> MemoryRelation:
        existing = next(
            (
                item
                for item in self.relations.values()
                if item.user_id == relation.user_id
                and item.source_entity_id == relation.source_entity_id
                and item.target_entity_id == relation.target_entity_id
                and item.relation == relation.relation
            ),
            None,
        )
        if existing:
            existing.confidence = max(existing.confidence, relation.confidence)
            existing.metadata.update(relation.metadata)
            existing.updated_at = utc_now()
            return existing
        self.relations[relation.id] = relation
        return relation

    def graph(self, *, user_id: str, entity: str = "", limit: int = 50) -> dict[str, Any]:
        entity_l = entity.casefold().strip()
        entities = [
            item
            for item in self.entities.values()
            if item.user_id == user_id
            and (
                not entity_l
                or entity_l in item.name.casefold()
                or any(entity_l in alias.casefold() for alias in item.aliases)
            )
        ][:limit]
        ids = {item.id for item in entities}
        relations = [
            item
            for item in self.relations.values()
            if item.user_id == user_id
            and (not ids or item.source_entity_id in ids or item.target_entity_id in ids)
        ][:limit]
        return {
            "entities": [item.to_dict() for item in entities],
            "relations": [item.to_dict() for item in relations],
        }

    def record_audit(self, action: str, payload: dict[str, Any]) -> None:
        self.audit.append({"action": action, "payload": payload, "created_at": utc_now()})

    def add_skill_evolution(self, proposal: SkillEvolutionProposal) -> SkillEvolutionProposal:
        proposal.updated_at = utc_now()
        self.skill_evolution[proposal.id] = proposal
        self.record_audit("skill_evolution", proposal.to_dict())
        return proposal

    def list_skill_evolution(
        self, *, user_id: str = "", limit: int = 50
    ) -> list[SkillEvolutionProposal]:
        items = [
            item
            for item in self.skill_evolution.values()
            if not user_id or item.user_id == user_id
        ]
        items.sort(key=lambda item: item.updated_at, reverse=True)
        return items[:limit]

    def status(self) -> dict[str, Any]:
        return {
            "backend": "memory",
            "available": True,
            "pgvector_available": False,
            "record_count": len(self.records),
            "entity_count": len(self.entities),
            "relation_count": len(self.relations),
            "skill_evolution_count": len(self.skill_evolution),
        }


class PostgresMemoryStore:
    def __init__(self, database_url: str, *, embedding_dim: int = 1536) -> None:
        self.database_url = database_url
        self.embedding_dim = int(embedding_dim or 1536)
        self.pgvector_available = False
        self._setup_done = False

    def _connect(self):
        import psycopg

        conn = psycopg.connect(
            self.database_url,
            autocommit=True,
            connect_timeout=3,
        )
        try:
            from pgvector.psycopg import register_vector

            register_vector(conn)
        except Exception as exc:
            logger.debug("[Memory] pgvector adapter registration skipped: %s", exc)
        return conn

    def setup(self) -> None:
        if self._setup_done:
            return
        with self._connect() as conn, conn.cursor() as cur:
            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                self.pgvector_available = True
            except Exception as exc:
                self.pgvector_available = False
                logger.warning("[Memory] pgvector unavailable, using JSON embeddings: %s", exc)
            vector_type = (
                f"vector({self.embedding_dim})" if self.pgvector_available else "jsonb"
            )
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS weaver_memory_records (
                    id text PRIMARY KEY,
                    user_id text NOT NULL,
                    scope text NOT NULL,
                    type text NOT NULL,
                    content text NOT NULL,
                    summary text NOT NULL DEFAULT '',
                    embedding {vector_type},
                    confidence double precision NOT NULL DEFAULT 0.75,
                    importance double precision NOT NULL DEFAULT 0.5,
                    quality_score double precision NOT NULL DEFAULT 0,
                    source_thread_id text NOT NULL DEFAULT '',
                    source_run_id text NOT NULL DEFAULT '',
                    source_evidence_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
                    source_urls jsonb NOT NULL DEFAULT '[]'::jsonb,
                    valid_from timestamptz,
                    valid_to timestamptz,
                    expires_at timestamptz,
                    status text NOT NULL DEFAULT 'active',
                    metadata jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                    dedupe_key text NOT NULL,
                    created_at timestamptz NOT NULL DEFAULT now(),
                    updated_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS weaver_memory_entities (
                    id text PRIMARY KEY,
                    user_id text NOT NULL,
                    name text NOT NULL,
                    type text NOT NULL DEFAULT 'entity',
                    aliases jsonb NOT NULL DEFAULT '[]'::jsonb,
                    embedding jsonb NOT NULL DEFAULT '[]'::jsonb,
                    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                    created_at timestamptz NOT NULL DEFAULT now(),
                    updated_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS weaver_memory_relations (
                    id text PRIMARY KEY,
                    user_id text NOT NULL,
                    source_entity_id text NOT NULL,
                    target_entity_id text NOT NULL,
                    relation text NOT NULL,
                    source_record_id text NOT NULL DEFAULT '',
                    confidence double precision NOT NULL DEFAULT 0.75,
                    valid_from timestamptz,
                    valid_to timestamptz,
                    status text NOT NULL DEFAULT 'active',
                    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                    created_at timestamptz NOT NULL DEFAULT now(),
                    updated_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS weaver_memory_audit_log (
                    id bigserial PRIMARY KEY,
                    action text NOT NULL,
                    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
                    created_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS weaver_skill_evolution (
                    id text PRIMARY KEY,
                    user_id text NOT NULL,
                    skill_name text NOT NULL,
                    content text NOT NULL,
                    rationale text NOT NULL DEFAULT '',
                    support_count integer NOT NULL DEFAULT 1,
                    confidence double precision NOT NULL DEFAULT 0.75,
                    status text NOT NULL DEFAULT 'proposed',
                    validation_status text NOT NULL DEFAULT 'pending',
                    applied_path text NOT NULL DEFAULT '',
                    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                    created_at timestamptz NOT NULL DEFAULT now(),
                    updated_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_weaver_memory_user_status ON weaver_memory_records(user_id, status)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_weaver_memory_type_scope ON weaver_memory_records(type, scope)"
            )
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_weaver_memory_dedupe ON weaver_memory_records(user_id, dedupe_key) WHERE status = 'active'"
            )
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_weaver_memory_entity_name ON weaver_memory_entities(user_id, lower(name))"
            )
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_weaver_memory_relation_unique ON weaver_memory_relations(user_id, source_entity_id, target_entity_id, relation)"
            )
        self._setup_done = True

    def _dict_row(self):
        from psycopg.rows import dict_row

        return dict_row

    def _record_from_row(self, row: dict[str, Any]) -> MemoryRecord:
        data = dict(row)
        data["source_evidence_ids"] = _as_list(data.get("source_evidence_ids"))
        data["source_urls"] = _as_list(data.get("source_urls"))
        data["metadata"] = _as_dict(data.get("metadata"))
        embedding = data.get("embedding") or []
        if isinstance(embedding, str):
            if embedding.startswith("["):
                try:
                    embedding = json.loads(embedding)
                except json.JSONDecodeError:
                    embedding = []
            else:
                embedding = []
        data["embedding"] = _as_list(embedding)
        for key in ("valid_from", "valid_to", "expires_at", "created_at", "updated_at"):
            if data.get(key) is not None:
                data[key] = str(data[key])
        return MemoryRecord.from_dict(data)

    def upsert_record(self, record: MemoryRecord) -> MemoryRecord:
        self.setup()
        with self._connect() as conn, conn.cursor(row_factory=self._dict_row()) as cur:
            cur.execute(
                """
                    UPDATE weaver_memory_records
                    SET status='superseded', updated_at=now()
                    WHERE user_id=%s AND dedupe_key=%s AND status='active' AND id<>%s
                    RETURNING id
                    """,
                (record.user_id, record.dedupe_key, record.id),
            )
            superseded = [row["id"] for row in cur.fetchall()]
            if superseded:
                record.metadata.setdefault("supersedes", superseded[0])
            embedding = record.embedding if self.pgvector_available else _json(record.embedding)
            cur.execute(
                """
                    INSERT INTO weaver_memory_records (
                        id, user_id, scope, type, content, summary, embedding,
                        confidence, importance, quality_score, source_thread_id,
                        source_run_id, source_evidence_ids, source_urls, valid_from,
                        valid_to, expires_at, status, metadata, dedupe_key, created_at,
                        updated_at
                    ) VALUES (
                        %(id)s, %(user_id)s, %(scope)s, %(type)s, %(content)s,
                        %(summary)s, %(embedding)s, %(confidence)s, %(importance)s,
                        %(quality_score)s, %(source_thread_id)s, %(source_run_id)s,
                        %(source_evidence_ids)s::jsonb, %(source_urls)s::jsonb,
                        %(valid_from)s, %(valid_to)s, %(expires_at)s, %(status)s,
                        %(metadata)s::jsonb, %(dedupe_key)s, %(created_at)s,
                        %(updated_at)s
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        content=EXCLUDED.content,
                        summary=EXCLUDED.summary,
                        embedding=EXCLUDED.embedding,
                        confidence=EXCLUDED.confidence,
                        importance=EXCLUDED.importance,
                        quality_score=EXCLUDED.quality_score,
                        source_evidence_ids=EXCLUDED.source_evidence_ids,
                        source_urls=EXCLUDED.source_urls,
                        status=EXCLUDED.status,
                        metadata=EXCLUDED.metadata,
                        updated_at=now()
                    RETURNING *
                    """,
                {
                    **record.to_dict(),
                    "embedding": embedding,
                    "source_evidence_ids": _json(record.source_evidence_ids),
                    "source_urls": _json(record.source_urls),
                    "metadata": _json(record.metadata),
                },
            )
            saved = self._record_from_row(cur.fetchone())
        self.record_audit("upsert_record", {"record_id": saved.id})
        return saved

    def list_records(
        self,
        *,
        user_id: str,
        query: str = "",
        type: str = "",
        scope: str = "",
        limit: int = 50,
        include_deleted: bool = False,
    ) -> list[MemoryRecord]:
        self.setup()
        where = ["user_id = %(user_id)s"]
        params: dict[str, Any] = {"user_id": user_id, "limit": max(1, int(limit or 50))}
        if not include_deleted:
            where.append("status = 'active'")
        if type:
            where.append("type = %(type)s")
            params["type"] = type
        if scope:
            where.append("scope = %(scope)s")
            params["scope"] = scope
        if query:
            where.append("(content ILIKE %(query)s OR summary ILIKE %(query)s)")
            params["query"] = f"%{query}%"
        sql = (
            "SELECT * FROM weaver_memory_records WHERE "
            + " AND ".join(where)
            + " ORDER BY updated_at DESC LIMIT %(limit)s"
        )
        with self._connect() as conn, conn.cursor(row_factory=self._dict_row()) as cur:
            cur.execute(sql, params)
            return [self._record_from_row(row) for row in cur.fetchall()]

    def soft_delete_record(self, record_id: str, *, user_id: str = "") -> bool:
        self.setup()
        where = "id=%s"
        params: list[Any] = [record_id]
        if user_id:
            where += " AND user_id=%s"
            params.append(user_id)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE weaver_memory_records SET status='deleted', updated_at=now() WHERE {where}",
                params,
            )
            deleted = cur.rowcount > 0
        if deleted:
            self.record_audit("delete_record", {"record_id": record_id})
        return deleted

    def upsert_entity(self, entity: MemoryEntity) -> MemoryEntity:
        self.setup()
        with self._connect() as conn, conn.cursor(row_factory=self._dict_row()) as cur:
            payload = {
                **entity.to_dict(),
                "aliases": _json(entity.aliases),
                "embedding": _json(entity.embedding),
                "metadata": _json(entity.metadata),
            }
            cur.execute(
                """
                    SELECT id FROM weaver_memory_entities
                    WHERE user_id=%(user_id)s AND lower(name)=lower(%(name)s)
                    """,
                payload,
            )
            existing = cur.fetchone()
            if existing:
                payload["id"] = existing["id"]
                cur.execute(
                    """
                        UPDATE weaver_memory_entities
                        SET aliases=%(aliases)s::jsonb,
                            metadata=metadata || %(metadata)s::jsonb,
                            updated_at=now()
                        WHERE id=%(id)s
                        RETURNING *
                        """,
                    payload,
                )
            else:
                cur.execute(
                    """
                        INSERT INTO weaver_memory_entities (
                            id, user_id, name, type, aliases, embedding, metadata,
                            created_at, updated_at
                        ) VALUES (
                            %(id)s, %(user_id)s, %(name)s, %(type)s,
                            %(aliases)s::jsonb, %(embedding)s::jsonb,
                            %(metadata)s::jsonb, %(created_at)s, %(updated_at)s
                        )
                        RETURNING *
                        """,
                    payload,
                )
            row = cur.fetchone()
        return MemoryEntity(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            name=str(row["name"]),
            type=str(row["type"]),
            aliases=[str(x) for x in _as_list(row.get("aliases"))],
            embedding=[float(x) for x in _as_list(row.get("embedding"))],
            metadata=_as_dict(row.get("metadata")),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def upsert_relation(self, relation: MemoryRelation) -> MemoryRelation:
        self.setup()
        with self._connect() as conn, conn.cursor(row_factory=self._dict_row()) as cur:
            payload = {**relation.to_dict(), "metadata": _json(relation.metadata)}
            cur.execute(
                """
                    SELECT id FROM weaver_memory_relations
                    WHERE user_id=%(user_id)s
                    AND source_entity_id=%(source_entity_id)s
                    AND target_entity_id=%(target_entity_id)s
                    AND relation=%(relation)s
                    """,
                payload,
            )
            existing = cur.fetchone()
            if existing:
                payload["id"] = existing["id"]
                cur.execute(
                    """
                        UPDATE weaver_memory_relations
                        SET confidence=GREATEST(confidence, %(confidence)s),
                            metadata=metadata || %(metadata)s::jsonb,
                            updated_at=now()
                        WHERE id=%(id)s
                        RETURNING *
                        """,
                    payload,
                )
            else:
                cur.execute(
                    """
                        INSERT INTO weaver_memory_relations (
                            id, user_id, source_entity_id, target_entity_id, relation,
                            source_record_id, confidence, valid_from, valid_to, status,
                            metadata, created_at, updated_at
                        ) VALUES (
                            %(id)s, %(user_id)s, %(source_entity_id)s,
                            %(target_entity_id)s, %(relation)s, %(source_record_id)s,
                            %(confidence)s, %(valid_from)s, %(valid_to)s, %(status)s,
                            %(metadata)s::jsonb, %(created_at)s, %(updated_at)s
                        )
                        RETURNING *
                        """,
                    payload,
                )
            row = cur.fetchone()
        return MemoryRelation(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            source_entity_id=str(row["source_entity_id"]),
            target_entity_id=str(row["target_entity_id"]),
            relation=str(row["relation"]),
            source_record_id=str(row["source_record_id"] or ""),
            confidence=float(row["confidence"] or 0.0),
            valid_from=str(row["valid_from"]) if row["valid_from"] else None,
            valid_to=str(row["valid_to"]) if row["valid_to"] else None,
            status=str(row["status"]),
            metadata=_as_dict(row["metadata"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def graph(self, *, user_id: str, entity: str = "", limit: int = 50) -> dict[str, Any]:
        self.setup()
        entity_filter = ""
        params: dict[str, Any] = {"user_id": user_id, "limit": max(1, int(limit or 50))}
        if entity:
            entity_filter = "AND (name ILIKE %(entity)s OR aliases::text ILIKE %(entity)s)"
            params["entity"] = f"%{entity}%"
        with self._connect() as conn, conn.cursor(row_factory=self._dict_row()) as cur:
            cur.execute(
                f"""
                    SELECT * FROM weaver_memory_entities
                    WHERE user_id=%(user_id)s {entity_filter}
                    ORDER BY updated_at DESC LIMIT %(limit)s
                    """,
                params,
            )
            entities = [dict(row) for row in cur.fetchall()]
            ids = [row["id"] for row in entities]
            if ids:
                cur.execute(
                    """
                        SELECT * FROM weaver_memory_relations
                        WHERE user_id=%(user_id)s
                        AND (
                            source_entity_id = ANY(%(ids)s::text[])
                            OR target_entity_id = ANY(%(ids)s::text[])
                        )
                        LIMIT %(limit)s
                        """,
                    {"user_id": user_id, "ids": ids, "limit": params["limit"]},
                )
            else:
                cur.execute(
                    """
                        SELECT * FROM weaver_memory_relations
                        WHERE user_id=%(user_id)s
                        ORDER BY updated_at DESC LIMIT %(limit)s
                        """,
                    params,
                )
            relations = [dict(row) for row in cur.fetchall()]
        return {
            "entities": [
                {
                    **row,
                    "aliases": _as_list(row.get("aliases")),
                    "embedding": _as_list(row.get("embedding")),
                    "metadata": _as_dict(row.get("metadata")),
                    "created_at": str(row.get("created_at")),
                    "updated_at": str(row.get("updated_at")),
                }
                for row in entities
            ],
            "relations": [
                {
                    **row,
                    "metadata": _as_dict(row.get("metadata")),
                    "created_at": str(row.get("created_at")),
                    "updated_at": str(row.get("updated_at")),
                }
                for row in relations
            ],
        }

    def record_audit(self, action: str, payload: dict[str, Any]) -> None:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO weaver_memory_audit_log(action, payload) VALUES (%s, %s::jsonb)",
                    (action, _json(payload)),
                )
        except Exception as exc:
            logger.debug("[Memory] audit write skipped: %s", exc)

    def add_skill_evolution(self, proposal: SkillEvolutionProposal) -> SkillEvolutionProposal:
        self.setup()
        with self._connect() as conn, conn.cursor(row_factory=self._dict_row()) as cur:
            cur.execute(
                """
                    INSERT INTO weaver_skill_evolution (
                        id, user_id, skill_name, content, rationale, support_count,
                        confidence, status, validation_status, applied_path,
                        metadata, created_at, updated_at
                    ) VALUES (
                        %(id)s, %(user_id)s, %(skill_name)s, %(content)s,
                        %(rationale)s, %(support_count)s, %(confidence)s,
                        %(status)s, %(validation_status)s, %(applied_path)s,
                        %(metadata)s::jsonb, %(created_at)s, %(updated_at)s
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        status=EXCLUDED.status,
                        validation_status=EXCLUDED.validation_status,
                        applied_path=EXCLUDED.applied_path,
                        metadata=EXCLUDED.metadata,
                        updated_at=now()
                    RETURNING *
                    """,
                {**proposal.to_dict(), "metadata": _json(proposal.metadata)},
            )
            row = cur.fetchone()
        return SkillEvolutionProposal(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            skill_name=str(row["skill_name"]),
            content=str(row["content"]),
            rationale=str(row["rationale"]),
            support_count=int(row["support_count"]),
            confidence=float(row["confidence"]),
            status=str(row["status"]),
            validation_status=str(row["validation_status"]),
            applied_path=str(row["applied_path"] or ""),
            metadata=_as_dict(row["metadata"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def list_skill_evolution(
        self, *, user_id: str = "", limit: int = 50
    ) -> list[SkillEvolutionProposal]:
        self.setup()
        where = []
        params: dict[str, Any] = {"limit": max(1, int(limit or 50))}
        if user_id:
            where.append("user_id=%(user_id)s")
            params["user_id"] = user_id
        sql = "SELECT * FROM weaver_skill_evolution"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC LIMIT %(limit)s"
        with self._connect() as conn, conn.cursor(row_factory=self._dict_row()) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [
            SkillEvolutionProposal(
                id=str(row["id"]),
                user_id=str(row["user_id"]),
                skill_name=str(row["skill_name"]),
                content=str(row["content"]),
                rationale=str(row["rationale"]),
                support_count=int(row["support_count"]),
                confidence=float(row["confidence"]),
                status=str(row["status"]),
                validation_status=str(row["validation_status"]),
                applied_path=str(row["applied_path"] or ""),
                metadata=_as_dict(row["metadata"]),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        ]

    def status(self) -> dict[str, Any]:
        self.setup()
        with self._connect() as conn, conn.cursor() as cur:
            counts = {}
            for key, table in (
                ("record_count", "weaver_memory_records"),
                ("entity_count", "weaver_memory_entities"),
                ("relation_count", "weaver_memory_relations"),
                ("skill_evolution_count", "weaver_skill_evolution"),
            ):
                cur.execute(f"SELECT count(*) FROM {table}")
                counts[key] = int(cur.fetchone()[0])
        return {
            "backend": "postgres",
            "available": True,
            "pgvector_available": self.pgvector_available,
            "embedding_dim": self.embedding_dim,
            **counts,
        }
