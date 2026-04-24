import asyncio
import logging
import time
from typing import Any, Callable, Dict, List

from langchain_core.messages import BaseMessage, ToolMessage

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


def enforce_tool_call_limit(state: Dict[str, Any], limit: int) -> None:
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


def maybe_strip_tool_messages(messages: List[BaseMessage]) -> List[BaseMessage]:
    """
    Optionally remove ToolMessage from history to save tokens.
    """
    if not settings.strip_tool_messages:
        return messages
    return [m for m in messages if not isinstance(m, ToolMessage)]


def mask_old_observations(messages: List[BaseMessage]) -> List[BaseMessage]:
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

    result: List[BaseMessage] = []
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
