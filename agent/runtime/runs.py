"""Lightweight run lifecycle registry for Weaver research flows."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from common.config import settings


class RunStatus(str, Enum):
    queued = "queued"
    pending = "pending"
    running = "running"
    paused = "paused"
    waiting_for_input = "waiting_for_input"
    resumed = "resumed"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


@dataclass
class RunRecord:
    run_id: str
    thread_id: str
    model: str = ""
    route: str = ""
    user_id: str = ""
    status: RunStatus = RunStatus.pending
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    ended_at: str = ""
    error: str = ""
    token_summary: dict[str, Any] = field(default_factory=dict)
    quality_summary: dict[str, Any] = field(default_factory=dict)
    workspace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunRecord":
        status = str(data.get("status") or RunStatus.pending.value)
        return cls(
            run_id=str(data.get("run_id") or data.get("thread_id") or ""),
            thread_id=str(data.get("thread_id") or data.get("run_id") or ""),
            model=str(data.get("model") or ""),
            route=str(data.get("route") or ""),
            user_id=str(data.get("user_id") or ""),
            status=RunStatus(status) if status in RunStatus._value2member_map_ else RunStatus.pending,
            created_at=str(data.get("created_at") or datetime.now(UTC).isoformat()),
            updated_at=str(data.get("updated_at") or datetime.now(UTC).isoformat()),
            ended_at=str(data.get("ended_at") or ""),
            error=str(data.get("error") or ""),
            token_summary=dict(data.get("token_summary") or {}),
            quality_summary=dict(data.get("quality_summary") or {}),
            workspace=dict(data.get("workspace") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


class RunManager:
    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}
        self._by_thread: dict[str, str] = {}
        self._db_ready = False
        self._db_error = ""
        self._backend = "memory"

    def _database_url(self) -> str:
        return (
            str(getattr(settings, "database_url", "") or "").strip()
            or str(getattr(settings, "memory_database_url", "") or "").strip()
        )

    def _init_db(self) -> None:
        if self._db_ready:
            return
        database_url = self._database_url()
        if not database_url:
            self._backend = "memory"
            return
        try:
            import psycopg
        except Exception as exc:
            self._db_error = str(exc)
            self._backend = "memory"
            return

        try:
            with psycopg.connect(
                database_url,
                autocommit=True,
                connect_timeout=3,
            ) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS weaver_run_records (
                            run_id text PRIMARY KEY,
                            thread_id text NOT NULL,
                            model text NOT NULL DEFAULT '',
                            route text NOT NULL DEFAULT '',
                            user_id text NOT NULL DEFAULT '',
                            status text NOT NULL DEFAULT 'pending',
                            created_at timestamptz NOT NULL DEFAULT now(),
                            updated_at timestamptz NOT NULL DEFAULT now(),
                            ended_at timestamptz,
                            error text NOT NULL DEFAULT '',
                            token_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
                            quality_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
                            workspace jsonb NOT NULL DEFAULT '{}'::jsonb,
                            metadata jsonb NOT NULL DEFAULT '{}'::jsonb
                        )
                        """
                    )
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_weaver_run_thread_id ON weaver_run_records(thread_id)"
                    )
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_weaver_run_status ON weaver_run_records(status)"
                    )
            self._backend = "postgres"
            self._db_ready = True
        except Exception as exc:
            self._db_error = str(exc)
            self._backend = "memory"

    def _connect(self):
        import psycopg

        return psycopg.connect(
            self._database_url(),
            autocommit=True,
            connect_timeout=3,
        )

    def _record_from_row(self, row: dict[str, Any]) -> RunRecord:
        payload = dict(row)
        for key in ("token_summary", "quality_summary", "workspace", "metadata"):
            value = payload.get(key)
            if isinstance(value, str):
                try:
                    import json

                    payload[key] = json.loads(value)
                except Exception:
                    payload[key] = {}
        return RunRecord.from_dict(payload)

    def _upsert_db(self, record: RunRecord) -> None:
        if self._backend != "postgres":
            return
        import json

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO weaver_run_records (
                        run_id, thread_id, model, route, user_id, status,
                        created_at, updated_at, ended_at, error, token_summary,
                        quality_summary, workspace, metadata
                    ) VALUES (
                        %(run_id)s, %(thread_id)s, %(model)s, %(route)s, %(user_id)s,
                        %(status)s, %(created_at)s, %(updated_at)s, %(ended_at)s,
                        %(error)s, %(token_summary)s::jsonb, %(quality_summary)s::jsonb,
                        %(workspace)s::jsonb, %(metadata)s::jsonb
                    )
                    ON CONFLICT (run_id) DO UPDATE SET
                        thread_id = EXCLUDED.thread_id,
                        model = EXCLUDED.model,
                        route = EXCLUDED.route,
                        user_id = EXCLUDED.user_id,
                        status = EXCLUDED.status,
                        updated_at = EXCLUDED.updated_at,
                        ended_at = EXCLUDED.ended_at,
                        error = EXCLUDED.error,
                        token_summary = EXCLUDED.token_summary,
                        quality_summary = EXCLUDED.quality_summary,
                        workspace = EXCLUDED.workspace,
                        metadata = EXCLUDED.metadata
                    """,
                    {
                        **record.to_dict(),
                        "token_summary": json.dumps(record.token_summary, ensure_ascii=False, default=str),
                        "quality_summary": json.dumps(record.quality_summary, ensure_ascii=False, default=str),
                        "workspace": json.dumps(record.workspace, ensure_ascii=False, default=str),
                        "metadata": json.dumps(record.metadata, ensure_ascii=False, default=str),
                    },
                )

    def _fetch_db(self, run_id_or_thread_id: str) -> RunRecord | None:
        if self._backend != "postgres":
            return None
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT *
                        FROM weaver_run_records
                        WHERE run_id = %s OR thread_id = %s
                        ORDER BY updated_at DESC
                        LIMIT 1
                        """,
                        (run_id_or_thread_id, run_id_or_thread_id),
                    )
                    row = cur.fetchone()
                    if not row:
                        return None
                    columns = [desc[0] for desc in cur.description or []]
                    payload = dict(zip(columns, row, strict=False))
                    return self._record_from_row(payload)
        except Exception as exc:
            self._db_error = str(exc)
            self._backend = "memory"
        return None

    def start(
        self,
        *,
        run_id: str,
        thread_id: str,
        model: str = "",
        route: str = "",
        user_id: str = "",
        workspace: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RunRecord:
        self._init_db()
        record = RunRecord(
            run_id=run_id,
            thread_id=thread_id,
            model=model,
            route=route,
            user_id=user_id,
            status=RunStatus.running,
            workspace=workspace or {},
            metadata=metadata or {},
        )
        self._runs[run_id] = record
        self._by_thread[thread_id] = run_id
        self._upsert_db(record)
        return record

    def get(self, run_id_or_thread_id: str) -> RunRecord | None:
        self._init_db()
        record = self._runs.get(run_id_or_thread_id)
        if record is not None:
            return record
        mapped = self._runs.get(self._by_thread.get(run_id_or_thread_id, ""))
        if mapped is not None:
            return mapped
        db_record = self._fetch_db(run_id_or_thread_id)
        if db_record is not None:
            self._runs[db_record.run_id] = db_record
            self._by_thread[db_record.thread_id] = db_record.run_id
        return db_record

    def finish(
        self,
        run_id_or_thread_id: str,
        *,
        status: RunStatus,
        error: str = "",
        token_summary: dict[str, Any] | None = None,
        quality_summary: dict[str, Any] | None = None,
        workspace: dict[str, Any] | None = None,
    ) -> RunRecord | None:
        record = self.get(run_id_or_thread_id)
        if record is None:
            return None
        record.status = status
        record.error = error
        record.updated_at = datetime.now(UTC).isoformat()
        record.ended_at = record.updated_at
        if token_summary is not None:
            record.token_summary = token_summary
        if quality_summary is not None:
            record.quality_summary = quality_summary
        if workspace is not None:
            record.workspace = workspace
        self._runs[record.run_id] = record
        self._by_thread[record.thread_id] = record.run_id
        self._init_db()
        self._upsert_db(record)
        return record

    def update(
        self,
        run_id_or_thread_id: str,
        *,
        status: RunStatus | None = None,
        error: str | None = None,
        token_summary: dict[str, Any] | None = None,
        quality_summary: dict[str, Any] | None = None,
        workspace: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RunRecord | None:
        record = self.get(run_id_or_thread_id)
        if record is None:
            return None
        if status is not None:
            record.status = status
        if error is not None:
            record.error = error
        if token_summary is not None:
            record.token_summary = token_summary
        if quality_summary is not None:
            record.quality_summary = quality_summary
        if workspace is not None:
            record.workspace = workspace
        if metadata is not None:
            merged = dict(record.metadata or {})
            merged.update(metadata)
            record.metadata = merged
        record.updated_at = datetime.now(UTC).isoformat()
        self._runs[record.run_id] = record
        self._by_thread[record.thread_id] = record.run_id
        self._init_db()
        self._upsert_db(record)
        return record

    def all(self) -> list[dict[str, Any]]:
        self._init_db()
        if self._backend == "postgres":
            try:
                with self._connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT * FROM weaver_run_records ORDER BY updated_at DESC LIMIT 500"
                        )
                        columns = [desc[0] for desc in cur.description or []]
                        rows = cur.fetchall()
                        return [
                            self._record_from_row(dict(zip(columns, row, strict=False))).to_dict()
                            for row in rows
                        ]
            except Exception as exc:
                self._db_error = str(exc)
                self._backend = "memory"
        return [record.to_dict() for record in self._runs.values()]


run_manager = RunManager()
