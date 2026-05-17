"""
Registry for active SSE streaming tasks.

Replaces the bare ``active_streams: dict[str, asyncio.Task]`` global in
``main.py`` with a small class that can be attached to ``app.state`` for
testability and future multi-worker awareness.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class StreamRegistry:
    """Track active SSE streaming tasks by thread ID."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}

    def register(self, thread_id: str, task: asyncio.Task) -> None:
        old = self._tasks.get(thread_id)
        if old is not None and not old.done():
            logger.debug("[stream_registry] Replacing existing task for %s", thread_id)
            old.cancel()
        self._tasks[thread_id] = task

    def cancel(self, thread_id: str, *, msg: str = "cancelled") -> bool:
        task = self._tasks.pop(thread_id, None)
        if task is None or task.done():
            return False
        task.cancel(msg=msg)
        return True

    def get(self, thread_id: str) -> Optional[asyncio.Task]:
        return self._tasks.get(thread_id)

    def cancel_all(self) -> int:
        count = 0
        for tid in list(self._tasks):
            if self.cancel(tid):
                count += 1
        return count

    @property
    def active_count(self) -> int:
        return sum(1 for t in self._tasks.values() if not t.done())

    def cleanup_done(self) -> int:
        done_keys = [k for k, t in self._tasks.items() if t.done()]
        for k in done_keys:
            del self._tasks[k]
        return len(done_keys)
