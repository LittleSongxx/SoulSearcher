"""
Tavily API key pool with automatic rotation on quota exhaustion.

Usage:
    from tools.search.tavily_key_pool import get_tavily_key_pool

    pool = get_tavily_key_pool()
    key = pool.get_key()          # Get the current active key
    pool.mark_exhausted(key)      # Mark a key as exhausted (auto-rotates)

Supports:
- TAVILY_API_KEYS (comma-separated) for multiple keys
- Falls back to TAVILY_API_KEY (single key) for backward compatibility
- Thread-safe rotation
- Logs rotation events for observability
"""

import logging
import threading
from typing import List, Optional

logger = logging.getLogger(__name__)

_QUOTA_ERROR_PATTERNS = (
    "rate limit",
    "quota",
    "exceeded",
    "429",
    "limit reached",
    "usage limit",
    "insufficient",
    "billing",
    "credits",
)


class TavilyKeyPool:
    """Thread-safe Tavily API key pool with automatic rotation."""

    def __init__(self, keys: List[str]):
        self._all_keys = [k.strip() for k in keys if k.strip()]
        self._lock = threading.Lock()
        self._current_index = 0
        self._exhausted: set = set()

        if not self._all_keys:
            logger.warning("[TavilyKeyPool] No API keys provided")
        else:
            masked = [self._mask(k) for k in self._all_keys]
            logger.info(f"[TavilyKeyPool] Initialized with {len(self._all_keys)} keys: {masked}")

    @staticmethod
    def _mask(key: str) -> str:
        """Mask a key for safe logging: show first 8 and last 4 chars."""
        if len(key) <= 16:
            return key[:4] + "..." + key[-4:] if len(key) > 8 else "***"
        return key[:8] + "..." + key[-4:]

    @property
    def available_count(self) -> int:
        """Number of keys not yet marked as exhausted."""
        with self._lock:
            return len(self._all_keys) - len(self._exhausted)

    def get_key(self) -> Optional[str]:
        """Get the current active key. Returns None if all keys are exhausted."""
        with self._lock:
            if not self._all_keys:
                return None
            # Find next non-exhausted key starting from current index
            for _ in range(len(self._all_keys)):
                key = self._all_keys[self._current_index]
                if key not in self._exhausted:
                    return key
                self._current_index = (self._current_index + 1) % len(self._all_keys)
            # All exhausted
            logger.error("[TavilyKeyPool] All API keys are exhausted!")
            return None

    def mark_exhausted(self, key: str) -> Optional[str]:
        """
        Mark a key as exhausted and rotate to the next available one.

        Returns the next available key, or None if all are exhausted.
        """
        with self._lock:
            if key in self._exhausted:
                # Already marked, just return current
                return self._get_next_available()

            self._exhausted.add(key)
            remaining = len(self._all_keys) - len(self._exhausted)
            logger.warning(
                f"[TavilyKeyPool] Key {self._mask(key)} marked as exhausted. "
                f"Remaining: {remaining}/{len(self._all_keys)}"
            )

            # Rotate to next
            self._current_index = (self._current_index + 1) % len(self._all_keys)
            next_key = self._get_next_available()
            if next_key:
                logger.info(f"[TavilyKeyPool] Rotated to key {self._mask(next_key)}")
            return next_key

    def _get_next_available(self) -> Optional[str]:
        """Find next available key (must be called with lock held)."""
        for _ in range(len(self._all_keys)):
            key = self._all_keys[self._current_index]
            if key not in self._exhausted:
                return key
            self._current_index = (self._current_index + 1) % len(self._all_keys)
        return None

    def reset(self) -> None:
        """Reset all keys to available (e.g., after quota period resets)."""
        with self._lock:
            self._exhausted.clear()
            self._current_index = 0
            logger.info("[TavilyKeyPool] All keys reset to available")

    @staticmethod
    def is_quota_error(error: Exception) -> bool:
        """Check if an exception looks like a quota/rate-limit error."""
        msg = str(error).lower()
        return any(pattern in msg for pattern in _QUOTA_ERROR_PATTERNS)


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------
_pool: Optional[TavilyKeyPool] = None
_pool_lock = threading.Lock()


def get_tavily_key_pool() -> TavilyKeyPool:
    """Get the global TavilyKeyPool singleton, initialized from settings."""
    global _pool
    if _pool is not None:
        return _pool

    with _pool_lock:
        if _pool is not None:
            return _pool

        from common.config import settings

        keys: List[str] = []

        # 1. Check TAVILY_API_KEYS (comma-separated, higher priority)
        multi_keys = getattr(settings, "tavily_api_keys", "") or ""
        if multi_keys.strip():
            keys = [k.strip() for k in multi_keys.split(",") if k.strip()]

        # 2. Fallback: use single TAVILY_API_KEY
        single_key = getattr(settings, "tavily_api_key", "") or ""
        if single_key.strip():
            if single_key.strip() not in keys:
                keys.insert(0, single_key.strip())

        _pool = TavilyKeyPool(keys)
        return _pool
