from __future__ import annotations

import hashlib

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from agent.api.models import (
    DocumentDeleteResponse,
    DocumentListResponse,
    DocumentSearchResponse,
    DocumentUploadResponse,
)

router = APIRouter(prefix="/api/documents")


def _rag_collection_for_request(request: Request) -> str:
    """
    Resolve the Chroma collection name for RAG documents.

    Hybrid behavior:
    - Default/dev (internal auth disabled): single shared collection
    - Enterprise internal (internal auth enabled): per-principal isolated collection
    """
    from common.config import settings

    base = (
        getattr(settings, "rag_collection_name", "") or "weaver_documents"
    ).strip() or "weaver_documents"
    internal_key = (getattr(settings, "internal_api_key", "") or "").strip()
    if not internal_key:
        return base

    principal_id = (
        getattr(request.state, "principal_id", "") or ""
    ).strip() or "internal"
    suffix = hashlib.sha256(principal_id.encode("utf-8")).hexdigest()[:12]
    return f"{base}__u_{suffix}"


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(request: Request, file: UploadFile = File(...)):
    """
    Upload a document to the RAG knowledge base.

    Supports PDF, DOCX, TXT, MD files.
    """
    from common.config import settings

    if not settings.rag_enabled:
        raise HTTPException(
            status_code=400,
            detail="RAG is not enabled. Set rag_enabled=True in settings.",
        )

    # Validate file size (max 50MB)
    MAX_FILE_SIZE = 50 * 1024 * 1024
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)}MB.",
        )

    # Validate file extension
    ALLOWED_EXTENSIONS = {"pdf", "docx", "doc", "txt", "md", "csv"}
    filename = file.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '.{ext}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    try:
        from tools.rag.rag_tool import get_rag_tool

        rag = get_rag_tool(collection_name=_rag_collection_for_request(request))
        if rag is None:
            raise HTTPException(status_code=500, detail="Failed to initialize RAG tool")

        result = rag.add_document(content=content, filename=file.filename)

        if not result.get("success"):
            raise HTTPException(
                status_code=400, detail=result.get("error", "Upload failed")
            )

        return {
            "success": True,
            "filename": file.filename,
            "chunks": result.get("chunks", 0),
            "message": f"Document '{file.filename}' uploaded successfully with {result.get('chunks', 0)} chunks",
        }

    except HTTPException:
        raise
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Document upload error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list", response_model=DocumentListResponse)
async def list_documents(request: Request, limit: int = 100):
    """
    List all documents in the RAG knowledge base.
    """
    from common.config import settings

    if not settings.rag_enabled:
        raise HTTPException(status_code=400, detail="RAG is not enabled.")

    try:
        from tools.rag.rag_tool import get_rag_tool

        rag = get_rag_tool(collection_name=_rag_collection_for_request(request))
        if rag is None:
            raise HTTPException(status_code=500, detail="Failed to initialize RAG tool")

        documents = rag.list_documents(limit=limit)
        count = rag.count()

        return {
            "total_chunks": count,
            "documents": documents,
        }

    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"List documents error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{source:path}", response_model=DocumentDeleteResponse)
async def delete_document(source: str, request: Request):
    """
    Delete a document from the RAG knowledge base by source path.
    """
    from common.config import settings

    if not settings.rag_enabled:
        raise HTTPException(status_code=400, detail="RAG is not enabled.")

    try:
        from tools.rag.rag_tool import get_rag_tool

        rag = get_rag_tool(collection_name=_rag_collection_for_request(request))
        if rag is None:
            raise HTTPException(status_code=500, detail="Failed to initialize RAG tool")

        result = rag.delete_document(source)
        if not result.get("success"):
            raise HTTPException(
                status_code=400, detail=result.get("error", "Delete failed")
            )

        return {"success": True, "message": f"Document '{source}' deleted"}

    except HTTPException:
        raise
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Delete document error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/search", response_model=DocumentSearchResponse)
async def search_documents(request: Request, query: str, n_results: int = 5):
    """
    Search the RAG knowledge base.
    """
    from common.config import settings

    if not settings.rag_enabled:
        raise HTTPException(status_code=400, detail="RAG is not enabled.")

    try:
        from tools.rag.rag_tool import get_rag_tool

        rag = get_rag_tool(collection_name=_rag_collection_for_request(request))
        if rag is None:
            raise HTTPException(status_code=500, detail="Failed to initialize RAG tool")

        results = rag.search(query, n_results=n_results)

        return {
            "query": query,
            "results": results,
        }

    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Search documents error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
