"""Deterministic claim-to-citation matrix helpers.

This module is intentionally lightweight. It does not judge semantic truth by
itself; instead it turns a generated report plus the current-run citation table
into an auditable matrix that downstream evaluators, exports, and UI surfaces
can use to spot missing or untraceable citations.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

_CITATION_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
_STRUCTURAL_PREFIXES = (
    "sources:",
    "references:",
    "appendix:",
    "table of contents",
)


def build_claim_citation_matrix(
    report_text: str,
    citation_table: list[dict[str, Any]] | None,
    *,
    max_claims: int = 80,
) -> dict[str, Any]:
    """Build a stable matrix linking report claims to citation rows."""
    bindings = {
        int(row.get("citation_index") or row.get("marker") or row.get("source_index")): row
        for row in (citation_table or [])
        if isinstance(row, dict)
        and str(row.get("citation_index") or row.get("marker") or row.get("source_index") or "").isdigit()
    }
    claims: list[dict[str, Any]] = []
    for claim_text in _extract_claims(report_text)[:max_claims]:
        markers = _citation_markers(claim_text)
        linked = [dict(bindings[marker]) for marker in markers if marker in bindings]
        missing = [marker for marker in markers if marker not in bindings]
        untraceable = [
            int(row.get("citation_index") or row.get("marker") or 0)
            for row in linked
            if not row.get("traceable")
        ]
        if not markers:
            status = "missing_citation"
        elif missing:
            status = "missing_source"
        elif untraceable:
            status = "untraceable"
        else:
            status = "traceable"
        claims.append(
            {
                "id": _claim_id(claim_text),
                "claim": claim_text,
                "citation_markers": markers,
                "status": status,
                "traceable": status == "traceable",
                "missing_markers": missing,
                "untraceable_markers": untraceable,
                "evidence_ids": [
                    evidence.get("evidence_id")
                    for row in linked
                    for evidence in (row.get("matched_passages") or [])
                    if isinstance(evidence, dict) and evidence.get("evidence_id")
                ],
                "source_ids": [
                    row.get("source_id") for row in linked if row.get("source_id")
                ],
                "urls": [
                    row.get("canonical_url") or row.get("url")
                    for row in linked
                    if row.get("canonical_url") or row.get("url")
                ],
            }
        )

    claim_count = len(claims)
    cited_count = sum(1 for claim in claims if claim["citation_markers"])
    traceable_count = sum(1 for claim in claims if claim["traceable"])
    missing_count = sum(1 for claim in claims if claim["status"] == "missing_citation")
    untraceable_count = sum(
        1 for claim in claims if claim["status"] in {"missing_source", "untraceable"}
    )
    return {
        "claims": claims,
        "summary": {
            "claim_citation_total": claim_count,
            "claim_citation_cited": cited_count,
            "claim_citation_traceable": traceable_count,
            "claim_citation_missing": missing_count,
            "claim_citation_untraceable": untraceable_count,
            "claim_citation_coverage": round(cited_count / claim_count, 4)
            if claim_count
            else 1.0,
            "claim_citation_traceability": round(traceable_count / claim_count, 4)
            if claim_count
            else 1.0,
        },
    }


def _extract_claims(report_text: str) -> list[str]:
    claims: list[str] = []
    for raw_line in str(report_text or "").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not _looks_like_claim_line(line):
            continue
        for sentence in _split_sentences(line):
            if _looks_like_claim_line(sentence):
                claims.append(sentence)
    return list(dict.fromkeys(claims))


def _looks_like_claim_line(text: str) -> bool:
    value = text.strip()
    lower = value.lower()
    if len(value) < 35:
        return False
    if _HEADING_RE.match(value):
        return False
    if any(lower.startswith(prefix) for prefix in _STRUCTURAL_PREFIXES):
        return False
    if lower.startswith(("http://", "https://")):
        return False
    if _CITATION_RE.search(value):
        return True
    if re.search(r"\d{4}|\d+(?:\.\d+)?\s*%|\$|€|¥", value):
        return True
    return len(value) >= 80


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?。！？])\s+", text)
    if len(parts) == 1:
        return [text]
    return [part.strip() for part in parts if part.strip()]


def _citation_markers(text: str) -> list[int]:
    markers: list[int] = []
    for match in _CITATION_RE.finditer(text):
        for value in re.split(r"\s*,\s*", match.group(1)):
            try:
                markers.append(int(value))
            except ValueError:
                continue
    return list(dict.fromkeys(markers))


def _claim_id(text: str) -> str:
    return "claim_" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
