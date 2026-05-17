"""Low-level text utilities shared across deepsearch helpers."""

from __future__ import annotations

import re
from typing import Any

from agent.workflows.source_url_utils import canonicalize_source_url


def _inline_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _canonical_text_url(value: Any) -> str:
    return canonicalize_source_url(value) or str(value or "").strip()


def _item_snippet(item: dict[str, Any]) -> str:
    if not isinstance(item, dict):
        return ""
    for key in (
        "snippet",
        "quote",
        "text",
        "summary",
        "content",
        "raw_excerpt",
        "markdown",
    ):
        text = _inline_text(item.get(key))
        if text:
            return text
    return ""


def _is_low_value_evidence_text(text: str) -> bool:
    lowered = str(text or "").lower()
    if not lowered.strip():
        return True
    if "please enable javascript" in lowered:
        return True
    if "checking your browser" in lowered:
        return True
    if "verify you are human" in lowered:
        return True
    if "cookie" in lowered and any(
        token in lowered
        for token in ("accept", "consent", "preferences", "manage cookies")
    ):
        return True
    return False


def _split_claim_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = [
            match.group(0)
            for match in re.finditer(r".+?(?:[。！？!?]|[.!?](?=\s|$))|.+$", stripped)
        ]
        if parts:
            sentences.extend(part.strip() for part in parts if part.strip())
        else:
            sentences.append(stripped)
    return sentences


def _is_short_claim_sentence(text: str) -> bool:
    value = str(text or "").strip()
    if re.search(r"[\u4e00-\u9fff]", value):
        return len(value) < 8
    return len(value) < 20
