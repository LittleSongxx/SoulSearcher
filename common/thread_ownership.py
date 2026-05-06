"""
Thread ownership registry.

This is a lightweight best-effort guard for internal single-tenant deployments.
When internal API auth is enabled, we can optionally bind thread_ids to a
trusted user identity (e.g. injected by a reverse proxy) and prevent cross-user
access to sensitive thread-scoped endpoints.

Note: This registry is in-memory and process-local. For multi-worker deployments,
use a shared checkpointer/store and enforce access via persisted session state.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class _ThreadOwner:
    owner_id: str
    created_at: float


_LOCK = threading.Lock()
_THREAD_OWNERS: dict[str, _ThreadOwner] = {}

_DEFAULT_TTL_S = 60 * 60 * 24  # 24h
_PRUNE_SIZE = 5000


def set_thread_owner(thread_id: str, owner_id: str) -> None:
    thread_id = (thread_id or "").strip()
    owner_id = (owner_id or "").strip()
    if not thread_id or not owner_id:
        return

    now = time.time()
    with _LOCK:
        _THREAD_OWNERS[thread_id] = _ThreadOwner(owner_id=owner_id, created_at=now)
        if len(_THREAD_OWNERS) > _PRUNE_SIZE:
            _prune_locked(now=now, ttl_s=_DEFAULT_TTL_S)


def get_thread_owner(thread_id: str, *, ttl_s: float = _DEFAULT_TTL_S) -> Optional[str]:
    thread_id = (thread_id or "").strip()
    if not thread_id:
        return None

    now = time.time()
    with _LOCK:
        owner = _THREAD_OWNERS.get(thread_id)
        if not owner:
            return None
        if ttl_s and now - owner.created_at > ttl_s:
            _THREAD_OWNERS.pop(thread_id, None)
            return None
        return owner.owner_id


def _prune_locked(*, now: float, ttl_s: float) -> None:
    if ttl_s <= 0:
        return
    expired = [tid for tid, o in _THREAD_OWNERS.items() if now - o.created_at > ttl_s]
    for tid in expired:
        _THREAD_OWNERS.pop(tid, None)

