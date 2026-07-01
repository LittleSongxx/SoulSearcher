from __future__ import annotations

import pytest


def test_rate_limiter_memory_limits_after_capacity():
    from common.rate_limiter import RateLimiter

    limiter = RateLimiter(general_per_minute=2, research_per_minute=1, window_seconds=60)

    assert limiter.check("u1").allowed is True
    assert limiter.check("u1").allowed is True
    result = limiter.check("u1")

    assert result.allowed is False
    assert result.retry_after > 0


def test_redis_rate_limiter_fail_open_uses_memory_fallback():
    from common.rate_limiter import RedisRateLimiter

    limiter = RedisRateLimiter(
        redis_url="",
        general_per_minute=1,
        window_seconds=60,
        fail_open=True,
    )

    assert limiter.check("u1").allowed is True
    assert limiter.check("u1").allowed is False
    assert "REDIS_URL" in limiter.backend_status["last_error"]


def test_idempotency_store_replays_and_rejects_payload_conflict(monkeypatch):
    from agent.runtime import idempotency as module

    store = module.IdempotencyStore()
    monkeypatch.setattr(store, "_database_url", lambda: "")

    first = store.begin(
        key="k1",
        scope="scope",
        user_id="u1",
        request_hash=module.canonical_request_hash({"a": 1}),
    )
    store.complete(first, response={"ok": True})

    replay = store.begin(
        key="k1",
        scope="scope",
        user_id="u1",
        request_hash=module.canonical_request_hash({"a": 1}),
    )

    assert replay.status == "completed"
    assert replay.response == {"ok": True}
    with pytest.raises(module.IdempotencyConflictError):
        store.begin(
            key="k1",
            scope="scope",
            user_id="u1",
            request_hash=module.canonical_request_hash({"a": 2}),
        )


def test_document_upload_dedupes_by_content_hash(monkeypatch, tmp_path):
    from agent.retrieval.documents import DocumentLibrary

    library = DocumentLibrary()
    monkeypatch.setattr(library, "database_url", "")
    monkeypatch.setattr(library, "root", tmp_path)

    rows: dict[str, dict] = {}
    chunks: dict[str, dict] = {}
    events: list[dict] = []

    class Cursor:
        description = None

        def __init__(self, row_factory=None):
            self.row_factory = row_factory
            self._rows = []
            self.rowcount = 0

        def execute(self, sql, params=None):
            params = params or {}
            if "CREATE " in sql:
                self._rows = []
                return
            if "FROM research_documents" in sql and "content_hash" in sql:
                matches = [
                    row
                    for row in rows.values()
                    if row["user_id"] == params["user_id"]
                    and row["content_hash"] == params["content_hash"]
                ]
                self._rows = matches[:1]
                return
            if "FROM research_documents" in sql and "id=%(id)s" in sql:
                row = rows.get(params["id"])
                self.description = [
                    ("document_id",),
                    ("user_id",),
                    ("filename",),
                    ("content_type",),
                    ("file_path",),
                    ("status",),
                    ("chunk_count",),
                ]
                if row and row["user_id"] == params["user_id"]:
                    self._rows = [
                        (
                            row["document_id"],
                            row["user_id"],
                            row["filename"],
                            row["content_type"],
                            row["file_path"],
                            row["status"],
                            row["chunk_count"],
                        )
                    ]
                else:
                    self._rows = []
                return
            if "INSERT INTO research_documents" in sql:
                rows[params["id"]] = {
                    "document_id": params["id"],
                    "user_id": params["user_id"],
                    "filename": params["filename"],
                    "content_type": params["content_type"],
                    "file_path": params["file_path"],
                    "content_hash": params["content_hash"],
                    "status": params["status"],
                    "chunk_count": params["chunk_count"],
                }
                self._rows = []
                return
            if "INSERT INTO research_document_chunks" in sql:
                chunks[params["id"]] = dict(params)
                return
            if "INSERT INTO research_document_events" in sql:
                events.append(dict(params))
                return

        def fetchone(self):
            return self._rows[0] if self._rows else None

        def fetchall(self):
            return list(self._rows)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Conn:
        def cursor(self, row_factory=None):
            return Cursor(row_factory=row_factory)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(library, "_connect", lambda: Conn())
    monkeypatch.setattr(library, "embed_text", lambda _text: [0.1] * library.embedding_dim)

    first = library.upload_document(
        user_id="u1",
        filename="a.txt",
        content_type="text/plain",
        data=b"hello world",
    )
    second = library.upload_document(
        user_id="u1",
        filename="a.txt",
        content_type="text/plain",
        data=b"hello world",
    )

    assert second["document_id"] == first["document_id"]
    assert len(rows) == 1
    assert len(events) == 1


