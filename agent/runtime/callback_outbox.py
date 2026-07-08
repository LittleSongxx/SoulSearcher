"""Durable callback outbox for long-running A2A task notifications."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from agent.runtime.idempotency import canonical_request_hash


@dataclass
class CallbackMessage:
    callback_id: str
    url: str
    payload: dict[str, Any]
    headers: dict[str, str]
    status: str = "pending"
    attempt_count: int = 0
    max_attempts: int = 3
    next_attempt_at: str = ""
    last_error: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class CallbackOutbox:
    def __init__(self) -> None:
        self._memory: dict[str, CallbackMessage] = {}
        self._db_ready = False
        self._backend = "memory"
        self._db_error = ""

    @property
    def status(self) -> dict[str, Any]:
        return {
            "backend": self._backend,
            "db_ready": self._db_ready,
            "db_error": self._db_error,
            "memory_pending": len([item for item in self._memory.values() if item.status in {"pending", "retrying"}]),
        }

    def enqueue(
        self,
        settings: Any,
        *,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        max_attempts: int,
    ) -> CallbackMessage:
        callback_id = _callback_id(payload)
        self._init_db(settings)
        message = CallbackMessage(
            callback_id=callback_id,
            url=url,
            payload=dict(payload or {}),
            headers=dict(headers or {}),
            max_attempts=max(1, int(max_attempts or 1)),
        )
        if self._backend == "postgres":
            existing = self._enqueue_db(settings, message)
            return existing or message
        existing = self._memory.get(callback_id)
        if existing is not None:
            return existing
        self._memory[callback_id] = message
        return message

    async def dispatch_ready(self, settings: Any, *, limit: int = 20) -> dict[str, int]:
        self._init_db(settings)
        messages = self._ready_db(settings, limit=limit) if self._backend == "postgres" else self._ready_memory(limit=limit)
        delivered = 0
        failed = 0
        dead_letter = 0
        for message in messages:
            ok, error = await _post_callback(message)
            if ok:
                delivered += 1
                self._mark_delivered(settings, message)
                continue
            failed += 1
            message.attempt_count += 1
            message.last_error = error
            if message.attempt_count >= message.max_attempts:
                dead_letter += 1
                message.status = "dead_letter"
            else:
                message.status = "retrying"
                message.next_attempt_at = (datetime.now(UTC) + timedelta(seconds=_retry_delay(message.attempt_count))).isoformat()
            self._mark_failed(settings, message)
        return {"scanned": len(messages), "delivered": delivered, "failed": failed, "dead_letter": dead_letter}

    def _database_url(self, settings: Any) -> str:
        return (
            str(getattr(settings, "database_url", "") or "").strip()
            or str(getattr(settings, "memory_database_url", "") or "").strip()
        )

    def _init_db(self, settings: Any) -> None:
        if self._db_ready:
            return
        database_url = self._database_url(settings)
        if not database_url:
            self._backend = "memory"
            return
        try:
            import psycopg

            with (
                psycopg.connect(database_url, autocommit=True, connect_timeout=3) as conn,
                conn.cursor() as cur,
            ):
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS soulsearcher_a2a_callback_outbox (
                        callback_id text PRIMARY KEY,
                        url text NOT NULL,
                        payload jsonb NOT NULL DEFAULT '{}'::jsonb,
                        headers jsonb NOT NULL DEFAULT '{}'::jsonb,
                        status text NOT NULL DEFAULT 'pending',
                        attempt_count integer NOT NULL DEFAULT 0,
                        max_attempts integer NOT NULL DEFAULT 3,
                        next_attempt_at timestamptz,
                        last_error text NOT NULL DEFAULT '',
                        created_at timestamptz NOT NULL DEFAULT now(),
                        updated_at timestamptz NOT NULL DEFAULT now(),
                        delivered_at timestamptz
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_soulsearcher_a2a_callback_status_next "
                    "ON soulsearcher_a2a_callback_outbox(status, next_attempt_at)"
                )
            self._backend = "postgres"
            self._db_ready = True
        except Exception as exc:
            self._db_error = str(exc)
            self._backend = "memory"

    def _enqueue_db(self, settings: Any, message: CallbackMessage) -> CallbackMessage | None:
        import psycopg
        from psycopg.rows import dict_row

        with (
            psycopg.connect(self._database_url(settings), autocommit=True, connect_timeout=3) as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            cur.execute(
                """
                INSERT INTO soulsearcher_a2a_callback_outbox (
                    callback_id, url, payload, headers, max_attempts
                ) VALUES (%s, %s, %s::jsonb, %s::jsonb, %s)
                ON CONFLICT (callback_id) DO NOTHING
                RETURNING *
                """,
                (
                    message.callback_id,
                    message.url,
                    json.dumps(message.payload, ensure_ascii=False, default=str),
                    json.dumps(message.headers, ensure_ascii=False, default=str),
                    message.max_attempts,
                ),
            )
            row = cur.fetchone()
            if row:
                return _message_from_row(row)
            cur.execute(
                "SELECT * FROM soulsearcher_a2a_callback_outbox WHERE callback_id=%s",
                (message.callback_id,),
            )
            row = cur.fetchone()
            return _message_from_row(row) if row else None

    def _ready_db(self, settings: Any, *, limit: int) -> list[CallbackMessage]:
        import psycopg
        from psycopg.rows import dict_row

        with (
            psycopg.connect(self._database_url(settings), autocommit=True, connect_timeout=3) as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            cur.execute(
                """
                SELECT *
                FROM soulsearcher_a2a_callback_outbox
                WHERE status IN ('pending', 'retrying')
                  AND (next_attempt_at IS NULL OR next_attempt_at <= now())
                ORDER BY created_at ASC
                LIMIT %s
                """,
                (max(1, min(int(limit or 20), 200)),),
            )
            return [_message_from_row(row) for row in cur.fetchall()]

    def _ready_memory(self, *, limit: int) -> list[CallbackMessage]:
        now = time.time()
        ready: list[CallbackMessage] = []
        for message in self._memory.values():
            if message.status not in {"pending", "retrying"}:
                continue
            if message.next_attempt_at:
                try:
                    if datetime.fromisoformat(message.next_attempt_at).timestamp() > now:
                        continue
                except Exception:
                    pass
            ready.append(message)
            if len(ready) >= max(1, min(int(limit or 20), 200)):
                break
        return ready

    def _mark_delivered(self, settings: Any, message: CallbackMessage) -> None:
        message.status = "delivered"
        if self._backend != "postgres":
            self._memory[message.callback_id] = message
            return
        import psycopg

        with (
            psycopg.connect(self._database_url(settings), autocommit=True, connect_timeout=3) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(
                """
                UPDATE soulsearcher_a2a_callback_outbox
                SET status='delivered', updated_at=now(), delivered_at=now(), last_error=''
                WHERE callback_id=%s
                """,
                (message.callback_id,),
            )

    def _mark_failed(self, settings: Any, message: CallbackMessage) -> None:
        if self._backend != "postgres":
            self._memory[message.callback_id] = message
            return
        import psycopg

        with (
            psycopg.connect(self._database_url(settings), autocommit=True, connect_timeout=3) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(
                """
                UPDATE soulsearcher_a2a_callback_outbox
                SET status=%(status)s,
                    attempt_count=%(attempt_count)s,
                    next_attempt_at=%(next_attempt_at)s,
                    last_error=%(last_error)s,
                    updated_at=now()
                WHERE callback_id=%(callback_id)s
                """,
                {
                    "callback_id": message.callback_id,
                    "status": message.status,
                    "attempt_count": message.attempt_count,
                    "next_attempt_at": message.next_attempt_at or None,
                    "last_error": message.last_error,
                },
            )

async def _post_callback(message: CallbackMessage) -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.post(message.url, json=message.payload, headers=message.headers)
        if 200 <= response.status_code < 300:
            return True, ""
        return False, f"HTTP {response.status_code}: {response.text[:500]}"
    except Exception as exc:
        return False, str(exc)


async def deliver_callback_once(
    *,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    attempts: int,
) -> tuple[bool, str]:
    message = CallbackMessage(
        callback_id=_callback_id(payload),
        url=url,
        payload=dict(payload or {}),
        headers=dict(headers or {}),
        max_attempts=max(1, int(attempts or 1)),
    )
    last_error = ""
    for attempt in range(message.max_attempts):
        ok, last_error = await _post_callback(message)
        if ok:
            return True, ""
        if attempt < message.max_attempts - 1:
            await asyncio.sleep(min(2.0, _retry_delay(attempt + 1)))
    return False, last_error


def _callback_id(payload: dict[str, Any]) -> str:
    explicit = (
        payload.get("callback_id")
        or payload.get("event_id")
        or payload.get("eventId")
    )
    if explicit:
        return str(explicit)
    return "cb_" + canonical_request_hash(
        {
            "event": payload.get("event") or "",
            "task_id": payload.get("task_id") or "",
            "context_id": payload.get("context_id") or "",
            "status": payload.get("status") or "",
            "artifact": payload.get("artifact") or {},
            "hitl": payload.get("hitl") or {},
            "error": payload.get("error") or {},
            "metadata": payload.get("metadata") or {},
        }
    )[:32]


def _message_from_row(row: dict[str, Any]) -> CallbackMessage:
    payload = row.get("payload") or {}
    headers = row.get("headers") or {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    if isinstance(headers, str):
        headers = json.loads(headers)
    return CallbackMessage(
        callback_id=str(row.get("callback_id") or ""),
        url=str(row.get("url") or ""),
        payload=dict(payload or {}),
        headers={str(k): str(v) for k, v in dict(headers or {}).items()},
        status=str(row.get("status") or "pending"),
        attempt_count=int(row.get("attempt_count") or 0),
        max_attempts=int(row.get("max_attempts") or 3),
        next_attempt_at=str(row.get("next_attempt_at") or ""),
        last_error=str(row.get("last_error") or ""),
        created_at=str(row.get("created_at") or ""),
    )


def _retry_delay(attempt_count: int) -> float:
    return min(60.0, max(1.0, 0.5 * (2 ** max(0, int(attempt_count or 1) - 1))))


callback_outbox = CallbackOutbox()
