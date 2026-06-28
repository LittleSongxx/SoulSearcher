from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from common.config import settings

logger = logging.getLogger(__name__)


SUPPORTED_EXTENSIONS = {".txt", ".md", ".html", ".htm", ".json", ".csv", ".pdf", ".docx"}


class DocumentLibraryUnavailable(RuntimeError):
    pass


@dataclass
class DocumentUpload:
    document_id: str
    user_id: str
    filename: str
    content_type: str
    status: str
    chunk_count: int
    path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "user_id": self.user_id,
            "filename": self.filename,
            "content_type": self.content_type,
            "status": self.status,
            "chunk_count": self.chunk_count,
            "path": self.path,
        }


class DocumentLibrary:
    def __init__(self) -> None:
        self.database_url = (
            getattr(settings, "document_library_database_url", "")
            or getattr(settings, "memory_database_url", "")
            or getattr(settings, "database_url", "")
        )
        self.embedding_model = str(
            getattr(settings, "document_library_embedding_model", "")
            or getattr(settings, "memory_embedding_model", "text-embedding-3-small")
        )
        self.embedding_dim = int(
            getattr(settings, "document_library_embedding_dim", 0)
            or getattr(settings, "memory_embedding_dim", 1536)
        )
        self.chunk_chars = int(getattr(settings, "document_library_chunk_chars", 1500))
        self.chunk_overlap = int(getattr(settings, "document_library_chunk_overlap", 200))
        self.root = Path(getattr(settings, "document_library_path", "data/document_library")).resolve()
        self.pgvector_available = False
        self._setup_done = False

    @property
    def available(self) -> bool:
        return bool(self.database_url)

    def status(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "database_configured": bool(self.database_url),
            "pgvector_available": self.pgvector_available,
            "root": str(self.root),
            "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
        }

    def _connect(self):
        if not self.database_url:
            raise DocumentLibraryUnavailable(
                "Document library requires MEMORY_DATABASE_URL, DATABASE_URL, or DOCUMENT_LIBRARY_DATABASE_URL."
            )
        import psycopg

        conn = psycopg.connect(self.database_url, autocommit=True, connect_timeout=3)
        try:
            from pgvector.psycopg import register_vector

            register_vector(conn)
        except Exception as exc:
            logger.debug("[DocumentLibrary] pgvector adapter registration skipped: %s", exc)
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
                    logger.warning("[DocumentLibrary] pgvector unavailable: %s", exc)
                vector_type = (
                    f"vector({self.embedding_dim})" if self.pgvector_available else "jsonb"
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS research_documents (
                        id text PRIMARY KEY,
                        user_id text NOT NULL,
                        filename text NOT NULL,
                        content_type text NOT NULL DEFAULT '',
                        file_path text NOT NULL DEFAULT '',
                        content_hash text NOT NULL DEFAULT '',
                        status text NOT NULL DEFAULT 'indexed',
                        chunk_count integer NOT NULL DEFAULT 0,
                        metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                        created_at timestamptz NOT NULL DEFAULT now(),
                        updated_at timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS research_document_chunks (
                        id text PRIMARY KEY,
                        document_id text NOT NULL REFERENCES research_documents(id) ON DELETE CASCADE,
                        user_id text NOT NULL,
                        chunk_index integer NOT NULL,
                        text text NOT NULL,
                        embedding {vector_type},
                        content_hash text NOT NULL DEFAULT '',
                        metadata jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                        created_at timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS research_document_events (
                        id text PRIMARY KEY,
                        document_id text NOT NULL DEFAULT '',
                        user_id text NOT NULL DEFAULT '',
                        event_type text NOT NULL,
                        payload jsonb NOT NULL DEFAULT '{}'::jsonb,
                        created_at timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_research_documents_user ON research_documents(user_id)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_research_chunks_user ON research_document_chunks(user_id)"
                )
        self._setup_done = True

    def upload_document(
        self,
        *,
        user_id: str,
        filename: str,
        content_type: str = "",
        data: bytes,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.setup()
        user_id = _safe_user_id(user_id)
        ext = Path(filename or "").suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported document type '{ext}'. Supported: {sorted(SUPPORTED_EXTENSIONS)}")
        document_id = f"doc_{uuid.uuid4().hex}"
        doc_dir = self.root / user_id / document_id
        original_dir = doc_dir / "original"
        original_dir.mkdir(parents=True, exist_ok=True)
        safe_name = _safe_filename(filename)
        file_path = original_dir / safe_name
        file_path.write_bytes(data or b"")

        content_hash = hashlib.sha256(data or b"").hexdigest()
        text = parse_document_bytes(data or b"", filename=safe_name)
        chunks = chunk_text(text, chunk_chars=self.chunk_chars, overlap=self.chunk_overlap)
        embeddings = [self.embed_text(chunk) for chunk in chunks]
        now = datetime.now().isoformat(timespec="seconds")
        doc_metadata = dict(metadata or {})
        doc_metadata.update({"char_count": len(text), "indexed_at": now})

        with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO research_documents (
                        id, user_id, filename, content_type, file_path, content_hash,
                        status, chunk_count, metadata, updated_at
                    )
                    VALUES (%(id)s, %(user_id)s, %(filename)s, %(content_type)s,
                            %(file_path)s, %(content_hash)s, %(status)s,
                            %(chunk_count)s, %(metadata)s::jsonb, now())
                    """,
                    {
                        "id": document_id,
                        "user_id": user_id,
                        "filename": safe_name,
                        "content_type": content_type,
                        "file_path": str(file_path),
                        "content_hash": content_hash,
                        "status": "indexed",
                        "chunk_count": len(chunks),
                        "metadata": json.dumps(doc_metadata, ensure_ascii=False, default=str),
                    },
                )
                for idx, (chunk, embedding) in enumerate(zip(chunks, embeddings), 1):
                    chunk_id = f"chk_{hashlib.sha1(f'{document_id}:{idx}:{chunk[:80]}'.encode()).hexdigest()[:16]}"
                    cur.execute(
                        """
                        INSERT INTO research_document_chunks (
                            id, document_id, user_id, chunk_index, text, embedding,
                            content_hash, metadata
                        )
                        VALUES (%(id)s, %(document_id)s, %(user_id)s, %(chunk_index)s,
                                %(text)s, %(embedding)s, %(content_hash)s, %(metadata)s::jsonb)
                        """,
                        {
                            "id": chunk_id,
                            "document_id": document_id,
                            "user_id": user_id,
                            "chunk_index": idx,
                            "text": chunk,
                            "embedding": embedding if self.pgvector_available else json.dumps(embedding),
                            "content_hash": hashlib.sha1(chunk.encode("utf-8")).hexdigest(),
                            "metadata": json.dumps({"filename": safe_name}, ensure_ascii=False),
                        },
                    )
                self._record_event(
                    cur,
                    user_id=user_id,
                    document_id=document_id,
                    event_type="indexed",
                    payload={"chunk_count": len(chunks), "filename": safe_name},
                )
        return DocumentUpload(
            document_id=document_id,
            user_id=user_id,
            filename=safe_name,
            content_type=content_type,
            status="indexed",
            chunk_count=len(chunks),
            path=str(file_path),
        ).to_dict()

    def list_documents(self, *, user_id: str) -> list[dict[str, Any]]:
        self.setup()
        with self._connect() as conn, conn.cursor(row_factory=_dict_row()) as cur:
                cur.execute(
                    """
                    SELECT id AS document_id, user_id, filename, content_type, file_path,
                           content_hash, status, chunk_count, metadata,
                           created_at::text, updated_at::text
                    FROM research_documents
                    WHERE user_id = %(user_id)s
                    ORDER BY created_at DESC
                    """,
                    {"user_id": _safe_user_id(user_id)},
                )
                return [dict(row) for row in cur.fetchall()]

    def delete_document(self, *, user_id: str, document_id: str) -> bool:
        self.setup()
        with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM research_documents WHERE user_id=%(user_id)s AND id=%(id)s",
                    {"user_id": _safe_user_id(user_id), "id": document_id},
                )
                deleted = int(getattr(cur, "rowcount", 0) or 0) > 0
                if deleted:
                    self._record_event(
                        cur,
                        user_id=_safe_user_id(user_id),
                        document_id=document_id,
                        event_type="deleted",
                        payload={},
                    )
        return deleted

    def reindex_document(self, *, user_id: str, document_id: str) -> dict[str, Any]:
        self.setup()
        docs = [doc for doc in self.list_documents(user_id=user_id) if doc.get("document_id") == document_id]
        if not docs:
            raise FileNotFoundError(document_id)
        doc = docs[0]
        data = Path(str(doc.get("file_path") or "")).read_bytes()
        text = parse_document_bytes(data, filename=str(doc.get("filename") or "document.txt"))
        chunks = chunk_text(text, chunk_chars=self.chunk_chars, overlap=self.chunk_overlap)
        embeddings = [self.embed_text(chunk) for chunk in chunks]
        with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM research_document_chunks WHERE user_id=%(user_id)s AND document_id=%(document_id)s",
                    {"user_id": _safe_user_id(user_id), "document_id": document_id},
                )
                for idx, (chunk, embedding) in enumerate(zip(chunks, embeddings), 1):
                    chunk_id = f"chk_{hashlib.sha1(f'{document_id}:{idx}:{chunk[:80]}'.encode()).hexdigest()[:16]}"
                    cur.execute(
                        """
                        INSERT INTO research_document_chunks (
                            id, document_id, user_id, chunk_index, text, embedding,
                            content_hash, metadata
                        )
                        VALUES (%(id)s, %(document_id)s, %(user_id)s, %(chunk_index)s,
                                %(text)s, %(embedding)s, %(content_hash)s, %(metadata)s::jsonb)
                        """,
                        {
                            "id": chunk_id,
                            "document_id": document_id,
                            "user_id": _safe_user_id(user_id),
                            "chunk_index": idx,
                            "text": chunk,
                            "embedding": embedding if self.pgvector_available else json.dumps(embedding),
                            "content_hash": hashlib.sha1(chunk.encode("utf-8")).hexdigest(),
                            "metadata": json.dumps({"filename": doc.get("filename")}, ensure_ascii=False),
                        },
                    )
                cur.execute(
                    """
                    UPDATE research_documents
                    SET chunk_count=%(chunk_count)s, status='indexed', updated_at=now()
                    WHERE user_id=%(user_id)s AND id=%(document_id)s
                    """,
                    {
                        "chunk_count": len(chunks),
                        "user_id": _safe_user_id(user_id),
                        "document_id": document_id,
                    },
                )
                self._record_event(
                    cur,
                    user_id=_safe_user_id(user_id),
                    document_id=document_id,
                    event_type="reindexed",
                    payload={"chunk_count": len(chunks)},
                )
        return {"document_id": document_id, "status": "indexed", "chunk_count": len(chunks)}

    def search(
        self,
        *,
        user_id: str,
        query: str,
        limit: int = 5,
        document_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        self.setup()
        user_id = _safe_user_id(user_id)
        query = str(query or "").strip()
        if not query:
            return []
        query_embedding = self.embed_text(query)
        params: dict[str, Any] = {
            "user_id": user_id,
            "limit": max(1, int(limit or 5)),
        }
        document_filter = ""
        clean_document_ids = [str(item).strip() for item in (document_ids or []) if str(item).strip()]
        if clean_document_ids:
            document_filter = "AND c.document_id = ANY(%(document_ids)s)"
            params["document_ids"] = clean_document_ids
        if self.pgvector_available and query_embedding:
            params["embedding"] = query_embedding
            sql = f"""
                SELECT c.id AS chunk_id, c.document_id, c.user_id, c.chunk_index,
                       c.text, c.content_hash, c.metadata,
                       d.filename, d.content_type, d.file_path,
                       1.0 / (1.0 + (c.embedding <-> %(embedding)s::vector)) AS vector_score
                FROM research_document_chunks c
                JOIN research_documents d ON d.id = c.document_id
                WHERE c.user_id = %(user_id)s {document_filter}
                ORDER BY c.embedding <-> %(embedding)s::vector ASC
                LIMIT %(limit)s
            """
        else:
            sql = f"""
                SELECT c.id AS chunk_id, c.document_id, c.user_id, c.chunk_index,
                       c.text, c.content_hash, c.metadata,
                       d.filename, d.content_type, d.file_path,
                       0.0 AS vector_score
                FROM research_document_chunks c
                JOIN research_documents d ON d.id = c.document_id
                WHERE c.user_id = %(user_id)s {document_filter}
                ORDER BY c.created_at DESC
                LIMIT %(limit)s
            """
        with self._connect() as conn, conn.cursor(row_factory=_dict_row()) as cur:
                cur.execute(sql, params)
                rows = [dict(row) for row in cur.fetchall()]
        ranked = []
        for row in rows:
            keyword = _keyword_score(query, str(row.get("text") or ""))
            vector = float(row.get("vector_score") or 0.0)
            score = round((vector * 0.7) + (keyword * 0.3), 4)
            ranked.append(
                {
                    "document_id": row.get("document_id"),
                    "chunk_id": row.get("chunk_id"),
                    "user_id": row.get("user_id"),
                    "chunk_index": row.get("chunk_index"),
                    "title": row.get("filename"),
                    "content": row.get("text"),
                    "content_hash": row.get("content_hash"),
                    "retrieval_score": score,
                    "metadata": row.get("metadata") or {},
                    "source_origin": "private_corpus",
                    "access_channel": "file_upload",
                    "retrieval_method": "vector_search" if self.pgvector_available else "keyword_search",
                }
            )
        ranked.sort(key=lambda item: float(item.get("retrieval_score") or 0.0), reverse=True)
        return ranked[: max(1, int(limit or 5))]

    def get_chunk(self, *, user_id: str, chunk_id: str) -> dict[str, Any] | None:
        self.setup()
        with self._connect() as conn, conn.cursor(row_factory=_dict_row()) as cur:
                cur.execute(
                    """
                    SELECT c.id AS chunk_id, c.document_id, c.user_id, c.chunk_index,
                           c.text, c.content_hash, c.metadata,
                           d.filename, d.content_type, d.file_path
                    FROM research_document_chunks c
                    JOIN research_documents d ON d.id = c.document_id
                    WHERE c.user_id = %(user_id)s AND c.id = %(chunk_id)s
                    """,
                    {"user_id": _safe_user_id(user_id), "chunk_id": chunk_id},
                )
                row = cur.fetchone()
        return dict(row) if row else None

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
            digest = hashlib.sha256(value.encode("utf-8")).digest()
            dims = max(1, self.embedding_dim)
            return [((digest[i % len(digest)] / 255.0) * 2.0) - 1.0 for i in range(dims)]

    def _record_event(
        self,
        cur: Any,
        *,
        user_id: str,
        document_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        cur.execute(
            """
            INSERT INTO research_document_events (id, document_id, user_id, event_type, payload)
            VALUES (%(id)s, %(document_id)s, %(user_id)s, %(event_type)s, %(payload)s::jsonb)
            """,
            {
                "id": f"doc_evt_{uuid.uuid4().hex}",
                "document_id": document_id,
                "user_id": user_id,
                "event_type": event_type,
                "payload": json.dumps(payload, ensure_ascii=False, default=str),
            },
        )


def parse_document_bytes(data: bytes, *, filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext in {".txt", ".md", ".html", ".htm", ".json"}:
        text = data.decode("utf-8", errors="ignore")
        if ext == ".json":
            try:
                return json.dumps(json.loads(text), ensure_ascii=False, indent=2)
            except Exception:
                return text
        if ext in {".html", ".htm"}:
            return re.sub(r"<[^>]+>", " ", text)
        return text
    if ext == ".csv":
        text = data.decode("utf-8", errors="ignore")
        rows = csv.reader(io.StringIO(text))
        return "\n".join(" | ".join(cell.strip() for cell in row) for row in rows)
    if ext == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            return "\n\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as exc:
            raise ValueError(f"Failed to parse PDF. Install pypdf or provide a valid PDF: {exc}") from exc
    if ext == ".docx":
        try:
            from docx import Document

            doc = Document(io.BytesIO(data))
            return "\n".join(paragraph.text for paragraph in doc.paragraphs)
        except Exception as exc:
            raise ValueError(f"Failed to parse DOCX: {exc}") from exc
    raise ValueError(f"Unsupported document type '{ext}'")


def chunk_text(text: str, *, chunk_chars: int = 1500, overlap: int = 200) -> list[str]:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return []
    chunk_chars = max(300, int(chunk_chars or 1500))
    overlap = max(0, min(int(overlap or 0), chunk_chars // 2))
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + chunk_chars)
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start = max(end - overlap, start + 1)
    return chunks


def get_document_library() -> DocumentLibrary:
    global _DOCUMENT_LIBRARY
    try:
        return _DOCUMENT_LIBRARY
    except NameError:
        _DOCUMENT_LIBRARY = DocumentLibrary()
        return _DOCUMENT_LIBRARY


def _dict_row():
    from psycopg.rows import dict_row

    return dict_row


def _safe_user_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.@-]+", "-", str(value or "default_user")).strip("-")
    return cleaned or "default_user"


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_. -]+", "-", str(value or "document.txt")).strip()
    return cleaned[:180] or "document.txt"


def _keyword_score(query: str, text: str) -> float:
    q_tokens = {token for token in re.split(r"\W+", query.lower()) if len(token) > 1}
    if not q_tokens:
        return 0.0
    t_tokens = {token for token in re.split(r"\W+", text.lower()) if len(token) > 1}
    if not t_tokens:
        return 0.0
    return len(q_tokens & t_tokens) / max(1, len(q_tokens))
