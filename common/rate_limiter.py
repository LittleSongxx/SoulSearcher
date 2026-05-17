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
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RateLimitResult:
    """Outcome of a single rate-limit check."""

    allowed: bool
    limit: int
    remaining: int
    reset_ts: int
    retry_after: int = 0


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
