import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from langchain_core.messages import BaseMessage, SystemMessage, ToolMessage

from agent.workflows.model_context_policy import is_token_limit_error
from common.config import settings

logger = logging.getLogger(__name__)


def retry_call(fn: Callable, *, attempts: int, backoff: float, **kwargs) -> Any:
    """
    Simple synchronous retry helper with exponential backoff.
    Note: Use async_retry_call for async contexts to avoid blocking.
    """
    last_exc = None
    for i in range(attempts):
        try:
            return fn(**kwargs)
        except Exception as e:
            last_exc = e
            wait = backoff * (2**i)
            logger.warning(
                f"Tool call failed (attempt {i + 1}/{attempts}): {e}; retrying in {wait:.1f}s"
            )
            time.sleep(wait)
    if last_exc:
        raise last_exc
    return None


async def async_retry_call(
    fn: Callable, *, attempts: int, backoff: float, **kwargs
) -> Any:
    """
    Async retry helper with exponential backoff.
    Does not block the event loop during wait.
    """
    last_exc = None
    for i in range(attempts):
        try:
            result = fn(**kwargs)
            # If fn returns a coroutine, await it
            if asyncio.iscoroutine(result):
                return await result
            return result
        except Exception as e:
            last_exc = e
            wait = backoff * (2**i)
            logger.warning(
                f"Tool call failed (attempt {i + 1}/{attempts}): {e}; retrying in {wait:.1f}s"
            )
            await asyncio.sleep(wait)
    if last_exc:
        raise last_exc
    return None


def enforce_tool_call_limit(state: dict[str, Any], limit: int) -> None:
    """
    Increment and enforce per-run tool call limit stored on state.
    limit=0 means unlimited.
    """
    if limit <= 0:
        return
    count = int(state.get("tool_call_count", 0)) + 1
    state["tool_call_count"] = count
    if count > limit:
        raise RuntimeError(f"Tool call limit exceeded ({count}/{limit})")


def maybe_strip_tool_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    """
    Optionally remove ToolMessage from history to save tokens.
    """
    if not settings.strip_tool_messages:
        return messages
    return [m for m in messages if not isinstance(m, ToolMessage)]


def mask_old_observations(messages: list[BaseMessage]) -> list[BaseMessage]:
    """
    Hybrid observation masking: keep recent tool observations in full,
    replace older ones with compact placeholders.

    This preserves full reasoning/action history while cutting token cost
    on stale observations by ~50%+.

    Controlled by:
    - settings.observation_masking (bool)
    - settings.observation_masking_window (int): recent messages whose
      ToolMessage observations are kept verbatim.
    """
    if not settings.observation_masking:
        return messages

    window = max(int(getattr(settings, "observation_masking_window", 5)), 0)

    # Find indices of all ToolMessages
    tool_indices = [i for i, m in enumerate(messages) if isinstance(m, ToolMessage)]
    if not tool_indices:
        return messages

    # The last `window` ToolMessages stay intact; older ones get masked
    keep_set = set(tool_indices[-window:]) if window else set()

    result: list[BaseMessage] = []
    for i, msg in enumerate(messages):
        if isinstance(msg, ToolMessage) and i not in keep_set:
            content = getattr(msg, "content", "") or ""
            # Build compact one-line summary (first 120 chars)
            snippet = content[:120].replace("\n", " ").strip()
            if len(content) > 120:
                snippet += "…"
            masked = ToolMessage(
                content=f"[Observation masked: {snippet}]",
                tool_call_id=getattr(msg, "tool_call_id", ""),
                name=getattr(msg, "name", None),
            )
            result.append(masked)
        else:
            result.append(msg)
    return result


def shrink_messages_for_retry(
    messages: list[BaseMessage],
    *,
    keep_ratio: float,
    keep_last_min: int = 2,
) -> list[BaseMessage]:
    """
    Shrink a message list while preserving SystemMessages and the most recent turns.

    All SystemMessages are kept verbatim (system prompts carry the task contract).
    Among non-system messages, the trailing ``keep_ratio`` fraction is preserved,
    with a floor of ``keep_last_min`` to ensure the last user/assistant turn is
    visible to the LLM.

    Args:
        messages: Original message list.
        keep_ratio: Fraction of non-system messages to keep from the tail
            (e.g. 0.5 keeps the most recent half). Clamped to [0, 1].
        keep_last_min: Minimum number of trailing non-system messages to keep,
            even if ``keep_ratio`` would yield fewer. Useful when the message
            list is small.

    Returns:
        New message list with SystemMessages preserved at their original positions
        relative to the kept tail.
    """
    if not messages:
        return list(messages or [])

    ratio = max(0.0, min(1.0, float(keep_ratio)))
    system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
    non_system = [m for m in messages if not isinstance(m, SystemMessage)]

    if not non_system:
        return list(system_msgs)

    keep_count = max(int(len(non_system) * ratio), keep_last_min)
    keep_count = min(keep_count, len(non_system))
    kept_tail = non_system[-keep_count:] if keep_count > 0 else []

    return system_msgs + kept_tail


