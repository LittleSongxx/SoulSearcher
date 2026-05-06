from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9\u4e00-\u9fff]+", str(text or "").lower()) if len(token) > 1}


def _evidence_text(item: dict[str, Any]) -> str:
    values = [
        item.get("claim"),
        item.get("quote"),
        item.get("snippet"),
        item.get("summary"),
        item.get("content"),
        item.get("title"),
        item.get("source_title"),
    ]
    return " ".join(_text(value) for value in values if _text(value))


def build_brief_coverage_artifact(
    *,
    research_brief: dict[str, Any],
    fact_cards: Iterable[dict[str, Any]] | None = None,
    evidence_items: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    fields = []
    seen = set()
    for field in research_brief.get("expected_fields") or []:
        text = _text(field)
        if text and text not in seen:
            seen.add(text)
            fields.append(text)
    if not fields:
        return {
            "schema_version": 1,
            "expected_field_count": 0,
            "covered_field_count": 0,
            "score": 1.0,
            "missing_fields": [],
            "fields": [],
        }

    evidence_rows = [item for item in (fact_cards or []) if isinstance(item, dict)]
    if not evidence_rows:
        evidence_rows = [item for item in (evidence_items or []) if isinstance(item, dict)]
    evidence_texts = [_evidence_text(item) for item in evidence_rows]
    field_results: list[dict[str, Any]] = []
    for field in fields:
        field_tokens = _tokens(field)
        matched = False
        best_overlap = 0.0
        for text in evidence_texts:
            text_tokens = _tokens(text)
            if not field_tokens or not text_tokens:
                continue
            overlap = len(field_tokens & text_tokens) / max(1, min(len(field_tokens), 8))
            best_overlap = max(best_overlap, overlap)
            if overlap >= 0.34 or field.lower() in text.lower():
                matched = True
                break
        field_results.append(
            {
                "field": field,
                "status": "covered" if matched else "missing",
                "best_overlap": round(best_overlap, 4),
            }
        )
    covered = sum(1 for item in field_results if item["status"] == "covered")
    missing = [item["field"] for item in field_results if item["status"] == "missing"]
    return {
        "schema_version": 1,
        "expected_field_count": len(fields),
        "covered_field_count": covered,
        "score": round(covered / max(1, len(fields)), 4),
        "missing_fields": missing,
        "fields": field_results,
    }
