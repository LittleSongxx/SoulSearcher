"""
Reliability helpers for search providers.

Provides retries with exponential backoff and a simple per-provider
circuit breaker to avoid hammering failing backends.
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ReliabilityPolicy:
    """Runtime policy for retries and circuit breaker behavior."""

    max_retries: int = 2
    retry_backoff_seconds: float = 0.5
    circuit_breaker_failures: int = 3
    circuit_breaker_reset_seconds: float = 60.0


@dataclass
class _ProviderReliabilityState:
    consecutive_failures: int = 0
    opened_at: Optional[float] = None
    total_calls: int = 0
    attempted_calls: int = 0
    success_count: int = 0
    failure_count: int = 0
    skipped_open_count: int = 0
    retry_count: int = 0
    circuit_open_count: int = 0
    last_failure: Optional[str] = None
    last_failure_at: Optional[float] = None


class ProviderReliabilityManager:
    """
    Wrap provider calls with retry and circuit-breaker safeguards.

    `call()` never raises provider errors; it returns `[]` on failure so callers
    can keep fallback behavior deterministic.
    """

    def __init__(self, policy: Optional[ReliabilityPolicy] = None):
        self.policy = policy or ReliabilityPolicy()
        self._states: dict[str, _ProviderReliabilityState] = {}
        self._lock = threading.Lock()

    def _state(self, provider_name: str) -> _ProviderReliabilityState:
        with self._lock:
            if provider_name not in self._states:
                self._states[provider_name] = _ProviderReliabilityState()
            return self._states[provider_name]

    def _record_success(self, provider_name: str) -> None:
        state = self._state(provider_name)
        with self._lock:
            state.success_count += 1
            state.consecutive_failures = 0
            state.opened_at = None

    def _record_failure(self, provider_name: str, error: Exception) -> None:
        state = self._state(provider_name)
        with self._lock:
            state.failure_count += 1
            state.consecutive_failures += 1
            state.last_failure = str(error)
            state.last_failure_at = time.monotonic()
            threshold = max(1, int(self.policy.circuit_breaker_failures))
            if state.consecutive_failures >= threshold and state.opened_at is None:
                state.opened_at = time.monotonic()
                state.circuit_open_count += 1

    def _reset_if_expired(self, provider_name: str) -> None:
        state = self._state(provider_name)
        with self._lock:
            if state.opened_at is None:
                return
            reset_after = max(0.0, float(self.policy.circuit_breaker_reset_seconds))
            if reset_after == 0.0 or (time.monotonic() - state.opened_at) >= reset_after:
                state.opened_at = None
                state.consecutive_failures = 0

    def is_open(self, provider_name: str) -> bool:
        """Return whether provider circuit is currently open."""
        self._reset_if_expired(provider_name)
        state = self._state(provider_name)
        with self._lock:
            return state.opened_at is not None

    def call(self, provider_name: str, fn: Callable[[], Any]) -> Any:
        """
        Execute provider call with retry/backoff and circuit breaker.

        Returns `[]` on failure/circuit-open to match search fallback flow.
        """
        state = self._state(provider_name)
        with self._lock:
            state.total_calls += 1

        if self.is_open(provider_name):
            with self._lock:
                state.skipped_open_count += 1
            logger.warning(f"[reliability] circuit open for provider={provider_name}, skip call")
            return []

        attempts = max(1, int(self.policy.max_retries) + 1)
        for attempt in range(attempts):
            try:
                with self._lock:
                    state.attempted_calls += 1
                result = fn()
                self._record_success(provider_name)
                return result
            except Exception as e:
                self._record_failure(provider_name, e)
                is_last_attempt = attempt >= (attempts - 1)

                if self.is_open(provider_name):
                    logger.warning(
                        f"[reliability] opened circuit for provider={provider_name}: {e}"
                    )
                    return []

                if is_last_attempt:
                    logger.warning(
                        f"[reliability] provider={provider_name} failed after "
                        f"{attempts} attempts: {e}"
                    )
                    return []

                with self._lock:
                    state.retry_count += 1
                base = max(0.0, float(self.policy.retry_backoff_seconds))
                delay = base * (2**attempt)
                if delay > 0:
                    time.sleep(delay)

        return []

    def snapshot(self, provider_name: str) -> dict[str, Any]:
        """
        Return a JSON-serializable view of the current reliability state for a provider.

        Notes:
        - Timestamps are expressed as durations (seconds) because `opened_at` is
          based on `time.monotonic()` and has no stable epoch meaning.
        - This method is safe to call from API endpoints for observability.
        """
        name = str(provider_name or "").strip()
        if not name:
            return {
                "provider": "",
                "is_open": False,
                "consecutive_failures": 0,
                "opened_for_seconds": None,
                "resets_in_seconds": None,
                "total_calls": 0,
                "attempted_calls": 0,
                "success_count": 0,
                "failure_count": 0,
                "skipped_open_count": 0,
                "retry_count": 0,
                "circuit_open_count": 0,
                "last_failure": None,
                "last_failure_age_seconds": None,
                "policy": {
                    "max_retries": int(self.policy.max_retries),
                    "retry_backoff_seconds": float(self.policy.retry_backoff_seconds),
                    "circuit_breaker_failures": int(self.policy.circuit_breaker_failures),
                    "circuit_breaker_reset_seconds": float(self.policy.circuit_breaker_reset_seconds),
                },
            }

        self._reset_if_expired(name)
        state = self._state(name)
        with self._lock:
            consecutive_failures = int(state.consecutive_failures)
            opened_at = state.opened_at
            total_calls = int(state.total_calls)
            attempted_calls = int(state.attempted_calls)
            success_count = int(state.success_count)
            failure_count = int(state.failure_count)
            skipped_open_count = int(state.skipped_open_count)
            retry_count = int(state.retry_count)
            circuit_open_count = int(state.circuit_open_count)
            last_failure = state.last_failure
            last_failure_at = state.last_failure_at

        is_open = opened_at is not None
        opened_for_seconds = None
        resets_in_seconds = None

        if opened_at is not None:
            opened_for_seconds = max(0.0, time.monotonic() - float(opened_at))
            reset_after = max(0.0, float(self.policy.circuit_breaker_reset_seconds))
            if reset_after == 0.0:
                resets_in_seconds = 0.0
            else:
                resets_in_seconds = max(0.0, reset_after - opened_for_seconds)

        last_failure_age_seconds = None
        if last_failure_at is not None:
            last_failure_age_seconds = max(0.0, time.monotonic() - float(last_failure_at))

        return {
            "provider": name,
            "is_open": bool(is_open),
            "consecutive_failures": consecutive_failures,
            "opened_for_seconds": opened_for_seconds,
            "resets_in_seconds": resets_in_seconds,
            "total_calls": total_calls,
            "attempted_calls": attempted_calls,
            "success_count": success_count,
            "failure_count": failure_count,
            "skipped_open_count": skipped_open_count,
            "retry_count": retry_count,
            "circuit_open_count": circuit_open_count,
            "last_failure": last_failure,
            "last_failure_age_seconds": last_failure_age_seconds,
            "policy": {
                "max_retries": int(self.policy.max_retries),
                "retry_backoff_seconds": float(self.policy.retry_backoff_seconds),
                "circuit_breaker_failures": int(self.policy.circuit_breaker_failures),
                "circuit_breaker_reset_seconds": float(self.policy.circuit_breaker_reset_seconds),
            },
        }
