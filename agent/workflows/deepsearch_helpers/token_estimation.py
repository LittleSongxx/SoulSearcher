"""Token estimation and budget helpers for DeepSearch."""

from __future__ import annotations

import time
from typing import Any, Optional


def _estimate_tokens_from_text(text: str) -> int:
    """Estimate token count for mixed CJK/Latin text.

    CJK characters typically map to 1-2 tokens each; Latin/ASCII runs
    average ~4 characters per token.  The simple ``len // 4`` heuristic
    severely underestimates token counts for Chinese/Japanese/Korean text.
    """
    if not text:
        return 0
    s = str(text)
    cjk = 0
    other = 0
    for ch in s:
        cp = ord(ch)
        if (
            0x4E00 <= cp <= 0x9FFF      # CJK Unified Ideographs
            or 0x3400 <= cp <= 0x4DBF    # CJK Extension A
            or 0xF900 <= cp <= 0xFAFF    # CJK Compatibility Ideographs
            or 0x3000 <= cp <= 0x303F    # CJK Symbols and Punctuation
            or 0xFF00 <= cp <= 0xFFEF    # Fullwidth Forms
            or 0xAC00 <= cp <= 0xD7AF    # Hangul Syllables
            or 0x3040 <= cp <= 0x309F    # Hiragana
            or 0x30A0 <= cp <= 0x30FF    # Katakana
        ):
            cjk += 1
        else:
            other += 1
    # CJK chars ≈ 1.5 tokens each; ASCII ≈ 0.25 tokens per char
    return max(1, int(cjk * 1.5 + other * 0.25))


def _estimate_tokens_from_results(results: list[dict[str, Any]]) -> int:
    tokens = 0
    for result in results or []:
        if not isinstance(result, dict):
            continue
        tokens += _estimate_tokens_from_text(result.get("title", ""))
        snippet = (
            result.get("raw_excerpt")
            or result.get("summary")
            or result.get("snippet")
            or result.get("content")
            or ""
        )
        tokens += _estimate_tokens_from_text(str(snippet)[:600])
    return tokens


def _budget_stop_reason(
    start_ts: float,
    tokens_used: int,
    max_seconds: float,
    max_tokens: int,
) -> Optional[str]:
    if max_seconds > 0 and (time.time() - start_ts) >= max_seconds:
        return "time_budget_exceeded"
    if max_tokens > 0 and tokens_used >= max_tokens:
        return "token_budget_exceeded"
    return None


def _should_skip_expensive_postprocessing(
    *,
    start_ts: float,
    max_seconds: float,
    budget_stop_reason: str,
    reserve_seconds: float = 20.0,
) -> bool:
    if budget_stop_reason:
        return True
    if max_seconds <= 0:
        return False
    remaining = float(max_seconds) - max(0.0, time.time() - start_ts)
    return remaining <= max(1.0, float(reserve_seconds or 0.0))
