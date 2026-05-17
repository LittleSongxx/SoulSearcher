"""Memory update queue with debounce mechanism."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from common.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ConversationContext:
    thread_id: str
    messages: list[Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    agent_name: str | None = None
    user_id: str | None = None
    correction_detected: bool = False
    reinforcement_detected: bool = False


class MemoryUpdateQueue:
    """Debounced queue for memory updates.

    Collects conversations and processes them after a configurable
    debounce period. Deduplicates by (thread_id, user_id, agent_name).
    """

    def __init__(self):
        self._queue: list[ConversationContext] = []
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._processing = False

    @staticmethod
    def _queue_key(thread_id: str, user_id: str | None, agent_name: str | None) -> tuple:
        return (thread_id, user_id, agent_name)

    def add(
        self,
        thread_id: str,
        messages: list[Any],
        agent_name: str | None = None,
        user_id: str | None = None,
        correction_detected: bool = False,
        reinforcement_detected: bool = False,
    ) -> None:
        """Add a conversation to the update queue."""
        config = getattr(settings, "memory_enabled", True)
        if not config:
            return

        with self._lock:
            self._enqueue_locked(
                thread_id=thread_id,
                messages=messages,
                agent_name=agent_name,
                user_id=user_id,
                correction_detected=correction_detected,
                reinforcement_detected=reinforcement_detected,
            )
            self._reset_timer()

        logger.debug("Memory queue: added thread=%s, size=%d", thread_id, len(self._queue))

    def _enqueue_locked(self, **kwargs: Any) -> None:
        queue_key = self._queue_key(
            kwargs["thread_id"], kwargs.get("user_id"), kwargs.get("agent_name")
        )
        existing = next(
            (c for c in self._queue if self._queue_key(c.thread_id, c.user_id, c.agent_name) == queue_key),
            None,
        )
        merged_correction = kwargs.get("correction_detected", False) or (
            existing.correction_detected if existing else False
        )
        merged_reinforcement = kwargs.get("reinforcement_detected", False) or (
            existing.reinforcement_detected if existing else False
        )
        context = ConversationContext(
            thread_id=kwargs["thread_id"],
            messages=kwargs["messages"],
            agent_name=kwargs.get("agent_name"),
            user_id=kwargs.get("user_id"),
            correction_detected=merged_correction,
            reinforcement_detected=merged_reinforcement,
        )
        # Deduplicate: replace existing entry for same key
        self._queue = [c for c in self._queue if self._queue_key(c.thread_id, c.user_id, c.agent_name) != queue_key]
        self._queue.append(context)

    def _reset_timer(self) -> None:
        debounce = getattr(settings, "memory_debounce_seconds", 30)
        self._schedule_timer(debounce)

    def _schedule_timer(self, delay: float) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(delay, self._process_queue)
        self._timer.daemon = True
        self._timer.start()

    def _process_queue(self) -> None:
        from agent.runtime.memory.updater import MemoryUpdater

        with self._lock:
            if self._processing:
                self._schedule_timer(0)
                return
            if not self._queue:
                return
            self._processing = True
            contexts = self._queue.copy()
            self._queue.clear()
            self._timer = None

        logger.info("Processing %d queued memory updates", len(contexts))
        try:
            updater = MemoryUpdater()
            for ctx in contexts:
                try:
                    updater.update_memory(
                        messages=ctx.messages,
                        thread_id=ctx.thread_id,
                        agent_name=ctx.agent_name,
                        correction_detected=ctx.correction_detected,
                        reinforcement_detected=ctx.reinforcement_detected,
                        user_id=ctx.user_id,
                    )
                except Exception:
                    logger.exception("Memory update failed for thread %s", ctx.thread_id)
                if len(contexts) > 1:
                    time.sleep(0.5)
        finally:
            with self._lock:
                self._processing = False

    def flush(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        self._process_queue()

    def clear(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._queue.clear()
            self._processing = False

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._queue)


_memory_queue: MemoryUpdateQueue | None = None
_queue_lock = threading.Lock()


def get_memory_queue() -> MemoryUpdateQueue:
    global _memory_queue
    with _queue_lock:
        if _memory_queue is None:
            _memory_queue = MemoryUpdateQueue()
        return _memory_queue


def reset_memory_queue() -> None:
    global _memory_queue
    with _queue_lock:
        if _memory_queue is not None:
            _memory_queue.clear()
        _memory_queue = None
