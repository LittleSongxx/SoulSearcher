from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import SystemMessage

from agent.memory import get_memory_service

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RequestMemoryContext:
    messages: list[Any] = field(default_factory=list)
    source_candidates: list[dict[str, Any]] = field(default_factory=list)
    retrieval_payload: dict[str, Any] = field(default_factory=dict)


def load_request_memory_context(
    *,
    settings: Any,
    user_id: str,
    input_text: str,
) -> RequestMemoryContext:
    if not getattr(settings, "memory_enabled", True):
        return RequestMemoryContext()
    try:
        memory_result = get_memory_service().retrieve(
            user_id=user_id or "default",
            query=input_text,
            include_context=True,
        )
        source_candidates = memory_source_candidates(memory_result)
        retrieval_payload = {
            "record_ids": [
                str(getattr(record, "id", ""))
                for record in (getattr(memory_result, "records", []) or [])
                if str(getattr(record, "id", ""))
            ],
            "source_candidates": source_candidates,
            "scoring": list(getattr(memory_result, "scoring", []) or []),
            "usage": (
                "Memory is research context only; memory-backed sources must be "
                "verified in the current run before citation."
            ),
        }
        messages: list[Any] = []
        if memory_result.context:
            messages.append(
                SystemMessage(
                    content=memory_result.context,
                    additional_kwargs={"hide_from_ui": True, "memory_context": True},
                )
            )
        return RequestMemoryContext(
            messages=messages,
            source_candidates=source_candidates,
            retrieval_payload=retrieval_payload,
        )
    except Exception as e:
        logger.warning("[Memory] Retrieval skipped: %s", e)
        return RequestMemoryContext()


def memory_source_candidates(memory_result: Any) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in getattr(memory_result, "records", []) or []:
        source_urls = [
            str(url).strip()
            for url in getattr(record, "source_urls", []) or []
            if str(url).strip()
        ]
        if not source_urls:
            continue
        for url in source_urls[:3]:
            if url in seen:
                continue
            seen.add(url)
            candidates.append(
                {
                    "url": url,
                    "title": str(getattr(record, "summary", "") or getattr(record, "content", ""))[:160],
                    "summary": str(getattr(record, "summary", "") or getattr(record, "content", ""))[:500],
                    "source": "memory",
                    "tool": "memory_retrieval",
                    "memory_record_id": str(getattr(record, "id", "")),
                    "memory_record_type": str(getattr(record, "type", "")),
                    "memory_source_evidence_ids": list(
                        getattr(record, "source_evidence_ids", []) or []
                    )[:8],
                    "requires_current_run_verification": True,
                }
            )
            if len(candidates) >= 12:
                return candidates
    return candidates
