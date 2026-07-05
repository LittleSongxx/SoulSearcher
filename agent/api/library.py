import hashlib
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agent.retrieval.documents import DocumentLibraryUnavailable, get_document_library
from agent.retrieval.gateway import retrieve_sources
from agent.retrieval.policy import LegacySourceRoutingError, build_retrieval_policy
from agent.runtime.idempotency import (
    IdempotencyConflictError,
    canonical_request_hash,
    idempotency_store,
)


class RetrievalSearchRequest(BaseModel):
    query: str
    user_id: Optional[str] = None
    retrieval_policy: dict[str, Any] = Field(default_factory=dict)
    max_results: int = 8


@dataclass(frozen=True)
class LibraryRouterDeps:
    settings: Any
    logger: Any


def build_library_router(deps: LibraryRouterDeps) -> APIRouter:
    router = APIRouter()

    def _request_user_id(request: Request, explicit_user_id: str | None = None) -> str:
        internal_key = (getattr(deps.settings, "internal_api_key", "") or "").strip()
        principal_id = (getattr(request.state, "principal_id", "") or "").strip()
        if internal_key and principal_id:
            explicit = (explicit_user_id or "").strip()
            if explicit and explicit != principal_id:
                raise HTTPException(status_code=403, detail="Forbidden")
            return principal_id
        return (
            explicit_user_id
            or getattr(deps.settings, "memory_user_id", "")
            or "default_user"
        ).strip()

    def _idempotency_key(request: Request) -> str:
        return (
            request.headers.get("Idempotency-Key")
            or request.headers.get("X-Idempotency-Key")
            or ""
        ).strip()

    def _begin_idempotency(
        request: Request,
        *,
        scope: str,
        user_id: str,
        payload: Any,
    ):
        key = _idempotency_key(request)
        if not key:
            return None
        try:
            return idempotency_store.begin(
                key=key,
                scope=scope,
                user_id=user_id,
                request_hash=canonical_request_hash(payload),
            )
        except IdempotencyConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    def _idempotent_response(record: Any) -> JSONResponse | None:
        if record is not None and getattr(record, "status", "") == "completed":
            return JSONResponse(
                status_code=int(getattr(record, "http_status", 200) or 200),
                content=dict(getattr(record, "response", {}) or {}),
                headers={"X-Idempotency-Replayed": "true"},
            )
        return None

    @router.get("/api/library/status")
    async def document_library_status():
        return get_document_library().status()

    @router.post("/api/library/documents")
    async def upload_library_document(
        request: Request,
        file: UploadFile = File(...),
        user_id: Optional[str] = None,
    ):
        owner_id = _request_user_id(request, user_id)
        try:
            data = await file.read()
            idem_key = _idempotency_key(request)
            idempotency_record = _begin_idempotency(
                request,
                scope="POST /api/library/documents",
                user_id=owner_id,
                payload={
                    "filename": file.filename or "document.txt",
                    "content_type": file.content_type or "",
                    "sha256": hashlib.sha256(data or b"").hexdigest(),
                },
            )
            replay = _idempotent_response(idempotency_record)
            if replay is not None:
                return replay
            result = get_document_library().upload_document(
                user_id=owner_id,
                filename=file.filename or "document.txt",
                content_type=file.content_type or "",
                data=data,
                idempotency_key=idem_key,
            )
            response_payload = {"document": result}
            idempotency_store.complete(idempotency_record, response=response_payload)
            return response_payload
        except DocumentLibraryUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            deps.logger.error("Document upload failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/api/library/documents")
    async def list_library_documents(request: Request, user_id: Optional[str] = None):
        owner_id = _request_user_id(request, user_id)
        try:
            return {"documents": get_document_library().list_documents(user_id=owner_id)}
        except DocumentLibraryUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            deps.logger.error("Document list failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.delete("/api/library/documents/{document_id}")
    async def delete_library_document(
        document_id: str,
        request: Request,
        user_id: Optional[str] = None,
    ):
        owner_id = _request_user_id(request, user_id)
        try:
            deleted = get_document_library().delete_document(
                user_id=owner_id,
                document_id=document_id,
            )
            if not deleted:
                raise HTTPException(status_code=404, detail="Document not found")
            return {"deleted": True, "document_id": document_id}
        except HTTPException:
            raise
        except DocumentLibraryUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            deps.logger.error("Document delete failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/api/library/documents/{document_id}/reindex")
    async def reindex_library_document(
        document_id: str,
        request: Request,
        user_id: Optional[str] = None,
    ):
        owner_id = _request_user_id(request, user_id)
        try:
            return {
                "document": get_document_library().reindex_document(
                    user_id=owner_id,
                    document_id=document_id,
                )
            }
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Document not found") from exc
        except DocumentLibraryUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            deps.logger.error("Document reindex failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/api/library/search")
    async def search_library(
        request: Request,
        q: str,
        limit: int = 5,
        user_id: Optional[str] = None,
    ):
        owner_id = _request_user_id(request, user_id)
        try:
            results = get_document_library().search(
                user_id=owner_id,
                query=q,
                limit=limit,
            )
            return {"query": q, "results": results}
        except DocumentLibraryUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            deps.logger.error("Document search failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/api/retrieval/search")
    async def retrieval_search_debug(request: Request, payload: RetrievalSearchRequest):
        owner_id = _request_user_id(request, payload.user_id)
        try:
            policy = build_retrieval_policy(payload.retrieval_policy, user_id=owner_id)
            return await retrieve_sources(
                payload.query,
                max_results=payload.max_results,
                config={"configurable": {"user_id": owner_id, "retrieval_policy": policy}},
            )
        except LegacySourceRoutingError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router