def test_document_upload_cleans_partial_file_on_db_failure(monkeypatch, tmp_path):
    from agent.retrieval.documents import DocumentLibrary

    library = DocumentLibrary()
    monkeypatch.setattr(library, "database_url", "")
    monkeypatch.setattr(library, "root", tmp_path)
    monkeypatch.setattr(library, "embed_text", lambda _text: [0.1] * library.embedding_dim)

    class Cursor:
        description = None

        def __init__(self):
            self._select_count = 0

        def execute(self, sql, params=None):
            if "CREATE " in sql:
                return
            if "FROM research_documents" in sql:
                self._select_count += 1
                return
            if "INSERT INTO research_documents" in sql:
                raise RuntimeError("db insert failed")

        def fetchone(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Conn:
        def cursor(self, row_factory=None):
            return Cursor()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(library, "_connect", lambda: Conn())

    with pytest.raises(RuntimeError, match="db insert failed"):
        library.upload_document(
            user_id="u1",
            filename="a.txt",
            content_type="text/plain",
            data=b"hello world",
        )

    assert [path for path in tmp_path.rglob("*") if path.is_file()] == []
    assert [path for path in tmp_path.rglob("*") if path.name.startswith("doc_")] == []


@pytest.mark.asyncio
async def test_llm_reliability_retries_transient_async_failure(monkeypatch):
    from agent.core.llm_reliability import LLMReliabilityManager, LLMReliabilityPolicy

    manager = LLMReliabilityManager(
        LLMReliabilityPolicy(
            max_retries=1,
            initial_delay_seconds=0,
            max_delay_seconds=0,
            jitter_seconds=0,
            circuit_breaker_failures=5,
        )
    )
    monkeypatch.setattr("agent.core.llm_reliability.asyncio.sleep", lambda _delay: _noop())
    calls = {"count": 0}

    async def flaky():
        calls["count"] += 1
        if calls["count"] == 1:
            raise TimeoutError("timed out")
        return "ok"

    assert await manager.acall("openai", "model", flaky) == "ok"
    snapshot = manager.snapshot("openai")

    assert calls["count"] == 2
    assert snapshot["retry_count"] == 1
    assert snapshot["success_count"] == 1


def test_llm_reliability_opens_circuit_after_failures():
    from agent.core.llm_reliability import (
        LLMProviderCircuitOpen,
        LLMReliabilityManager,
        LLMReliabilityPolicy,
    )

    manager = LLMReliabilityManager(
        LLMReliabilityPolicy(
            max_retries=0,
            initial_delay_seconds=0,
            max_delay_seconds=0,
            jitter_seconds=0,
            circuit_breaker_failures=2,
            circuit_breaker_reset_seconds=60,
        )
    )

    def failing():
        raise TimeoutError("provider timeout")

    with pytest.raises(TimeoutError):
        manager.call("dashscope", "qwen", failing)
    with pytest.raises(TimeoutError):
        manager.call("dashscope", "qwen", failing)
    with pytest.raises(LLMProviderCircuitOpen):
        manager.call("dashscope", "qwen", lambda: "not called")

    snapshot = manager.snapshot("dashscope")
    assert snapshot["is_open"] is True
    assert snapshot["failure_count"] == 2
    assert snapshot["skipped_open_count"] == 1


def test_background_lease_store_allows_only_one_active_lease():
    from agent.runtime.background_leases import BackgroundLeaseStore

    store = BackgroundLeaseStore(redis_url="", ttl_seconds=60)
    first = store.acquire("thread-1")
    second = store.acquire("thread-1")

    assert first.acquired is True
    assert second.acquired is False
    assert store.refresh(first) is True
    assert store.release(first) is True
    assert store.acquire("thread-1").acquired is True


def test_background_lease_status_pings_configured_redis(monkeypatch):
    from agent.runtime.background_leases import BackgroundLeaseStore

    class Client:
        def __init__(self):
            self.ping_count = 0

        def ping(self):
            self.ping_count += 1
            return True

    client = Client()
    store = BackgroundLeaseStore(redis_url="redis://example")
    monkeypatch.setattr(store, "_get_client", lambda: client)

    status = store.status

    assert status["available"] is True
    assert status["backend"] == "redis"
    assert client.ping_count == 1


async def _noop():
    return None
