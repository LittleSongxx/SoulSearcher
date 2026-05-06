from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, Optional

from agent.workflows.claim_verifier import ClaimCheck, ClaimStatus, ClaimVerifier
from agent.workflows.evidence import evidence_to_passages
from agent.workflows.source_url_utils import canonicalize_source_url


def _text(value: Any) -> str:
    return str(value or "").strip()


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _source_index_by_url(sources: Iterable[dict[str, Any]]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, source in enumerate(sources or [], 1):
        if not isinstance(source, dict):
            continue
        urls = [source.get("url"), source.get("rawUrl")]
        for url in urls:
            canonical = canonicalize_source_url(url) or _text(url)
            if canonical and canonical not in mapping:
                mapping[canonical] = idx
    return mapping


def _evidence_ids_by_url(evidence_items: Iterable[dict[str, Any]]) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for item in evidence_items or []:
        if not isinstance(item, dict):
            continue
        evidence_id = _text(item.get("id"))
        canonical = canonicalize_source_url(item.get("url")) or _text(item.get("url"))
        if not evidence_id or not canonical:
            continue
        mapping.setdefault(canonical, [])
        if evidence_id not in mapping[canonical]:
            mapping[canonical].append(evidence_id)
    return mapping


def _extract_quotes(check: ClaimCheck) -> list[str]:
    quotes: list[str] = []
    for passage in check.evidence_passages or []:
        if not isinstance(passage, dict):
            continue
        quote = _collapse(passage.get("quote") or passage.get("text") or "")
        if quote and quote not in quotes:
            quotes.append(quote[:260])
    return quotes[:3]


def _check_to_entry(
    check: ClaimCheck,
    *,
    source_indices: dict[str, int],
    evidence_ids: dict[str, list[str]],
    ordinal: int,
) -> dict[str, Any]:
    urls: list[str] = []
    indices: list[int] = []
    ids: list[str] = []
    for raw_url in check.evidence_urls or []:
        canonical = canonicalize_source_url(raw_url) or _text(raw_url)
        if not canonical:
            continue
        if canonical not in urls:
            urls.append(canonical)
        index = source_indices.get(canonical)
        if isinstance(index, int) and index not in indices:
            indices.append(index)
        for evidence_id in evidence_ids.get(canonical, []):
            if evidence_id not in ids:
                ids.append(evidence_id)
    return {
        "id": f"C{ordinal}",
        "claim": check.claim,
        "status": check.status.value,
        "source_indices": indices,
        "source_urls": urls,
        "evidence_ids": ids[:8],
        "quotes": _extract_quotes(check),
        "score": check.score,
        "notes": check.notes,
    }


def build_claim_ledger(
    *,
    summary_notes: list[str],
    search_runs: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    evidence_items: Optional[list[dict[str, Any]]] = None,
    passages: Optional[list[dict[str, Any]]] = None,
    max_claims: int = 24,
    min_overlap_tokens: int = 2,
    max_evidence_per_claim: int = 3,
) -> list[dict[str, Any]]:
    text = "\n".join(_text(item) for item in summary_notes or [] if _text(item))
    if not text:
        return []
    verifier = ClaimVerifier(
        min_overlap_tokens=min_overlap_tokens,
        max_evidence_per_claim=max_evidence_per_claim,
    )
    evidence_passages = passages if passages else evidence_to_passages(evidence_items or [])
    checks = verifier.verify_report(
        text,
        search_runs,
        max_claims=max_claims,
        passages=evidence_passages if evidence_passages else None,
    )
    source_indices = _source_index_by_url(sources)
    evidence_id_map = _evidence_ids_by_url(evidence_items or [])
    return [
        _check_to_entry(
            check,
            source_indices=source_indices,
            evidence_ids=evidence_id_map,
            ordinal=idx,
        )
        for idx, check in enumerate(checks, 1)
    ]


def format_claim_ledger_for_writer(ledger: list[dict[str, Any]], *, max_entries: int = 18) -> str:
    if not ledger:
        return ""
    lines = [
        "# 声明-证据账本",
        "写作时优先使用 status=verified 的声明；status=unsupported 或 contradicted 的声明只能写成不确定、资料不足或删除，不得写成确定事实。",
    ]
    for entry in ledger[: max(1, int(max_entries or 1))]:
        claim = _collapse(entry.get("claim"))
        status = _text(entry.get("status"))
        indices = entry.get("source_indices") if isinstance(entry.get("source_indices"), list) else []
        refs = " ".join(f"[{idx}]" for idx in indices if isinstance(idx, int)) or "无正文引用"
        quotes = entry.get("quotes") if isinstance(entry.get("quotes"), list) else []
        quote = _collapse(quotes[0]) if quotes else ""
        line = f"- {entry.get('id')}: status={status}; refs={refs}; claim={claim}"
        if quote:
            line += f"; evidence={quote}"
        lines.append(line)
    return "\n".join(lines).strip()


def summarize_claim_checks(checks: list[ClaimCheck]) -> dict[str, int]:
    return {
        "claim_verifier_total": len(checks or []),
        "claim_verifier_verified": sum(1 for check in checks or [] if check.status == ClaimStatus.VERIFIED),
        "claim_verifier_unsupported": sum(1 for check in checks or [] if check.status == ClaimStatus.UNSUPPORTED),
        "claim_verifier_contradicted": sum(1 for check in checks or [] if check.status == ClaimStatus.CONTRADICTED),
    }


def serialize_claim_checks(checks: list[ClaimCheck]) -> list[dict[str, Any]]:
    return [
        {
            "claim": check.claim,
            "status": check.status.value,
            "evidence_urls": check.evidence_urls,
            "evidence_passages": check.evidence_passages,
            "score": check.score,
            "notes": check.notes,
        }
        for check in checks or []
    ]
