from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional

from agent.workflows.source_url_utils import canonicalize_source_url


class EvidenceSourceType(str, Enum):
    WEB = "web"
    FETCHED_PAGE = "fetched_page"
    PASSAGE = "passage"
    RAG = "rag"
    MCP = "mcp"
    TREE_FINDING = "tree_finding"
    LEGACY_SOURCE = "legacy_source"


@dataclass
class EvidenceItem:
    id: str
    source_type: str
    provider: str = ""
    url: str = ""
    document_id: str = ""
    title: str = ""
    snippet: str = ""
    content_ref: str = ""
    published_date: str = ""
    retrieved_at: str = ""
    query: str = ""
    quality_score: Optional[float] = None
    freshness_score: Optional[float] = None
    citation_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        return {key: value for key, value in data.items() if value not in (None, "", [], {})}


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(part or "") for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _score(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def from_search_result(result: Dict[str, Any], *, query: str = "", citation_id: str = "") -> EvidenceItem:
    url = canonicalize_source_url(result.get("url")) or _text(result.get("url"))
    title = _text(result.get("title"))
    snippet = _text(result.get("summary") or result.get("snippet") or result.get("content") or result.get("raw_excerpt"))
    provider = _text(result.get("provider") or result.get("source") or "web")
    published_date = _text(
        result.get("published_date")
        or result.get("publishedDate")
        or result.get("datePublished")
        or result.get("publishedAt")
        or result.get("published_at")
        or result.get("date_published")
        or result.get("pubDate")
        or result.get("displayDate")
        or result.get("date")
    )
    return EvidenceItem(
        id=_stable_id("ev_web", url, title, snippet[:200], query),
        source_type=EvidenceSourceType.WEB.value,
        provider=provider,
        url=url,
        title=title,
        snippet=snippet,
        published_date=published_date,
        retrieved_at=_text(result.get("retrieved_at")) or _now(),
        query=query,
        quality_score=_score(result.get("score")),
        citation_id=citation_id,
        metadata={key: value for key, value in result.items() if key not in {"title", "url", "summary", "snippet", "content", "raw_excerpt", "provider", "source", "published_date", "publishedDate", "datePublished", "publishedAt", "published_at", "date_published", "pubDate", "displayDate", "date", "retrieved_at", "score"}},
    )


def from_source(source: Dict[str, Any], *, citation_id: str = "") -> EvidenceItem:
    url = canonicalize_source_url(source.get("url")) or _text(source.get("url"))
    title = _text(source.get("title") or source.get("name"))
    snippet = _text(source.get("snippet") or source.get("summary") or source.get("content"))
    return EvidenceItem(
        id=_stable_id("ev_src", url, title, citation_id),
        source_type=EvidenceSourceType.LEGACY_SOURCE.value,
        provider=_text(source.get("provider") or source.get("source")),
        url=url,
        title=title,
        snippet=snippet,
        retrieved_at=_text(source.get("retrieved_at")) or _now(),
        quality_score=_score(source.get("score")),
        citation_id=citation_id or _text(source.get("citation_id") or source.get("tag")),
        metadata={key: value for key, value in source.items() if key not in {"url", "title", "name", "snippet", "summary", "content", "provider", "source", "retrieved_at", "score", "citation_id", "tag"}},
    )


def from_fetched_page(page: Dict[str, Any]) -> EvidenceItem:
    url = canonicalize_source_url(page.get("url")) or _text(page.get("url"))
    title = _text(page.get("title"))
    snippet = _text(page.get("markdown") or page.get("text"))[:1000]
    return EvidenceItem(
        id=_stable_id("ev_page", url, title),
        source_type=EvidenceSourceType.FETCHED_PAGE.value,
        provider=_text(page.get("method")) or "fetcher",
        url=url,
        title=title,
        snippet=snippet,
        content_ref=_text(page.get("content_ref")),
        retrieved_at=_text(page.get("retrieved_at")) or _now(),
        metadata={key: value for key, value in page.items() if key not in {"url", "title", "markdown", "text", "method", "content_ref", "retrieved_at"}},
    )


def from_passage(passage: Dict[str, Any]) -> EvidenceItem:
    url = canonicalize_source_url(passage.get("url")) or _text(passage.get("url"))
    title = _text(passage.get("page_title") or passage.get("title"))
    snippet = _text(passage.get("text") or passage.get("quote"))
    content_ref = _text(passage.get("snippet_hash") or passage.get("content_ref"))
    return EvidenceItem(
        id=_stable_id("ev_passage", url, content_ref, snippet[:200]),
        source_type=EvidenceSourceType.PASSAGE.value,
        provider=_text(passage.get("method")) or "fetcher",
        url=url,
        title=title,
        snippet=snippet,
        content_ref=content_ref,
        retrieved_at=_text(passage.get("retrieved_at")) or _now(),
        metadata={key: value for key, value in passage.items() if key not in {"url", "page_title", "title", "text", "quote", "snippet_hash", "content_ref", "method", "retrieved_at"}},
    )


def from_rag_result(result: Dict[str, Any], *, query: str = "") -> EvidenceItem:
    document_id = _text(result.get("document_id") or result.get("id") or result.get("chunk_id"))
    filename = _text(result.get("filename") or result.get("source"))
    snippet = _text(result.get("content") or result.get("text") or result.get("snippet"))
    return EvidenceItem(
        id=_stable_id("ev_rag", document_id, filename, snippet[:200], query),
        source_type=EvidenceSourceType.RAG.value,
        provider="rag",
        document_id=document_id,
        title=filename,
        snippet=snippet,
        retrieved_at=_now(),
        query=query,
        quality_score=_score(result.get("score")),
        metadata={key: value for key, value in result.items() if key not in {"document_id", "id", "chunk_id", "filename", "source", "content", "text", "snippet", "score"}},
    )


def from_tree_finding(finding: Dict[str, Any], *, branch_id: str = "", branch_topic: str = "") -> Optional[EvidenceItem]:
    result = finding.get("result") if isinstance(finding.get("result"), dict) else finding
    if not isinstance(result, dict):
        return None
    item = from_search_result(result, query=_text(finding.get("query")))
    item.source_type = EvidenceSourceType.TREE_FINDING.value
    item.metadata.update({"branch_id": branch_id, "branch_topic": branch_topic})
    return item


def dedupe_evidence(items: Iterable[EvidenceItem]) -> List[EvidenceItem]:
    output: List[EvidenceItem] = []
    seen = set()
    for item in items:
        key = item.id
        if item.url:
            key = f"url:{item.source_type}:{item.url}:{item.content_ref}"
        elif item.document_id:
            key = f"doc:{item.document_id}:{item.content_ref}:{item.snippet[:120]}"
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def build_evidence_items(
    *,
    search_runs: Optional[List[Dict[str, Any]]] = None,
    sources: Optional[List[Dict[str, Any]]] = None,
    fetched_pages: Optional[List[Dict[str, Any]]] = None,
    passages: Optional[List[Dict[str, Any]]] = None,
    rag_results: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    items: List[EvidenceItem] = []
    for run in search_runs or []:
        if not isinstance(run, dict):
            continue
        query = _text(run.get("query"))
        results = run.get("results")
        if not isinstance(results, list):
            continue
        for idx, result in enumerate(results, 1):
            if isinstance(result, dict):
                items.append(from_search_result(result, query=query, citation_id=f"S{len(items) + 1}" if not query else f"{query[:12]}#{idx}"))
    for idx, source in enumerate(sources or [], 1):
        if isinstance(source, dict):
            items.append(from_source(source, citation_id=_text(source.get("citation_id")) or f"S{idx}"))
    for page in fetched_pages or []:
        if isinstance(page, dict):
            items.append(from_fetched_page(page))
    for passage in passages or []:
        if isinstance(passage, dict):
            items.append(from_passage(passage))
    for rag_result in rag_results or []:
        if isinstance(rag_result, dict):
            items.append(from_rag_result(rag_result, query=_text(rag_result.get("query"))))
    return [item.to_dict() for item in dedupe_evidence(items)]


def evidence_to_passages(evidence_items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    passages: List[Dict[str, Any]] = []
    for item in evidence_items or []:
        if not isinstance(item, dict):
            continue
        text = _text(item.get("snippet"))
        if not text:
            continue
        passages.append(
            {
                "url": item.get("url") or item.get("document_id") or "",
                "title": item.get("title") or "",
                "text": text,
                "retrieved_at": item.get("retrieved_at") or "",
                "source_type": item.get("source_type") or "",
                "evidence_id": item.get("id") or "",
            }
        )
    return passages
