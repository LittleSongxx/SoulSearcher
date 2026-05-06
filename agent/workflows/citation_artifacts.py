from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Optional

from agent.workflows.source_url_utils import canonicalize_source_url

_CITATION_RE = re.compile(r"\[(S\d+|\d+)\]")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(part or "") for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _parse_dt(value: Any) -> Optional[datetime]:
    text = _text(value)
    if not text:
        return None
    try:
        normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        return None


def _evidence_lookup(evidence_items: Iterable[dict[str, Any]]) -> dict[str, list[str]]:
    lookup: dict[str, list[str]] = {}
    for item in evidence_items or []:
        if not isinstance(item, dict):
            continue
        evidence_id = _text(item.get("id"))
        if not evidence_id:
            continue
        keys = []
        url = canonicalize_source_url(item.get("url")) or _text(item.get("url"))
        citation_id = _text(item.get("citation_id"))
        document_id = _text(item.get("document_id"))
        if url:
            keys.append(f"url:{url}")
        if citation_id:
            keys.append(f"citation:{citation_id}")
        if document_id:
            keys.append(f"document:{document_id}")
        for key in keys:
            lookup.setdefault(key, [])
            if evidence_id not in lookup[key]:
                lookup[key].append(evidence_id)
    return lookup


def build_citation_annotations(
    *,
    report: str,
    sources: list[dict[str, Any]],
    evidence_items: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    source_by_marker: dict[str, dict[str, Any]] = {}
    for idx, source in enumerate(sources or [], 1):
        if not isinstance(source, dict):
            continue
        source_by_marker[str(idx)] = source
        citation_id = _text(source.get("citation_id") or source.get("tag"))
        if citation_id:
            source_by_marker[citation_id] = source

    evidence_by_key = _evidence_lookup(evidence_items or [])
    reference_start = (report or "").find("## 参考来源（自动生成）")
    annotations: list[dict[str, Any]] = []
    for occurrence, match in enumerate(_CITATION_RE.finditer(report or ""), 1):
        marker_key = match.group(1)
        source = source_by_marker.get(marker_key)
        if source is None and marker_key.startswith("S"):
            source = source_by_marker.get(marker_key[1:])
        if not isinstance(source, dict):
            continue
        url = canonicalize_source_url(source.get("url")) or _text(source.get("url"))
        raw_url = _text(source.get("rawUrl"))
        citation_id = marker_key
        source_index = int(marker_key) if marker_key.isdigit() else None
        evidence_ids = []
        if url:
            evidence_ids.extend(evidence_by_key.get(f"url:{url}", []))
        evidence_ids.extend(evidence_by_key.get(f"citation:{citation_id}", []))
        annotations.append(
            {
                "id": _stable_id("cite", marker_key, match.start(), url or raw_url),
                "citation_id": citation_id,
                "marker": match.group(0),
                "source_index": source_index,
                "start_char": match.start(),
                "end_char": match.end(),
                "section": "references" if reference_start >= 0 and match.start() >= reference_start else "body",
                "title": _text(source.get("title") or source.get("name")),
                "url": url,
                "rawUrl": raw_url or None,
                "domain": _text(source.get("domain")) or None,
                "provider": _text(source.get("provider")) or None,
                "publishedDate": _text(source.get("publishedDate") or source.get("published_date")) or None,
                "evidence_ids": evidence_ids,
                "occurrence": occurrence,
            }
        )
    return annotations


def _append_event(events: list[dict[str, Any]], event: dict[str, Any]) -> None:
    event = {key: value for key, value in event.items() if value not in (None, "", [], {})}
    event.setdefault("id", _stable_id("tl", event.get("event_type"), event.get("timestamp"), event.get("title"), len(events)))
    event.setdefault("order", len(events) + 1)
    events.append(event)


def build_timeline_artifacts(
    *,
    search_runs: Optional[list[dict[str, Any]]] = None,
    sources: Optional[list[dict[str, Any]]] = None,
    evidence_items: Optional[list[dict[str, Any]]] = None,
    quality_gates: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for run_idx, run in enumerate(search_runs or [], 1):
        if not isinstance(run, dict):
            continue
        results = run.get("results") if isinstance(run.get("results"), list) else []
        providers = sorted(
            {
                _text(result.get("provider") or result.get("source_type"))
                for result in results
                if isinstance(result, dict) and _text(result.get("provider") or result.get("source_type"))
            }
        )
        _append_event(
            events,
            {
                "event_type": "search",
                "timestamp": _text(run.get("timestamp")),
                "title": f"Search: {_text(run.get('query'))}" if _text(run.get("query")) else "Search",
                "query": _text(run.get("query")),
                "result_count": len(results),
                "providers": providers,
                "run_index": run_idx,
            },
        )

    for idx, source in enumerate(sources or [], 1):
        if not isinstance(source, dict):
            continue
        url = canonicalize_source_url(source.get("url")) or _text(source.get("url"))
        _append_event(
            events,
            {
                "event_type": "source",
                "title": _text(source.get("title") or source.get("name")) or "Source",
                "url": url,
                "rawUrl": _text(source.get("rawUrl")) or None,
                "provider": _text(source.get("provider")) or None,
                "publishedDate": _text(source.get("publishedDate") or source.get("published_date")) or None,
                "citation_id": _text(source.get("citation_id")) or str(idx),
                "source_index": idx,
            },
        )

    for item in evidence_items or []:
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title")) or _text(item.get("document_id")) or "Evidence"
        _append_event(
            events,
            {
                "event_type": "evidence",
                "timestamp": _text(item.get("retrieved_at")),
                "title": title,
                "evidence_id": _text(item.get("id")),
                "source_type": _text(item.get("source_type")),
                "provider": _text(item.get("provider")) or None,
                "url": canonicalize_source_url(item.get("url")) or _text(item.get("url")) or None,
                "document_id": _text(item.get("document_id")) or None,
                "citation_id": _text(item.get("citation_id")) or None,
            },
        )

    for gate in quality_gates or []:
        if not isinstance(gate, dict):
            continue
        gate_results = gate.get("gates") if isinstance(gate.get("gates"), list) else []
        failed = [g for g in gate_results if isinstance(g, dict) and not bool(g.get("passed"))]
        _append_event(
            events,
            {
                "event_type": "quality_gate",
                "title": f"Quality gate: {_text(gate.get('stage')) or 'stage'}",
                "stage": _text(gate.get("stage")),
                "epoch": gate.get("epoch"),
                "gate_count": len(gate_results),
                "failed_count": len(failed),
            },
        )

    decorated = []
    for idx, event in enumerate(events):
        parsed = _parse_dt(event.get("timestamp"))
        decorated.append((parsed is None, parsed or datetime.max.replace(tzinfo=UTC), idx, event))
    decorated.sort(key=lambda item: item[:3])
    output = []
    for order, (_, _, _, event) in enumerate(decorated, 1):
        item = dict(event)
        item["order"] = order
        output.append(item)
    return output
