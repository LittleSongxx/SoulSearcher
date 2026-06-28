from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def stable_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class MemoryScope(str, Enum):
    user = "user"
    research = "research"
    global_ = "global"


class MemoryType(str, Enum):
    profile = "profile"
    preference = "preference"
    fact = "fact"
    research_finding = "research_finding"
    episode = "episode"
    procedure = "procedure"
    source = "source"


class MemoryStatus(str, Enum):
    active = "active"
    superseded = "superseded"
    deleted = "deleted"
    rejected = "rejected"


class SkillEvolutionStatus(str, Enum):
    proposed = "proposed"
    applied = "applied"
    rejected = "rejected"
    failed = "failed"


@dataclass(slots=True)
class MemoryRecord:
    content: str
    user_id: str = "default"
    scope: str = MemoryScope.user.value
    type: str = MemoryType.fact.value
    summary: str = ""
    id: str = field(default_factory=lambda: f"mem_{uuid.uuid4().hex}")
    embedding: list[float] = field(default_factory=list)
    confidence: float = 0.75
    importance: float = 0.5
    quality_score: float = 0.0
    source_thread_id: str = ""
    source_run_id: str = ""
    source_evidence_ids: list[str] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    valid_from: str | None = None
    valid_to: str | None = None
    expires_at: str | None = None
    status: str = MemoryStatus.active.value
    metadata: dict[str, Any] = field(default_factory=dict)
    dedupe_key: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.content = str(self.content or "").strip()
        self.summary = str(self.summary or "").strip()
        if not self.dedupe_key:
            self.dedupe_key = stable_hash(
                f"{self.user_id}|{self.scope}|{self.type}|{self.content}"
            )
        self.confidence = max(0.0, min(1.0, float(self.confidence or 0.0)))
        self.importance = max(0.0, min(1.0, float(self.importance or 0.0)))
        self.quality_score = max(0.0, min(1.0, float(self.quality_score or 0.0)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "scope": self.scope,
            "type": self.type,
            "content": self.content,
            "summary": self.summary,
            "embedding": list(self.embedding),
            "confidence": self.confidence,
            "importance": self.importance,
            "quality_score": self.quality_score,
            "source_thread_id": self.source_thread_id,
            "source_run_id": self.source_run_id,
            "source_evidence_ids": list(self.source_evidence_ids),
            "source_urls": list(self.source_urls),
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "expires_at": self.expires_at,
            "status": self.status,
            "metadata": dict(self.metadata),
            "dedupe_key": self.dedupe_key,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryRecord:
        return cls(
            id=str(data.get("id") or f"mem_{uuid.uuid4().hex}"),
            user_id=str(data.get("user_id") or "default"),
            scope=str(data.get("scope") or MemoryScope.user.value),
            type=str(data.get("type") or MemoryType.fact.value),
            content=str(data.get("content") or ""),
            summary=str(data.get("summary") or ""),
            embedding=[
                float(x)
                for x in (data.get("embedding") or [])
                if isinstance(x, (int, float))
            ],
            confidence=float(data.get("confidence", 0.75) or 0.0),
            importance=float(data.get("importance", 0.5) or 0.0),
            quality_score=float(data.get("quality_score", 0.0) or 0.0),
            source_thread_id=str(data.get("source_thread_id") or ""),
            source_run_id=str(data.get("source_run_id") or ""),
            source_evidence_ids=[
                str(x) for x in (data.get("source_evidence_ids") or [])
            ],
            source_urls=[str(x) for x in (data.get("source_urls") or [])],
            valid_from=data.get("valid_from"),
            valid_to=data.get("valid_to"),
            expires_at=data.get("expires_at"),
            status=str(data.get("status") or MemoryStatus.active.value),
            metadata=dict(data.get("metadata") or {}),
            dedupe_key=str(data.get("dedupe_key") or ""),
            created_at=str(data.get("created_at") or utc_now()),
            updated_at=str(data.get("updated_at") or utc_now()),
        )


@dataclass(slots=True)
class MemoryEntity:
    name: str
    user_id: str = "default"
    type: str = "entity"
    id: str = field(default_factory=lambda: f"ent_{uuid.uuid4().hex}")
    aliases: list[str] = field(default_factory=list)
    embedding: list[float] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "type": self.type,
            "aliases": list(self.aliases),
            "embedding": list(self.embedding),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(slots=True)
class MemoryRelation:
    source_entity_id: str
    target_entity_id: str
    relation: str
    user_id: str = "default"
    id: str = field(default_factory=lambda: f"rel_{uuid.uuid4().hex}")
    source_record_id: str = ""
    confidence: float = 0.75
    valid_from: str | None = None
    valid_to: str | None = None
    status: str = MemoryStatus.active.value
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "source_entity_id": self.source_entity_id,
            "target_entity_id": self.target_entity_id,
            "relation": self.relation,
            "source_record_id": self.source_record_id,
            "confidence": self.confidence,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "status": self.status,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(slots=True)
class MemoryEpisode:
    thread_id: str
    run_id: str
    user_id: str = "default"
    research_brief: str = ""
    final_report: str = ""
    quality_summary: dict[str, Any] = field(default_factory=dict)
    evidence_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SkillEvolutionProposal:
    skill_name: str
    content: str
    rationale: str
    user_id: str = "default"
    id: str = field(default_factory=lambda: f"skill_evo_{uuid.uuid4().hex}")
    support_count: int = 1
    confidence: float = 0.75
    status: str = SkillEvolutionStatus.proposed.value
    validation_status: str = "pending"
    applied_path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "skill_name": self.skill_name,
            "content": self.content,
            "rationale": self.rationale,
            "support_count": self.support_count,
            "confidence": self.confidence,
            "status": self.status,
            "validation_status": self.validation_status,
            "applied_path": self.applied_path,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(slots=True)
class MemoryRetrievalResult:
    records: list[MemoryRecord] = field(default_factory=list)
    entities: list[MemoryEntity] = field(default_factory=list)
    relations: list[MemoryRelation] = field(default_factory=list)
    scoring: list[dict[str, Any]] = field(default_factory=list)
    context: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "records": [record.to_dict() for record in self.records],
            "entities": [entity.to_dict() for entity in self.entities],
            "relations": [relation.to_dict() for relation in self.relations],
            "scoring": list(self.scoring),
            "context": self.context,
        }
