"""Leases for background research runs.

The in-process task map protects a single worker.  This module adds a Redis
lease option so multi-worker deployments can prevent duplicate background
execution for the same thread_id.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Lease:
    key: str
    token: str
    acquired: bool
    backend: str


class BackgroundLeaseStore:
    def __init__(
        self,
        *,
        redis_url: str = "",
        ttl_seconds: int = 1800,
        key_prefix: str = "soulsearcher:bglease",
    ) -> None:
        self.redis_url = str(redis_url or "").strip()
        self.ttl_seconds = max(60, int(ttl_seconds or 1800))
        self.key_prefix = key_prefix.strip(":") or "soulsearcher:bglease"
        self._client: Any | None = None
        self._memory: dict[str, tuple[str, float]] = {}
        self._last_error = ""

    @property
    def status(self) -> dict[str, Any]:
        return {
            "backend": "redis" if self.redis_url else "memory",
            "redis_url_configured": bool(self.redis_url),
            "available": bool(self.redis_url and self._client is not None and not self._last_error)
            or not self.redis_url,
            "ttl_seconds": self.ttl_seconds,
            "last_error": self._last_error,
            "memory_lease_count": len(self._memory),
        }

    def acquire(self, thread_id: str) -> Lease:
        key = self._key(thread_id)
        token = uuid.uuid4().hex
        if self.redis_url:
            try:
                client = self._get_client()
                acquired = bool(client.set(key, token, nx=True, ex=self.ttl_seconds))
                self._last_error = ""
                return Lease(key=key, token=token, acquired=acquired, backend="redis")
            except Exception as exc:
                self._last_error = str(exc)
                return Lease(
                    key=key,
                    token=token,
                    acquired=False,
                    backend="redis_unavailable",
                )
        self._evict_expired_memory()
        existing = self._memory.get(key)
        if existing and existing[1] > time.monotonic():
            return Lease(key=key, token=token, acquired=False, backend="memory")
        self._memory[key] = (token, time.monotonic() + self.ttl_seconds)
        return Lease(key=key, token=token, acquired=True, backend="memory")

    def refresh(self, lease: Lease) -> bool:
        if not lease.acquired:
            return False
        if lease.backend == "redis" and self.redis_url:
            try:
                client = self._get_client()
                value = client.get(lease.key)
                if value != lease.token:
                    return False
                client.expire(lease.key, self.ttl_seconds)
                self._last_error = ""
                return True
            except Exception as exc:
                self._last_error = str(exc)
                return False
        current = self._memory.get(lease.key)
        if not current or current[0] != lease.token:
            return False
        self._memory[lease.key] = (lease.token, time.monotonic() + self.ttl_seconds)
        return True

    def release(self, lease: Lease) -> bool:
        if not lease.acquired:
            return False
        if lease.backend == "redis" and self.redis_url:
            try:
                client = self._get_client()
                value = client.get(lease.key)
                if value == lease.token:
                    client.delete(lease.key)
                    self._last_error = ""
                    return True
                return False
            except Exception as exc:
                self._last_error = str(exc)
                return False
        current = self._memory.get(lease.key)
        if current and current[0] == lease.token:
            self._memory.pop(lease.key, None)
            return True
        return False

    def _get_client(self):
        if self._client is not None:
            return self._client
        import redis

        self._client = redis.Redis.from_url(
            self.redis_url,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
            decode_responses=True,
        )
        self._client.ping()
        return self._client

    def _key(self, thread_id: str) -> str:
        return f"{self.key_prefix}:{thread_id}"

    def _evict_expired_memory(self) -> None:
        now = time.monotonic()
        for key, (_, expires_at) in list(self._memory.items()):
            if expires_at <= now:
                self._memory.pop(key, None)
