from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from agent.memory import (
    MemoryRecord,
    MemoryScope,
    MemoryType,
    MemoryUnavailableError,
)


class MemoryStatusResponse(BaseModel):
    backend: str
    available: bool = False
    pgvector_available: bool = False
    embedding_model: str = ""
    embedding_dim: int = 0
    record_count: int = 0
    entity_count: int = 0
    relation_count: int = 0
    skill_evolution_count: int = 0
    error: Optional[str] = None


class MemoryListResponse(BaseModel):
    records: list[dict[str, Any]]
    count: int
    query: str = ""
    user_id: str


class MemoryRecordCreateRequest(BaseModel):
    content: str
    user_id: Optional[str] = None
    scope: str = MemoryScope.user.value
    type: str = MemoryType.fact.value
    summary: str = ""
    confidence: float = 0.75
    importance: float = 0.5
    quality_score: float = 0.0
    source_thread_id: str = ""
    source_run_id: str = ""
    source_evidence_ids: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    expires_at: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryRecordResponse(BaseModel):
    record: dict[str, Any]


class MemoryDeleteResponse(BaseModel):
    deleted: bool


class MemoryRetrieveRequest(BaseModel):
    query: str
    user_id: Optional[str] = None
    type: str = ""
    scope: str = ""
    limit: int = 12
    include_context: bool = True


class MemoryRetrieveResponse(BaseModel):
    records: list[dict[str, Any]]
    entities: list[dict[str, Any]]
    relations: list[dict[str, Any]]
    scoring: list[dict[str, Any]]
    context: str = ""
    user_id: str


class MemoryGraphResponse(BaseModel):
    entities: list[dict[str, Any]] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)


class MemorySkillEvolutionResponse(BaseModel):
    proposals: list[dict[str, Any]]
    count: int


@dataclass(slots=True)
class MemoryRouterDeps:
    settings: Any
    get_memory_service: Any


router = APIRouter()
_deps: MemoryRouterDeps | None = None


def configure_memory_router(deps: MemoryRouterDeps) -> APIRouter:
    global _deps
    _deps = deps
    return router


def _settings() -> Any:
    if _deps is None:
        raise RuntimeError("Memory router is not configured")
    return _deps.settings


def _memory_service() -> Any:
    if _deps is None:
        raise RuntimeError("Memory router is not configured")
    return _deps.get_memory_service()


def _request_user_id(request: Request, explicit_user_id: str | None = None) -> str:
    settings = _settings()
    internal_key = (getattr(settings, "internal_api_key", "") or "").strip()
    principal_id = (getattr(request.state, "principal_id", "") or "").strip()
    if internal_key and principal_id:
        explicit = (explicit_user_id or "").strip()
        if explicit and explicit != principal_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        return principal_id
    return (explicit_user_id or settings.memory_user_id or "default_user").strip()


@router.get("/api/memory/status", response_model=MemoryStatusResponse)
async def memory_status():
    if not getattr(_settings(), "memory_enabled", True):
        return {
            "backend": "disabled",
            "available": False,
            "error": "Memory is disabled",
        }
    try:
        return _memory_service().status()
    except MemoryUnavailableError as exc:
        return {
            "backend": "unavailable",
            "available": False,
            "error": str(exc),
        }


@router.get("/api/memory", response_model=MemoryListResponse)
async def list_memory_records(
    request: Request,
    query: str = "",
    type: str = "",
    scope: str = "",
    limit: int = 50,
    user_id: Optional[str] = None,
):
    if not getattr(_settings(), "memory_enabled", True):
        raise HTTPException(status_code=503, detail="Memory is disabled")
    resolved_user_id = _request_user_id(request, user_id)
    try:
        records = _memory_service().list_records(
            user_id=resolved_user_id,
            query=query,
            type=type,
            scope=scope,
            limit=max(1, min(int(limit or 50), 200)),
        )
        return {
            "records": [record.to_dict() for record in records],
            "count": len(records),
            "query": query,
            "user_id": resolved_user_id,
        }
    except MemoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/memory/records", response_model=MemoryRecordResponse)
