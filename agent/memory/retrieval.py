from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Any

from agent.memory.models import MemoryRecord, MemoryStatus

TOKEN_RE = re.compile(r"[A-Za-z0-9_\-\u4e00-\u9fff]{2,}")


def tokenize(text: str) -> set[str]:
    return {item.casefold() for item in TOKEN_RE.findall(str(text or ""))}


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if not norm_a or not norm_b:
        return 0.0
    return max(0.0, min(1.0, (dot / (norm_a * norm_b) + 1.0) / 2.0))


def keyword_score(query: str, record: MemoryRecord) -> float:
    query_tokens = tokenize(query)
    if not query_tokens:
        return 0.0
    record_tokens = tokenize(f"{record.content} {record.summary}")
    if not record_tokens:
        return 0.0
    overlap = query_tokens & record_tokens
    return min(1.0, len(overlap) / max(1, min(len(query_tokens), 8)))


def recency_score(record: MemoryRecord, *, now: datetime | None = None) -> float:
    raw = record.updated_at or record.created_at
    if not raw:
        return 0.5
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        now = now or datetime.now(UTC)
        age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
    except Exception:
        return 0.5
    return 1.0 / (1.0 + age_days / 30.0)


def is_active(record: MemoryRecord, *, now: datetime | None = None) -> bool:
    if record.status != MemoryStatus.active.value:
        return False
    now = now or datetime.now(UTC)
    for field_name in ("expires_at", "valid_to"):
        raw = getattr(record, field_name, None)
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            if dt < now:
                return False
        except Exception:
            continue
    return True


def score_record(
    query: str,
    record: MemoryRecord,
    *,
    query_embedding: list[float] | None = None,
    now: datetime | None = None,
) -> tuple[float, dict[str, Any]]:
    vector = cosine_similarity(query_embedding or [], record.embedding)
    keyword = keyword_score(query, record)
    recency = recency_score(record, now=now)
    confidence = max(0.0, min(1.0, float(record.confidence or 0.0)))
    importance = max(0.0, min(1.0, float(record.importance or 0.0)))
    quality = max(0.0, min(1.0, float(record.quality_score or 0.0)))
    evidence = 1.0 if record.source_evidence_ids or record.source_urls else 0.0

    score = (
        vector * 0.25
        + keyword * 0.30
        + recency * 0.10
        + confidence * 0.15
        + importance * 0.10
        + quality * 0.05
        + evidence * 0.05
    )
    details = {
        "record_id": record.id,
        "score": round(score, 4),
        "vector": round(vector, 4),
        "keyword": round(keyword, 4),
        "recency": round(recency, 4),
        "confidence": round(confidence, 4),
        "importance": round(importance, 4),
        "quality": round(quality, 4),
        "evidence": evidence,
    }
    return score, details


def rank_records(
    query: str,
    records: list[MemoryRecord],
    *,
    query_embedding: list[float] | None = None,
    limit: int = 12,
) -> tuple[list[MemoryRecord], list[dict[str, Any]]]:
    scored: list[tuple[float, MemoryRecord, dict[str, Any]]] = []
    now = datetime.now(UTC)
    for record in records:
        if not is_active(record, now=now):
            continue
        score, details = score_record(
            query,
            record,
            query_embedding=query_embedding,
            now=now,
        )
        if score <= 0:
            continue
        scored.append((score, record, details))
    scored.sort(key=lambda item: item[0], reverse=True)
    top = scored[: max(0, int(limit or 0))]
    return [item[1] for item in top], [item[2] for item in top]
