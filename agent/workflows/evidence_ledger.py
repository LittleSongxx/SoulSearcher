"""Evidence ledger helpers for stable source/citation artifacts.

This module centralizes the normalization that used to be spread across
researcher/report/quality code paths.  The helpers are intentionally small and
dict-based so existing artifacts remain backward compatible.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from agent.core.state import EvidenceItem
from agent.workflows.source_registry import SourceRegistry

_CITATION_MARKER_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def snippet_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    return hashlib.sha1(normalized[:2000].encode("utf-8")).hexdigest()[:16]


def normalize_evidence_item(
    item: dict[str, Any],
    *,
    registry: SourceRegistry | None = None,
    now: str | None = None,
    fallback_id_prefix: str = "evidence",
    index: int = 1,
) -> dict[str, Any] | None:
    """Normalize one evidence-like dict into Weaver's public artifact shape."""
    if not isinstance(item, dict):
        return None

    registry = registry or SourceRegistry()
    now = now or datetime.now().isoformat(timespec="seconds")
    url = str(
        item.get("url")
        or item.get("canonical_url")
        or item.get("source_url")
        or item.get("source")
        or ""
    ).strip()
    title = str(item.get("title") or item.get("name") or "").strip()[:240]
    content = str(
        item.get("content")
        or item.get("text")
        or item.get("evidence")
        or item.get("snippet")
        or item.get("summary")
        or title
        or ""
    ).strip()
    if not url and not content:
        return None

    record = registry.register(url=url, title=title) if url else None
    canonical_url = record.canonical_url if record else url
    source_id = str(item.get("source_id") or (record.source_id if record else ""))
    digest = str(item.get("snippet_hash") or snippet_hash(content or canonical_url))

    score_value = item.get("score")
    try:
        score = float(score_value) if score_value is not None else None
    except (TypeError, ValueError):
        score = None

    metadata = dict(item.get("metadata") if isinstance(item.get("metadata"), dict) else {})
    metadata.setdefault("canonical_url", canonical_url)
    metadata.setdefault("snippet_hash", digest)
    if item.get("cached_path"):
        metadata.setdefault("cached_path", str(item.get("cached_path")))
    if item.get("line_count") is not None:
        metadata.setdefault("line_count", item.get("line_count"))
    if source_id:
        metadata.setdefault("source_id", source_id)
    if item.get("source_index") is not None:
        metadata.setdefault("source_index", item.get("source_index"))
    if item.get("requires_current_run_verification") is not None:
        metadata.setdefault(
            "requires_current_run_verification",
            bool(item.get("requires_current_run_verification")),
        )
    if item.get("source"):
        metadata.setdefault("source_kind", str(item.get("source") or ""))

    evidence_id = str(
        item.get("id")
        or (
            f"{fallback_id_prefix}_{hashlib.sha1(f'{canonical_url}|{digest}|{index}'.encode('utf-8')).hexdigest()[:12]}"
        )
    )

    artifact = EvidenceItem(
        id=evidence_id,
        type=str(item.get("type") or ("source_text" if content else "source_url")),
        source_id=source_id,
        title=title,
        url=canonical_url,
        source=str(item.get("source") or canonical_url or title),
        content=content[:1600],
        tool=str(item.get("tool") or ""),
        query=str(item.get("query") or ""),
        retrieved_at=str(item.get("retrieved_at") or now),
        score=score,
        metadata=metadata,
    ).to_artifact()
    artifact["canonical_url"] = canonical_url
    artifact["snippet_hash"] = digest
    if source_id:
        artifact["source_id"] = source_id
    artifact["claim_support"] = {
        "status": str(item.get("support_status") or item.get("status") or "unverified"),
        "score": score,
    }
    return artifact


