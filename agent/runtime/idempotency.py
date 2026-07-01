"""Idempotency-key support for mutation endpoints.

The store is deliberately small: endpoints own their business transaction, while
this module records request fingerprints and completed responses so client
retries after timeouts can safely return the original result.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from common.config import settings


class IdempotencyConflictError(RuntimeError):
    """Raised when the same key is reused with a different request payload."""


@dataclass
class IdempotencyRecord:
    key: str
    scope: str
    user_id: str
    request_hash: str
    status: str = "in_progress"
    response: dict[str, Any] | None = None
    http_status: int = 200
    created_at: str = ""
    expires_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "scope": self.scope,
            "user_id": self.user_id,
            "request_hash": self.request_hash,
            "status": self.status,
            "response": self.response or {},
            "http_status": self.http_status,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }


def canonical_request_hash(payload: Any) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class IdempotencyStore:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str], IdempotencyRecord] = {}
        self._db_ready = False
        self._backend = "memory"
        self._db_error = ""

    def _database_url(self) -> str:
        return (
            str(getattr(settings, "database_url", "") or "").strip()
            or str(getattr(settings, "memory_database_url", "") or "").strip()
        )

    def _ttl_seconds(self) -> int:
        return max(60, int(getattr(settings, "idempotency_ttl_seconds", 86400) or 86400))

    def _init_db(self) -> None:
        if self._db_ready:
            return
        database_url = self._database_url()
        if not database_url:
            self._backend = "memory"
            return
        try:
            import psycopg

            with psycopg.connect(database_url, autocommit=True, connect_timeout=3) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS soulsearcher_idempotency_keys (
                            key text NOT NULL,
                            scope text NOT NULL,
                            user_id text NOT NULL,
                            request_hash text NOT NULL,
                            status text NOT NULL DEFAULT 'in_progress',
                            response jsonb NOT NULL DEFAULT '{}'::jsonb,
                            http_status integer NOT NULL DEFAULT 200,
                            created_at timestamptz NOT NULL DEFAULT now(),
                            updated_at timestamptz NOT NULL DEFAULT now(),
                            expires_at timestamptz NOT NULL,
                            PRIMARY KEY (key, scope, user_id)
                        )
                        """
                    )
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_soulsearcher_idempotency_expiry "
                        "ON soulsearcher_idempotency_keys(expires_at)"
                    )
            self._backend = "postgres"
            self._db_ready = True
        except Exception as exc:
            self._db_error = str(exc)
            self._backend = "memory"

    def begin(
        self,
        *,
        key: str,
        scope: str,
        user_id: str,
        request_hash: str,
    ) -> IdempotencyRecord | None:
        clean_key = _clean(key)
        if not clean_key or not bool(getattr(settings, "idempotency_enabled", True)):
            return None
        clean_scope = _clean(scope)
        clean_user = _clean(user_id) or "anonymous"
        self._init_db()
        if self._backend == "postgres":
            return self._begin_db(
                key=clean_key,
                scope=clean_scope,
                user_id=clean_user,
                request_hash=request_hash,
            )
        return self._begin_memory(
            key=clean_key,
            scope=clean_scope,
            user_id=clean_user,
            request_hash=request_hash,
        )

    def complete(
        self,
        record: IdempotencyRecord | None,
        *,
        response: dict[str, Any],
        http_status: int = 200,
    ) -> None:
        if record is None:
            return
        record.status = "completed"
        record.response = dict(response or {})
        record.http_status = int(http_status or 200)
        if self._backend == "postgres":
            self._complete_db(record)
        else:
            self._records[(record.key, record.scope, record.user_id)] = record

    @property
    def status(self) -> dict[str, Any]:
        return {
            "backend": self._backend,
            "db_ready": self._db_ready,
            "db_error": self._db_error,
            "memory_records": len(self._records),
        }

    def _begin_memory(
        self,
        *,
        key: str,
        scope: str,
        user_id: str,
        request_hash: str,
    ) -> IdempotencyRecord:
        self._prune_memory()
        identity = (key, scope, user_id)
        existing = self._records.get(identity)
        if existing:
            if existing.request_hash != request_hash:
                raise IdempotencyConflictError(
                    "Idempotency-Key was already used with a different request payload."
                )
            return existing
        now = datetime.now(UTC)
        record = IdempotencyRecord(
            key=key,
            scope=scope,
            user_id=user_id,
            request_hash=request_hash,
            created_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=self._ttl_seconds())).isoformat(),
        )
        self._records[identity] = record
        return record

    def _begin_db(
        self,
        *,
        key: str,
        scope: str,
        user_id: str,
        request_hash: str,
    ) -> IdempotencyRecord:
        import psycopg
        from psycopg.rows import dict_row

        expires_at = datetime.now(UTC) + timedelta(seconds=self._ttl_seconds())
        with psycopg.connect(self._database_url(), autocommit=True, connect_timeout=3) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    DELETE FROM soulsearcher_idempotency_keys
                    WHERE expires_at < now()
                    """
                )
                cur.execute(
                    """
                    INSERT INTO soulsearcher_idempotency_keys (
                        key, scope, user_id, request_hash, expires_at
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (key, scope, user_id) DO NOTHING
                    RETURNING key, scope, user_id, request_hash, status, response,
                              http_status, created_at::text, expires_at::text
                    """,
                    (key, scope, user_id, request_hash, expires_at),
                )
                row = cur.fetchone()
                if row is None:
                    cur.execute(
                        """
                        SELECT key, scope, user_id, request_hash, status, response,
                               http_status, created_at::text, expires_at::text
                        FROM soulsearcher_idempotency_keys
                        WHERE key=%s AND scope=%s AND user_id=%s
                        """,
                        (key, scope, user_id),
                    )
                    row = cur.fetchone()
                if not row:
                    raise RuntimeError("Failed to load idempotency record")
                if str(row["request_hash"]) != request_hash:
                    raise IdempotencyConflictError(
                        "Idempotency-Key was already used with a different request payload."
                    )
                return IdempotencyRecord(
                    key=str(row["key"]),
                    scope=str(row["scope"]),
                    user_id=str(row["user_id"]),
                    request_hash=str(row["request_hash"]),
                    status=str(row["status"]),
                    response=dict(row["response"] or {}),
                    http_status=int(row["http_status"] or 200),
                    created_at=str(row["created_at"] or ""),
                    expires_at=str(row["expires_at"] or ""),
                )

    def _complete_db(self, record: IdempotencyRecord) -> None:
        import psycopg

        with psycopg.connect(self._database_url(), autocommit=True, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE soulsearcher_idempotency_keys
                    SET status='completed',
                        response=%(response)s::jsonb,
                        http_status=%(http_status)s,
                        updated_at=now()
                    WHERE key=%(key)s AND scope=%(scope)s AND user_id=%(user_id)s
                    """,
                    {
                        "key": record.key,
                        "scope": record.scope,
                        "user_id": record.user_id,
                        "response": json.dumps(record.response or {}, ensure_ascii=False, default=str),
                        "http_status": int(record.http_status or 200),
                    },
                )

    def _prune_memory(self) -> None:
        now = time.time()
        for identity, record in list(self._records.items()):
            try:
                expires_ts = datetime.fromisoformat(record.expires_at).timestamp()
            except Exception:
                expires_ts = now + self._ttl_seconds()
            if expires_ts < now:
                self._records.pop(identity, None)


def _clean(value: str) -> str:
    return str(value or "").strip()


idempotency_store = IdempotencyStore()
