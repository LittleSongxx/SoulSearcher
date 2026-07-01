"""Reliability wrapper for OpenAI-compatible LLM calls.

This module adds a small backend-engineering layer around LangChain chat
models: transient failure classification, exponential backoff with jitter,
per-provider circuit breaking, and introspection snapshots.  It is deliberately
provider-agnostic because SoulSearcher supports OpenAI-compatible endpoints
from several vendors.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from dataclasses import dataclass
from typing import Any

from common.config import settings

logger = logging.getLogger(__name__)


class LLMProviderCircuitOpen(RuntimeError):
    """Raised when an LLM provider circuit is open."""


@dataclass
class LLMReliabilityPolicy:
    enabled: bool = True
    max_retries: int = 2
    initial_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0
    jitter_seconds: float = 0.25
    circuit_breaker_failures: int = 5
    circuit_breaker_reset_seconds: float = 60.0

    @classmethod
    def from_settings(cls) -> LLMReliabilityPolicy:
        return cls(
            enabled=bool(getattr(settings, "llm_reliability_enabled", True)),
            max_retries=max(0, int(getattr(settings, "llm_retry_max_attempts", 3) or 3) - 1),
            initial_delay_seconds=max(
                0.0,
                float(getattr(settings, "llm_retry_initial_delay_seconds", 0.5) or 0.0),
            ),
            max_delay_seconds=max(
                0.0,
                float(getattr(settings, "llm_retry_max_delay_seconds", 8.0) or 0.0),
            ),
            jitter_seconds=max(
                0.0,
                float(getattr(settings, "llm_retry_jitter_seconds", 0.25) or 0.0),
            ),
            circuit_breaker_failures=max(
                1,
                int(getattr(settings, "llm_circuit_breaker_failures", 5) or 5),
            ),
            circuit_breaker_reset_seconds=max(
                1.0,
                float(getattr(settings, "llm_circuit_breaker_reset_seconds", 60.0) or 60.0),
            ),
        )


@dataclass
class LLMCircuitState:
    consecutive_failures: int = 0
    opened_at: float | None = None
    total_calls: int = 0
    attempted_calls: int = 0
    success_count: int = 0
    failure_count: int = 0
    retry_count: int = 0
    skipped_open_count: int = 0
    circuit_open_count: int = 0
    last_failure: str = ""
    last_failure_at: float | None = None
    last_failure_kind: str = ""
    last_model: str = ""


@dataclass
class LLMCallFailure:
    retryable: bool
    kind: str
    status_code: int | None = None
    retry_after: float | None = None


class LLMReliabilityManager:
    def __init__(self, policy: LLMReliabilityPolicy | None = None) -> None:
        self.policy = policy or LLMReliabilityPolicy.from_settings()
        self._states: dict[str, LLMCircuitState] = {}

    def reset(self) -> None:
        self._states.clear()

    def snapshot(self, provider: str | None = None) -> dict[str, Any]:
        now = time.monotonic()
        if provider is not None:
            return self._snapshot_one(provider, self._states.get(provider), now)
        return {
            name: self._snapshot_one(name, state, now)
            for name, state in sorted(self._states.items())
        }

    async def acall(self, provider: str, model: str, func, *args, **kwargs):
        if not self.policy.enabled:
            return await func(*args, **kwargs)

        provider_key = provider or "default"
        state = self._states.setdefault(provider_key, LLMCircuitState())
        state.total_calls += 1
        state.last_model = model or state.last_model
        self._raise_if_open(provider_key, state)

        attempts = self.policy.max_retries + 1
        last_exc: BaseException | None = None
        for attempt in range(attempts):
            if attempt:
                state.retry_count += 1
            state.attempted_calls += 1
            try:
                result = await func(*args, **kwargs)
                self._record_success(state)
                return result
            except Exception as exc:
                last_exc = exc
                failure = classify_llm_failure(exc)
                self._record_failure(provider_key, state, exc, failure)
                if not failure.retryable or attempt >= attempts - 1:
                    raise
                await asyncio.sleep(self._retry_delay(attempt, failure))
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("LLM call failed without an exception")

    def call(self, provider: str, model: str, func, *args, **kwargs):
        if not self.policy.enabled:
            return func(*args, **kwargs)

        provider_key = provider or "default"
        state = self._states.setdefault(provider_key, LLMCircuitState())
        state.total_calls += 1
        state.last_model = model or state.last_model
        self._raise_if_open(provider_key, state)

        attempts = self.policy.max_retries + 1
        last_exc: BaseException | None = None
        for attempt in range(attempts):
            if attempt:
                state.retry_count += 1
            state.attempted_calls += 1
            try:
                result = func(*args, **kwargs)
                self._record_success(state)
                return result
            except Exception as exc:
                last_exc = exc
                failure = classify_llm_failure(exc)
                self._record_failure(provider_key, state, exc, failure)
                if not failure.retryable or attempt >= attempts - 1:
                    raise
                time.sleep(self._retry_delay(attempt, failure))
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("LLM call failed without an exception")

    def _raise_if_open(self, provider: str, state: LLMCircuitState) -> None:
        if state.opened_at is None:
            return
        elapsed = time.monotonic() - state.opened_at
        if elapsed >= self.policy.circuit_breaker_reset_seconds:
            state.opened_at = None
            state.consecutive_failures = 0
            return
        state.skipped_open_count += 1
        raise LLMProviderCircuitOpen(
            f"LLM provider circuit is open for {provider}; "
            f"retry after {int(self.policy.circuit_breaker_reset_seconds - elapsed) + 1}s"
        )

    def _record_success(self, state: LLMCircuitState) -> None:
        state.success_count += 1
        state.consecutive_failures = 0
        state.opened_at = None

    def _record_failure(
        self,
        provider: str,
        state: LLMCircuitState,
        exc: Exception,
        failure: LLMCallFailure,
    ) -> None:
        state.failure_count += 1
        state.consecutive_failures += 1
        state.last_failure = sanitize_llm_error(str(exc))
        state.last_failure_at = time.monotonic()
        state.last_failure_kind = failure.kind
        if state.consecutive_failures >= self.policy.circuit_breaker_failures:
            if state.opened_at is None:
                state.circuit_open_count += 1
                logger.warning(
                    "[LLM] circuit opened for provider=%s after %d failures (%s)",
                    provider,
                    state.consecutive_failures,
                    state.last_failure_kind,
                )
            state.opened_at = time.monotonic()

    def _retry_delay(self, attempt: int, failure: LLMCallFailure) -> float:
        if failure.retry_after is not None:
            return max(0.0, min(float(failure.retry_after), self.policy.max_delay_seconds))
        base = self.policy.initial_delay_seconds * (2 ** max(0, attempt))
        jitter = random.uniform(0.0, self.policy.jitter_seconds) if self.policy.jitter_seconds else 0.0
        return max(0.0, min(base + jitter, self.policy.max_delay_seconds))

    def _snapshot_one(
        self,
        provider: str,
        state: LLMCircuitState | None,
        now: float,
    ) -> dict[str, Any]:
        if state is None:
            return {
                "provider": provider,
                "is_open": False,
                "consecutive_failures": 0,
                "total_calls": 0,
                "attempted_calls": 0,
                "success_count": 0,
                "failure_count": 0,
                "retry_count": 0,
                "skipped_open_count": 0,
                "circuit_open_count": 0,
            }
        opened_for = (now - state.opened_at) if state.opened_at is not None else None
        resets_in = (
            max(0.0, self.policy.circuit_breaker_reset_seconds - opened_for)
            if opened_for is not None
            else None
        )
        last_failure_age = (
            now - state.last_failure_at if state.last_failure_at is not None else None
        )
        return {
            "provider": provider,
            "last_model": state.last_model,
            "is_open": state.opened_at is not None,
            "consecutive_failures": state.consecutive_failures,
            "opened_for_seconds": opened_for,
            "resets_in_seconds": resets_in,
            "total_calls": state.total_calls,
            "attempted_calls": state.attempted_calls,
            "success_count": state.success_count,
            "failure_count": state.failure_count,
            "retry_count": state.retry_count,
            "skipped_open_count": state.skipped_open_count,
            "circuit_open_count": state.circuit_open_count,
            "last_failure": state.last_failure,
            "last_failure_kind": state.last_failure_kind,
            "last_failure_age_seconds": last_failure_age,
        }


class ReliableChatModel:
    """Transparent proxy that wraps LangChain chat model invocations."""

    def __init__(
        self,
        wrapped: Any,
        *,
        provider: str,
        model: str,
        manager: LLMReliabilityManager | None = None,
    ) -> None:
        self._wrapped = wrapped
        self._provider = provider or "default"
        self._model = model or ""
        self._manager = manager or llm_reliability_manager

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)

    async def ainvoke(self, *args, **kwargs):
        return await self._manager.acall(
            self._provider,
            self._model,
            self._wrapped.ainvoke,
            *args,
            **kwargs,
        )

    def invoke(self, *args, **kwargs):
        return self._manager.call(
            self._provider,
            self._model,
            self._wrapped.invoke,
            *args,
            **kwargs,
        )

    def with_config(self, *args, **kwargs):
        return self._wrap(self._wrapped.with_config(*args, **kwargs), *args)

    def with_retry(self, *args, **kwargs):
        return self._wrap(self._wrapped.with_retry(*args, **kwargs))

    def bind_tools(self, *args, **kwargs):
        return self._wrap(self._wrapped.bind_tools(*args, **kwargs))

    def with_structured_output(self, *args, **kwargs):
        return self._wrap(self._wrapped.with_structured_output(*args, **kwargs))

    def bind(self, *args, **kwargs):
        return self._wrap(self._wrapped.bind(*args, **kwargs))

    def _wrap(self, wrapped: Any, *config_args: Any) -> ReliableChatModel:
        provider = self._provider
        model = self._model
        for item in config_args:
            if isinstance(item, dict):
                candidate = _extract_configurable(item)
                provider = (
                    str(candidate.get("llm_provider") or "")
                    or provider_from_model_config(candidate)
                    or provider
                )
                model = str(candidate.get("model") or model)
        return ReliableChatModel(
            wrapped,
            provider=provider,
            model=model,
            manager=self._manager,
        )


def wrap_chat_model(model: Any, *, provider: str = "", model_name: str = "") -> ReliableChatModel:
    if isinstance(model, ReliableChatModel):
        return model
    return ReliableChatModel(
        model,
        provider=provider or provider_from_model_config({"model": model_name}),
        model=model_name,
    )


def provider_from_model_config(config: dict[str, Any]) -> str:
    config = _extract_configurable(config)
    base_url = str(config.get("base_url") or config.get("azure_endpoint") or "").lower()
    model = str(config.get("model") or config.get("azure_deployment") or "")
    if "dashscope.aliyuncs.com" in base_url or model.startswith("qwen"):
        return "dashscope"
    if "azure" in base_url or config.get("azure_endpoint"):
        return "azure_openai"
    if "openai.com" in base_url or not base_url:
        return "openai"
    return base_url.split("//")[-1].split("/")[0].split(":")[0] or "openai_compatible"


def _extract_configurable(config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    configurable = config.get("configurable")
    if isinstance(configurable, dict):
        merged = dict(config)
        merged.update(configurable)
        return merged
    return config


def classify_llm_failure(exc: BaseException) -> LLMCallFailure:
    status_code = _status_code(exc)
    retry_after = _retry_after(exc)
    if status_code in {408, 409, 425, 429, 500, 502, 503, 504}:
        return LLMCallFailure(
            retryable=True,
            kind=f"http_{status_code}",
            status_code=status_code,
            retry_after=retry_after,
        )
    if status_code is not None:
        return LLMCallFailure(
            retryable=False,
            kind=f"http_{status_code}",
            status_code=status_code,
            retry_after=retry_after,
        )
    text = str(exc).lower()
    retryable_markers = (
        "timeout",
        "timed out",
        "connection reset",
        "connection aborted",
        "temporarily unavailable",
        "rate limit",
        "too many requests",
        "server overloaded",
        "service unavailable",
    )
    if any(marker in text for marker in retryable_markers):
        return LLMCallFailure(retryable=True, kind="transient")
    return LLMCallFailure(retryable=False, kind=exc.__class__.__name__)


def _status_code(exc: BaseException) -> int | None:
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        if value is not None:
            try:
                return int(value)
            except Exception:
                pass
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    if value is not None:
        try:
            return int(value)
        except Exception:
            return None
    return None


def _retry_after(exc: BaseException) -> float | None:
    headers = getattr(exc, "headers", None)
    if headers is None:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
    if not headers:
        return None
    try:
        value = headers.get("retry-after") or headers.get("Retry-After")
    except Exception:
        return None
    try:
        return float(value)
    except Exception:
        return None


def sanitize_llm_error(message: str, *, max_len: int = 500) -> str:
    text = str(message or "")
    text = re.sub(r"\b(sk|sess|key)-[A-Za-z0-9_\-]{6,}", r"\1-***", text)
    text = re.sub(r"(?i)(api[_-]?key|authorization|bearer)\s*[:=]\s*\S+", r"\1=***", text)
    return text[:max_len]


llm_reliability_manager = LLMReliabilityManager()