def build_evidence_ledger(
    state: dict[str, Any],
    *,
    curated_sources: list[dict[str, Any]] | None = None,
    notes: list[str] | None = None,
    max_items: int = 24,
) -> list[dict[str, Any]]:
    """Build a deduped evidence ledger from state, artifacts, sources, and notes."""
    state = state if isinstance(state, dict) else {}
    artifacts = state.get("deepsearch_artifacts", {}) or {}
    if not isinstance(artifacts, dict):
        artifacts = {}

    registry = SourceRegistry()
    now = datetime.now().isoformat(timespec="seconds")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(raw: dict[str, Any], *, prefix: str = "evidence") -> None:
        if len(items) >= max_items:
            return
        normalized = normalize_evidence_item(
            raw,
            registry=registry,
            now=now,
            fallback_id_prefix=prefix,
            index=len(items) + 1,
        )
        if not normalized:
            return
        key = (
            str(normalized.get("canonical_url") or normalized.get("url") or ""),
            str(normalized.get("snippet_hash") or ""),
        )
        if key in seen:
            return
        seen.add(key)
        items.append(normalized)

    for item in state.get("evidence_items", []) or []:
        if isinstance(item, dict):
            add(item, prefix="state")

    for item in artifacts.get("evidence_items", []) or []:
        if isinstance(item, dict):
            add(item, prefix="artifact")

    sources: list[dict[str, Any]] = []
    if curated_sources:
        sources.extend(curated_sources)
    state_sources = state.get("sources", []) or []
    if isinstance(state_sources, list):
        sources.extend([source for source in state_sources if isinstance(source, dict)])
    for source in sources:
        add(
            {
                "type": "source_url",
                "source_id": source.get("id") or source.get("source_id") or "",
                "title": source.get("title") or source.get("name") or "",
                "url": source.get("url") or source.get("source_url") or "",
                "source": source.get("url") or source.get("title") or "",
                "content": source.get("snippet") or source.get("summary") or source.get("title") or "",
                "score": source.get("relevance_score") or source.get("score"),
                "tool": source.get("tool") or "source_curation",
            },
            prefix="source",
        )

    note_values = notes if notes is not None else (state.get("notes", []) or [])
    for note in note_values:
        text = str(note or "").strip()
        if not text:
            continue
        for chunk in re.split(r"\n\s*\n", text):
            chunk = re.sub(r"\s+", " ", chunk).strip()
            if len(chunk) < 80:
                continue
            url_match = re.search(r"https?://[^\s\])>\"']+", chunk)
            url = url_match.group(0) if url_match else ""
            add(
                {
                    "type": "research_note",
                    "source": url,
                    "url": url,
                    "content": chunk,
                    "tool": "researcher",
                },
                prefix="note",
            )
            if len(items) >= max_items:
                break
        if len(items) >= max_items:
            break

    return items


def evidence_passages(evidence_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    passages: list[dict[str, Any]] = []
    for item in evidence_items or []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or item.get("text") or "").strip()
        if not content:
            continue
        canonical_url = str(
            item.get("canonical_url")
            or item.get("url")
            or item.get("source")
            or ""
        ).strip()
        passages.append(
            {
                "url": canonical_url,
                "canonical_url": canonical_url,
                "title": str(item.get("title") or item.get("source") or ""),
                "text": content[:1600],
                "evidence_id": str(item.get("id") or ""),
                "source_id": str(item.get("source_id") or ""),
                "snippet_hash": str(item.get("snippet_hash") or ""),
                "source_index": item.get("source_index"),
            }
        )
    return passages