_DEFAULT_SHRINK_RATIOS = (0.5, 0.25)


def _resolve_shrink_ratios(max_retries: int) -> tuple[float, ...]:
    """Pick a shrink schedule sized to ``max_retries``.

    Always returns at least one ratio. For ``max_retries`` larger than the
    default schedule, additional retries reuse the smallest ratio (most
    aggressive truncation).
    """
    retries = max(1, int(max_retries))
    if retries <= len(_DEFAULT_SHRINK_RATIOS):
        return _DEFAULT_SHRINK_RATIOS[:retries]
    extra = retries - len(_DEFAULT_SHRINK_RATIOS)
    return _DEFAULT_SHRINK_RATIOS + (_DEFAULT_SHRINK_RATIOS[-1],) * extra


def invoke_with_token_recovery(
    llm: Any,
    messages: list[BaseMessage],
    *,
    config: dict | None = None,
    max_retries: int = 2,
    label: str = "",
) -> Any:
    """
    Invoke an LLM with messages, recovering from token-limit errors only.

    Strategy:
        1. Try the original ``messages`` once.
        2. If the failure matches ``is_token_limit_error``, shrink the message
           history (preserving SystemMessages and the trailing turns) and retry.
        3. Other exceptions propagate immediately so callers can surface real
           errors (auth, schema, network) without silent corruption.
        4. After ``max_retries`` exhausted, re-raise the last token-limit error.

    The function does not mutate ``messages``. ``config`` is forwarded to
    ``llm.invoke`` when provided (LangChain ``RunnableConfig``).

    Args:
        llm: A LangChain runnable exposing ``.invoke(messages, config=...)``.
        messages: Conversation history.
        config: Optional ``RunnableConfig`` forwarded to the LLM.
        max_retries: Maximum shrink+retry attempts after the first failure
            (default 2). Total LLM calls = 1 + max_retries in the worst case.
        label: Optional tag included in log messages for traceability.

    Returns:
        Whatever ``llm.invoke`` returns.

    Raises:
        Exception: Re-raises the last error if all retries fail or the error
            is not token-limit related.
    """
    invoke_kwargs: dict[str, Any] = {}
    if config is not None:
        invoke_kwargs["config"] = config

    try:
        return llm.invoke(messages, **invoke_kwargs)
    except Exception as exc:
        if not is_token_limit_error(exc):
            raise
        last_exc: BaseException = exc
        ratios = _resolve_shrink_ratios(max_retries)
        for attempt, ratio in enumerate(ratios, start=1):
            shrunk = shrink_messages_for_retry(messages, keep_ratio=ratio)
            if len(shrunk) >= len(messages):
                # Already at minimum size; further shrinking would be a no-op.
                break
            logger.warning(
                "[token_recovery%s] attempt %d/%d: shrank %d -> %d messages (ratio=%.2f)",
                f" {label}" if label else "",
                attempt,
                len(ratios),
                len(messages),
                len(shrunk),
                ratio,
            )
            try:
                return llm.invoke(shrunk, **invoke_kwargs)
            except Exception as retry_exc:
                if not is_token_limit_error(retry_exc):
                    raise
                last_exc = retry_exc
        raise last_exc


async def ainvoke_with_token_recovery(
    llm: Any,
    messages: list[BaseMessage],
    *,
    config: dict | None = None,
    max_retries: int = 2,
    label: str = "",
) -> Any:
    """Async variant of :func:`invoke_with_token_recovery`.

    See :func:`invoke_with_token_recovery` for behaviour. Uses ``llm.ainvoke``
    instead of ``llm.invoke``.
    """
    invoke_kwargs: dict[str, Any] = {}
    if config is not None:
        invoke_kwargs["config"] = config

    try:
        return await llm.ainvoke(messages, **invoke_kwargs)
    except Exception as exc:
        if not is_token_limit_error(exc):
            raise
        last_exc: BaseException = exc
        ratios = _resolve_shrink_ratios(max_retries)
        for attempt, ratio in enumerate(ratios, start=1):
            shrunk = shrink_messages_for_retry(messages, keep_ratio=ratio)
            if len(shrunk) >= len(messages):
                break
            logger.warning(
                "[token_recovery%s] attempt %d/%d: shrank %d -> %d messages (ratio=%.2f)",
                f" {label}" if label else "",
                attempt,
                len(ratios),
                len(messages),
                len(shrunk),
                ratio,
            )
            try:
                return await llm.ainvoke(shrunk, **invoke_kwargs)
            except Exception as retry_exc:
                if not is_token_limit_error(retry_exc):
                    raise
                last_exc = retry_exc
        raise last_exc
