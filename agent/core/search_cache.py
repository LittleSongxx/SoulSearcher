"""
Search Cache - LRU cache for search results with TTL support.

Prevents redundant API calls for similar/duplicate queries within a session.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """A cached search result entry."""

    query: str
    results: list[dict[str, Any]]
    timestamp: float
    hit_count: int = 0


class SearchCache:
    """
    Thread-safe LRU cache for search results.

    Features:
    - TTL-based expiration
    - Semantic similarity matching (finds similar queries)
    - LRU eviction when capacity reached
    - Thread-safe operations
    """

    def __init__(
        self,
        max_size: int = 100,
        ttl_seconds: float = 3600,  # 1 hour default
        similarity_threshold: float = 0.85,
    ):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self.similarity_threshold = similarity_threshold
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()

        # Stats
        self.hits = 0
        self.misses = 0
        self.similar_hits = 0
        self.sets = 0
        self.evictions = 0
        self.expired = 0

    def _normalize_query(self, query: str) -> str:
        """Normalize query for consistent caching."""
        return " ".join(query.lower().split())

    def _query_hash(self, query: str) -> str:
        """Generate hash for exact matching."""
        normalized = self._normalize_query(query)
        return hashlib.md5(normalized.encode()).hexdigest()[:16]

    def _is_expired(self, entry: CacheEntry) -> bool:
        """Check if entry has expired."""
        return (time.time() - entry.timestamp) > self.ttl_seconds

    def _find_similar(self, query: str) -> Optional[tuple[str, CacheEntry]]:
        """Find a similar query in cache using fuzzy matching."""
        normalized = self._normalize_query(query)

        for key, entry in self._cache.items():
            if self._is_expired(entry):
                continue

            cached_normalized = self._normalize_query(entry.query)
            similarity = SequenceMatcher(None, normalized, cached_normalized).ratio()

            if similarity >= self.similarity_threshold:
                return key, entry

        return None

    def get(self, query: str) -> Optional[list[dict[str, Any]]]:
        """
        Get cached results for a query.

        Checks exact match first, then similar queries.

        Returns:
            Cached results if found and not expired, None otherwise
        """
        with self._lock:
            query_hash = self._query_hash(query)

            # Try exact match first
            if query_hash in self._cache:
                entry = self._cache[query_hash]
                if not self._is_expired(entry):
                    # Move to end (most recently used)
                    self._cache.move_to_end(query_hash)
                    entry.hit_count += 1
                    self.hits += 1
                    logger.debug(f"[search_cache] Exact hit for: {query[:50]}")
                    return entry.results
                else:
                    # Remove expired entry
                    del self._cache[query_hash]
                    self.expired += 1

            # Try similar query match
            similar_match = self._find_similar(query)
            if similar_match:
                similar_key, similar_entry = similar_match
                self._cache.move_to_end(similar_key)
                similar_entry.hit_count += 1
                self.similar_hits += 1
                logger.debug(f"[search_cache] Similar hit for: {query[:50]}")
                return similar_entry.results

            self.misses += 1
            return None

    def set(self, query: str, results: list[dict[str, Any]]) -> None:
        """Cache search results for a query."""
        with self._lock:
            query_hash = self._query_hash(query)
            self.sets += 1

            if query_hash in self._cache:
                self._cache.move_to_end(query_hash)
                self._cache[query_hash] = CacheEntry(
                    query=query,
                    results=results,
                    timestamp=time.time(),
                )
                return

            # Evict oldest if at capacity
            while len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)
                self.evictions += 1

            self._cache[query_hash] = CacheEntry(
                query=query,
                results=results,
                timestamp=time.time(),
            )

    def clear(self) -> None:
        """Clear all cached entries."""
        with self._lock:
            self._cache.clear()
            self.hits = 0
            self.misses = 0
            self.similar_hits = 0
            self.sets = 0
            self.evictions = 0
            self.expired = 0

    def cleanup_expired(self) -> int:
        """Remove all expired entries. Returns count of removed entries."""
        with self._lock:
            expired_keys = [k for k, v in self._cache.items() if self._is_expired(v)]
            for k in expired_keys:
                del self._cache[k]
            self.expired += len(expired_keys)
            return len(expired_keys)

    def stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            total_requests = self.hits + self.similar_hits + self.misses
            hit_rate = (self.hits + self.similar_hits) / max(total_requests, 1)
            capacity_utilization = len(self._cache) / max(self.max_size, 1)

            return {
                "size": len(self._cache),
                "max_size": self.max_size,
                "hits": self.hits,
                "similar_hits": self.similar_hits,
                "misses": self.misses,
                "sets": self.sets,
                "evictions": self.evictions,
                "expired": self.expired,
                "total_requests": total_requests,
                "hit_rate": round(hit_rate, 3),
                "capacity_utilization": round(capacity_utilization, 3),
                "ttl_seconds": float(self.ttl_seconds),
                "similarity_threshold": float(self.similarity_threshold),
            }


# Global cache instance
_search_cache: Optional[SearchCache] = None


def get_search_cache() -> SearchCache:
    """Get the global search cache instance."""
    global _search_cache
    if _search_cache is None:
        try:
            from common.config import settings

            _search_cache = SearchCache(
                max_size=max(1, int(getattr(settings, "search_cache_max_size", 200))),
                ttl_seconds=max(
                    1.0, float(getattr(settings, "search_cache_ttl_seconds", 1800.0))
                ),
                similarity_threshold=min(
                    1.0,
                    max(0.0, float(getattr(settings, "search_cache_similarity_threshold", 0.9))),
                ),
            )
        except Exception:
            _search_cache = SearchCache()
    return _search_cache


def clear_search_cache() -> None:
    """Clear the global search cache."""
    global _search_cache
    if _search_cache:
        _search_cache.clear()


class QueryDeduplicator:
    """
    Deduplicates queries before execution.

    Prevents sending duplicate or near-duplicate queries to the search API.
    """

    def __init__(self, similarity_threshold: float = 0.85):
        self.similarity_threshold = similarity_threshold

    def _normalize(self, query: str) -> str:
        """Normalize query for comparison."""
        return " ".join(query.lower().split())

    def deduplicate(self, queries: list[str]) -> tuple[list[str], list[str]]:
        """
        Deduplicate a list of queries.

        Returns:
            (unique_queries, duplicate_queries)
        """
        if not queries:
            return [], []

        unique: list[str] = []
        duplicates: list[str] = []
        # Use dict for O(1) exact match lookup, list for similarity check
        seen_exact: set = set()
        seen_normalized: list[str] = []

        for query in queries:
            normalized = self._normalize(query)

            # Fast path: exact match (O(1))
            if normalized in seen_exact:
                duplicates.append(query)
                continue

            # Slow path: similarity check (only against unique queries)
            is_duplicate = False
            for seen in seen_normalized:
                similarity = SequenceMatcher(None, normalized, seen).ratio()
                if similarity >= self.similarity_threshold:
                    is_duplicate = True
                    break

            if is_duplicate:
                duplicates.append(query)
            else:
                unique.append(query)
                seen_exact.add(normalized)
                seen_normalized.append(normalized)

        if duplicates:
            logger.info(f"[query_dedup] Removed {len(duplicates)} duplicate queries")

        return unique, duplicates
