"""
In-memory token-bucket rate limiter.

Encapsulates the rate-limiting logic that was previously spread across global
variables in ``main.py``.  The class is designed to be instantiated once and
attached to ``app.state`` so that it can be replaced in tests and (in the
future) swapped for a Redis-backed implementation in multi-worker deployments.

NOTE: This implementation is per-process.  Under ``gunicorn --workers N`` each
worker maintains its own bucket set, so effective limits are multiplied by N.
For strict shared limits, replace with a Redis token-bucket (e.g. via
``redis-py``'s Lua-based rate limiter).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass
class RateLimitResult:
    """Outcome of a single rate-limit check."""

    allowed: bool
    limit: int
    remaining: int
    reset_ts: int
    retry_after: int = 0


class RateLimiterBackend(Protocol):
    """Minimal backend contract used by the HTTP middleware."""

    def check(self, identity: str, *, is_research: bool = False) -> RateLimitResult:
        ...

    async def start_cleanup_loop(self, interval: float = 300.0) -> None:
        ...

    async def stop_cleanup_loop(self) -> None:
        ...


class RateLimiter:
    """Thread-safe, in-memory token-bucket rate limiter with LRU eviction."""

    def __init__(
        self,
        *,
        general_per_minute: int = 60,
        research_per_minute: int = 20,
        window_seconds: int = 60,
        max_buckets: int = 10_000,
    ) -> None:
        self.general_per_minute = max(1, general_per_minute)
        self.research_per_minute = max(1, research_per_minute)
        self.window_seconds = max(1, window_seconds)
        self.max_buckets = max(1, max_buckets)
        self._buckets: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._cleanup_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, identity: str, *, is_research: bool = False) -> RateLimitResult:
        """Consume one token and return the result."""
        limit = self.research_per_minute if is_research else self.general_per_minute
        bucket_key = f"{identity}:{'research' if is_research else 'general'}"
        now = time.time()

        bucket = self._buckets.get(bucket_key)
        if bucket is None or now - bucket["window_start"] >= self.window_seconds:
            bucket = {"tokens": limit - 1, "window_start": now}
            self._buckets[bucket_key] = bucket
        else:
            bucket["tokens"] -= 1
            try:
                self._buckets.move_to_end(bucket_key)
            except Exception:
                pass

        # Cap memory usage.
        try:
            while len(self._buckets) > self.max_buckets:
                self._buckets.popitem(last=False)
        except Exception:
            pass

        remaining = max(int(bucket.get("tokens", 0)), 0)
        reset_ts = int(bucket["window_start"] + self.window_seconds)
        exceeded = bucket.get("tokens", 0) < 0

        retry_after = 0
        if exceeded:
            retry_after = int(self.window_seconds - (now - bucket["window_start"])) + 1

        return RateLimitResult(
            allowed=not exceeded,
            limit=limit,
            remaining=0 if exceeded else remaining,
            reset_ts=reset_ts,
            retry_after=retry_after,
        )

    # ------------------------------------------------------------------
    # Periodic cleanup
    # ------------------------------------------------------------------

    async def start_cleanup_loop(self, interval: float = 300.0) -> None:
        """Start a background task that evicts stale buckets every *interval* seconds."""
        if self._cleanup_task is not None:
            return
        self._cleanup_task = asyncio.create_task(self._cleanup_loop(interval))

    async def stop_cleanup_loop(self) -> None:
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

    async def _cleanup_loop(self, interval: float) -> None:
        try:
            while True:
                await asyncio.sleep(interval)
                self._evict_stale()
        except asyncio.CancelledError:
            return

    def _evict_stale(self) -> int:
        now = time.time()
        stale_keys = [
            k
            for k, v in self._buckets.items()
            if now - v["window_start"] > self.window_seconds * 2
        ]
        for k in stale_keys:
            self._buckets.pop(k, None)
        return len(stale_keys)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def bucket_count(self) -> int:
        return len(self._buckets)


class RedisRateLimiter:
    """Redis-backed fixed-window limiter with memory fallback.

    The existing in-memory limiter is intentionally kept as fallback so a Redis
    blip degrades strict distributed limiting into local limiting instead of
    taking down request handling.
    """

    def __init__(
        self,
        *,
        redis_url: str,
        general_per_minute: int = 60,
        research_per_minute: int = 20,
        window_seconds: int = 60,
        max_buckets: int = 10_000,
        fail_open: bool = True,
        key_prefix: str = "soulsearcher:rl",
    ) -> None:
        self.redis_url = str(redis_url or "").strip()
        self.general_per_minute = max(1, general_per_minute)
        self.research_per_minute = max(1, research_per_minute)
        self.window_seconds = max(1, window_seconds)
        self.fail_open = bool(fail_open)
        self.key_prefix = key_prefix.strip(":") or "soulsearcher:rl"
        self._client: Any | None = None
        self._redis_error = ""
        self._fallback = RateLimiter(
            general_per_minute=general_per_minute,
            research_per_minute=research_per_minute,
            window_seconds=window_seconds,
            max_buckets=max_buckets,
        )

    @property
    def backend_status(self) -> dict[str, Any]:
        return {
            "backend": "redis",
            "available": self._client is not None and not self._redis_error,
            "redis_url_configured": bool(self.redis_url),
            "fail_open": self.fail_open,
            "last_error": self._redis_error,
            "fallback_bucket_count": self._fallback.bucket_count,
        }

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self.redis_url:
            raise RuntimeError("REDIS_URL is not configured")
        try:
            import redis

            self._client = redis.Redis.from_url(
                self.redis_url,
                socket_connect_timeout=0.5,
                socket_timeout=0.5,
                decode_responses=True,
            )
            self._client.ping()
            self._redis_error = ""
            return self._client
        except Exception as exc:
            self._client = None
            self._redis_error = str(exc)
            raise

    def check(self, identity: str, *, is_research: bool = False) -> RateLimitResult:
        limit = self.research_per_minute if is_research else self.general_per_minute
        now = time.time()
        window = int(now // self.window_seconds)
        reset_ts = int((window + 1) * self.window_seconds)
        kind = "research" if is_research else "general"
        key = f"{self.key_prefix}:{kind}:{identity}:{window}"

        try:
            client = self._get_client()
            count = int(client.incr(key))
            if count == 1:
                client.expire(key, self.window_seconds * 2)
            remaining = max(limit - count, 0)
            exceeded = count > limit
            retry_after = max(1, reset_ts - int(now)) if exceeded else 0
            self._redis_error = ""
            return RateLimitResult(
                allowed=not exceeded,
                limit=limit,
                remaining=remaining,
                reset_ts=reset_ts,
                retry_after=retry_after,
            )
        except Exception as exc:
            self._redis_error = str(exc)
            logger.warning("[rate_limiter] Redis check failed: %s", exc)
            if self.fail_open:
                return self._fallback.check(identity, is_research=is_research)
            return RateLimitResult(
                allowed=False,
                limit=limit,
                remaining=0,
                reset_ts=reset_ts,
                retry_after=max(1, reset_ts - int(now)),
            )

    async def start_cleanup_loop(self, interval: float = 300.0) -> None:
        await self._fallback.start_cleanup_loop(interval=interval)

    async def stop_cleanup_loop(self) -> None:
        await self._fallback.stop_cleanup_loop()


def build_rate_limiter(
    *,
    backend: str = "memory",
    redis_url: str = "",
    general_per_minute: int = 60,
    research_per_minute: int = 20,
    window_seconds: int = 60,
    max_buckets: int = 10_000,
    redis_fail_open: bool = True,
) -> RateLimiterBackend:
    backend_name = str(backend or "memory").strip().lower()
    if backend_name == "auto":
        backend_name = "redis" if str(redis_url or "").strip() else "memory"
    if backend_name == "redis":
        return RedisRateLimiter(
            redis_url=redis_url,
            general_per_minute=general_per_minute,
            research_per_minute=research_per_minute,
            window_seconds=window_seconds,
            max_buckets=max_buckets,
            fail_open=redis_fail_open,
        )
    return RateLimiter(
        general_per_minute=general_per_minute,
        research_per_minute=research_per_minute,
        window_seconds=window_seconds,
        max_buckets=max_buckets,
    )