async def create_memory_record(request: Request, payload: MemoryRecordCreateRequest):
    if not getattr(_settings(), "memory_enabled", True):
        raise HTTPException(status_code=503, detail="Memory is disabled")
    content = (payload.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    resolved_user_id = _request_user_id(request, payload.user_id)
    try:
        record = MemoryRecord(
            user_id=resolved_user_id,
            scope=payload.scope,
            type=payload.type,
            content=content,
            summary=payload.summary,
            confidence=payload.confidence,
            importance=payload.importance,
            quality_score=payload.quality_score,
            source_thread_id=payload.source_thread_id,
            source_run_id=payload.source_run_id,
            source_evidence_ids=payload.source_evidence_ids,
            source_urls=payload.source_urls,
            valid_from=payload.valid_from,
            valid_to=payload.valid_to,
            expires_at=payload.expires_at,
            metadata={**payload.metadata, "manual": True},
        )
        saved = _memory_service().upsert_record(record)
        return {"record": saved.to_dict()}
    except MemoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/api/memory/records/{record_id}", response_model=MemoryDeleteResponse)
async def delete_memory_record(
    record_id: str,
    request: Request,
    user_id: Optional[str] = None,
):
    if not getattr(_settings(), "memory_enabled", True):
        raise HTTPException(status_code=503, detail="Memory is disabled")
    resolved_user_id = _request_user_id(request, user_id)
    try:
        deleted = _memory_service().delete_record(
            record_id,
            user_id=resolved_user_id,
        )
        if not deleted:
            raise HTTPException(status_code=404, detail="Memory record not found")
        return {"deleted": True}
    except HTTPException:
        raise
    except MemoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/memory/retrieve", response_model=MemoryRetrieveResponse)
async def retrieve_memory(request: Request, payload: MemoryRetrieveRequest):
    if not getattr(_settings(), "memory_enabled", True):
        raise HTTPException(status_code=503, detail="Memory is disabled")
    query = (payload.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    resolved_user_id = _request_user_id(request, payload.user_id)
    try:
        result = _memory_service().retrieve(
            user_id=resolved_user_id,
            query=query,
            type=payload.type,
            scope=payload.scope,
            limit=max(1, min(int(payload.limit or 12), 50)),
            include_context=payload.include_context,
        )
        return {
            "records": [record.to_dict() for record in result.records],
            "entities": [entity.to_dict() for entity in result.entities],
            "relations": [relation.to_dict() for relation in result.relations],
            "scoring": result.scoring,
            "context": result.context,
            "user_id": resolved_user_id,
        }
    except MemoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/memory/graph", response_model=MemoryGraphResponse)
async def get_memory_graph(
    request: Request,
    entity: str = "",
    limit: int = 50,
    user_id: Optional[str] = None,
):
    if not getattr(_settings(), "memory_enabled", True):
        raise HTTPException(status_code=503, detail="Memory is disabled")
    resolved_user_id = _request_user_id(request, user_id)
    try:
        return _memory_service().graph(
            user_id=resolved_user_id,
            entity=entity,
            limit=max(1, min(int(limit or 50), 200)),
        )
    except MemoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/memory/skill-evolution", response_model=MemorySkillEvolutionResponse)
async def list_memory_skill_evolution(
    request: Request,
    limit: int = 50,
    user_id: Optional[str] = None,
):
    if not getattr(_settings(), "memory_enabled", True):
        raise HTTPException(status_code=503, detail="Memory is disabled")
    resolved_user_id = _request_user_id(request, user_id)
    try:
        proposals = _memory_service().list_skill_evolution(
            user_id=resolved_user_id,
            limit=max(1, min(int(limit or 50), 200)),
        )
        return {"proposals": proposals, "count": len(proposals)}
    except MemoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