def _normalize_source_candidate(
    item: dict[str, Any],
    *,
    registry: SourceRegistry,
    index: int,
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None

    url = str(
        item.get("url")
        or item.get("canonical_url")
        or item.get("source_url")
        or item.get("source")
        or ""
    ).strip()
    title = str(item.get("title") or item.get("name") or "").strip()[:240]
    content = str(
        item.get("content")
        or item.get("text")
        or item.get("snippet")
        or item.get("summary")
        or title
        or ""
    ).strip()
    if not url and not content:
        return None

    record = registry.register(url=url, title=title) if url else None
    canonical_url = record.canonical_url if record else url
    source_id = str(item.get("source_id") or (record.source_id if record else ""))
    digest = str(item.get("snippet_hash") or snippet_hash(content or canonical_url))
    metadata = dict(item.get("metadata") if isinstance(item.get("metadata"), dict) else {})
    metadata.setdefault("canonical_url", canonical_url)
    metadata.setdefault("snippet_hash", digest)
    if source_id:
        metadata.setdefault("source_id", source_id)
    return {
        "source_index": index,
        "title": title,
        "url": canonical_url,
        "raw_url": url,
        "canonical_url": canonical_url,
        "source_id": source_id,
        "snippet_hash": digest,
        "text": content[:1600],
        "metadata": metadata,
        "relevance_score": item.get("relevance_score") or item.get("score"),
        "authority_score": item.get("authority_score"),
        "coverage_score": item.get("coverage_score"),
        "recency_score": item.get("recency_score"),
        "requires_current_run_verification": bool(
            item.get("requires_current_run_verification")
            or metadata.get("requires_current_run_verification")
        ),
        "source_kind": str(
            item.get("source_kind")
            or metadata.get("source_kind")
            or item.get("source")
            or ""
        ),
    }


def build_citation_bindings(
    *,
    sources: list[dict[str, Any]] | None = None,
    evidence_items: list[dict[str, Any]] | None = None,
    passages: list[dict[str, Any]] | None = None,
    max_items: int = 24,
) -> list[dict[str, Any]]:
    """Build index-based source bindings used to trace citation markers."""
    registry = SourceRegistry()
    candidates: list[dict[str, Any]] = []
    normalized_sources: list[dict[str, Any]] = []

    source_values = [item for item in (sources or []) if isinstance(item, dict)]
    if not source_values:
        source_values = [item for item in (evidence_items or []) if isinstance(item, dict)]
    if not source_values:
        source_values = [item for item in (passages or []) if isinstance(item, dict)]

    for index, item in enumerate(source_values[:max_items], 1):
        normalized = _normalize_source_candidate(item, registry=registry, index=index)
        if normalized:
            normalized_sources.append(normalized)

    def _normalized_passages(values: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for index, item in enumerate([item for item in (values or []) if isinstance(item, dict)], 1):
            normalized = _normalize_source_candidate(item, registry=registry, index=index)
            if normalized:
                normalized["evidence_id"] = str(item.get("evidence_id") or item.get("id") or "")
                normalized["text"] = str(
                    item.get("text")
                    or item.get("content")
                    or item.get("snippet")
                    or ""
                ).strip()[:1600]
                output.append(normalized)
        return output

    normalized_passages = _normalized_passages(passages)
    normalized_evidence = _normalized_passages(evidence_items)

    def _match_passages(binding: dict[str, Any]) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        for candidate in normalized_passages + normalized_evidence:
            if not candidate:
                continue
            if binding.get("source_id") and candidate.get("source_id") == binding.get("source_id"):
                matches.append(candidate)
                continue
            if binding.get("canonical_url") and candidate.get("canonical_url") == binding.get("canonical_url"):
                if binding.get("snippet_hash") and candidate.get("snippet_hash") == binding.get("snippet_hash"):
                    matches.append(candidate)
                elif not binding.get("snippet_hash"):
                    matches.append(candidate)
        return matches

    for binding in normalized_sources:
        matches = _match_passages(binding)
        candidates.append(
            {
                **binding,
                "matched_passage_count": len(matches),
                "matched_passages": [
                    {
                        "evidence_id": item.get("evidence_id") or "",
                        "source_id": item.get("source_id") or "",
                        "canonical_url": item.get("canonical_url") or "",
                        "snippet_hash": item.get("snippet_hash") or "",
                        "title": item.get("title") or "",
                    }
                    for item in matches[:5]
                ],
                "traceable": bool(
                    binding.get("source_id")
                    and binding.get("canonical_url")
                    and binding.get("snippet_hash")
                    and matches
                ),
                "requires_current_run_verification": bool(
                    binding.get("requires_current_run_verification")
                ),
                "source_kind": str(binding.get("source_kind") or ""),
            }
        )
    return candidates


def build_citation_table(
    evidence_items: list[dict[str, Any]] | None,
    *,
    max_items: int = 24,
) -> list[dict[str, Any]]:
    """Return stable evidence-first citation rows for report prompts."""
    rows: list[dict[str, Any]] = []
    bindings = build_citation_bindings(
        sources=evidence_items,
        evidence_items=evidence_items,
        passages=evidence_passages(evidence_items or []),
        max_items=max_items,
    )
    for index, binding in enumerate(bindings[:max_items], 1):
        row = dict(binding)
        row["citation_index"] = index
        row["marker"] = index
        row["verification_status"] = "traceable" if row.get("traceable") else "unverified"
        if not row.get("traceable"):
            row["traceability_reason"] = (
                "missing source_id, canonical_url, snippet_hash, or matched passage"
            )
        else:
            row["traceability_reason"] = "source binding matches current-run evidence"
        rows.append(row)
    return rows


def format_citation_table_for_prompt(citation_table: list[dict[str, Any]]) -> str:
    """Render citation rows compactly for the writer model."""
    if not citation_table:
        return (
            "<Evidence Citation Table>\n"
            "No current-run evidence is available. Do not invent citations.\n"
            "</Evidence Citation Table>"
        )
    lines = ["<Evidence Citation Table>"]
    for row in citation_table:
        idx = row.get("citation_index") or row.get("marker") or row.get("source_index")
        title = str(row.get("title") or row.get("url") or "Untitled source").strip()
        url = str(row.get("canonical_url") or row.get("url") or "").strip()
        text = re.sub(r"\s+", " ", str(row.get("text") or "")).strip()
        status = str(row.get("verification_status") or "unverified")
        lines.append(
            f"[{idx}] {title}\n"
            f"URL: {url}\n"
            f"Status: {status}\n"
            f"Evidence: {text[:700]}"
        )
    lines.append("</Evidence Citation Table>")
    return "\n".join(lines)


def citation_markers(report_text: str) -> set[int]:
    markers: set[int] = set()
    for match in _CITATION_MARKER_RE.finditer(str(report_text or "")):
        for value in re.split(r"\s*,\s*", match.group(1)):
            try:
                markers.add(int(value))
            except ValueError:
                continue
    return markers


def evaluate_citation_gate(
    report_text: str,
    *,
    sources: list[dict[str, Any]] | None = None,
    evidence_items: list[dict[str, Any]] | None = None,
    passages: list[dict[str, Any]] | None = None,
    require_evidence: bool = True,
    require_citations: bool = False,
) -> dict[str, Any]:
    """Return deterministic citation-gate status before/alongside LLM judges."""
    markers = sorted(citation_markers(report_text))
    sources = [item for item in (sources or []) if isinstance(item, dict)]
    evidence_items = [item for item in (evidence_items or []) if isinstance(item, dict)]
    passages = [item for item in (passages or []) if isinstance(item, dict)]
    bindings = build_citation_bindings(
        sources=sources,
        evidence_items=evidence_items,
        passages=passages,
        max_items=max(len(sources), len(evidence_items), len(passages), 24),
    )

    missing_markers = [marker for marker in markers if marker < 1 or marker > len(bindings)]
    unresolved_markers: list[int] = []
    passed = True
    verdict = "pass"
    issues: list[str] = []
    suggestions: list[str] = []
    has_evidence = bool(passages or evidence_items or bindings)

    if markers and require_evidence and not has_evidence:
        passed = False
        verdict = "incomplete"
        issues.append("Citations are present, but no source evidence passages were available.")
        suggestions.append("Persist source passages or evidence items before final evaluation.")
    if require_citations and has_evidence and not markers:
        passed = False
        verdict = "incomplete"
        issues.append("The report has current-run evidence but no numbered citations.")
        suggestions.append("Add citations using only the current-run evidence table.")
    if missing_markers:
        passed = False
        verdict = "incomplete"
        issues.append(
            "Citation markers do not map to registered sources: "
            + ", ".join(f"[{marker}]" for marker in missing_markers[:10])
        )
        suggestions.append("Revise citations so every marker maps to a registered source.")

    citation_bindings: list[dict[str, Any]] = []
    for marker in markers:
        if marker < 1 or marker > len(bindings):
            continue
        binding = dict(bindings[marker - 1])
        binding["marker"] = marker
        citation_bindings.append(binding)
        if not binding.get("traceable"):
            unresolved_markers.append(marker)
            passed = False
            verdict = "incomplete"
            issues.append(
                f"Citation marker [{marker}] lacks a traceable source_id + canonical_url + snippet_hash binding."
            )
            suggestions.append(
                "Ensure the cited source is normalized into the evidence ledger before finalizing the report."
            )
        if binding.get("requires_current_run_verification") or str(
            binding.get("source_kind") or ""
        ).lower() == "memory":
            unresolved_markers.append(marker)
            passed = False
            verdict = "incomplete"
            issues.append(
                f"Citation marker [{marker}] points to memory-only source context that was not verified in this run."
            )
            suggestions.append(
                "Re-fetch or verify the remembered source in the current run before citing it."
            )
        elif require_evidence and not binding.get("matched_passage_count"):
            unresolved_markers.append(marker)
            passed = False
            verdict = "incomplete"
            issues.append(
                f"Citation marker [{marker}] could not be matched to a retrievable evidence passage."
            )
            suggestions.append(
                "Persist the source passage or cached excerpt so the citation can be traced end-to-end."
            )

    return {
        "passed": passed,
        "verdict": verdict,
        "score": 1.0 if passed else 0.0,
        "markers": markers,
        "missing_markers": missing_markers,
        "unresolved_markers": unresolved_markers,
        "source_count": len(sources),
        "evidence_count": len(evidence_items),
        "passage_count": len(passages),
        "binding_count": len(bindings),
        "citation_bindings": citation_bindings,
        "issues": issues,
        "suggestions": suggestions,
    }
