from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool

from agent.retrieval.documents import DocumentLibraryUnavailable, get_document_library
from agent.retrieval.policy import build_retrieval_policy, source_allowed
from agent.retrieval.types import RetrievalResult, RetrievedPassage, RetrievedSource
from agent.workflows.evidence_ledger import normalize_evidence_item

logger = logging.getLogger(__name__)


async def retrieve_sources(
    query: str,
    max_results: int = 8,
    *,
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Unified Deep Research retrieval gateway."""
    cfg = (config or {}).get("configurable") if isinstance(config, dict) else {}
    cfg = cfg if isinstance(cfg, dict) else {}
    policy = build_retrieval_policy(
        cfg.get("retrieval_policy"),
        user_id=str(cfg.get("user_id") or ""),
        config={"configurable": cfg},
    )
    budget = policy.get("budget") if isinstance(policy.get("budget"), dict) else {}
    limit = max(1, min(int(max_results or budget.get("max_results") or 8), 20))
    warnings: list[str] = []
    sources: list[dict[str, Any]] = []
    passages: list[dict[str, Any]] = []

    if source_allowed(policy, "public_web", "search_api", "web_search"):
        try:
            public_results = await asyncio.to_thread(
                _public_web_search,
                query,
                min(limit, int(budget.get("max_public_results") or 6)),
                policy,
            )
            sources.extend(public_results)
        except Exception as exc:
            logger.warning("[Retrieval] public web search failed: %s", exc)
            warnings.append(f"public_web search failed: {exc}")

    if source_allowed(policy, "public_web", "search_api", "academic_search"):
        try:
            academic_results = await asyncio.to_thread(
                _academic_search,
                query,
                min(limit, int(budget.get("max_public_results") or 6)),
            )
            sources.extend(academic_results)
        except Exception as exc:
            logger.warning("[Retrieval] academic search failed: %s", exc)
            warnings.append(f"academic search failed: {exc}")

    if (
        source_allowed(policy, "private_corpus", "file_upload", "vector_search")
        or source_allowed(policy, "private_corpus", "file_upload", "keyword_search")
    ):
        try:
            private_results = await asyncio.to_thread(
                _private_corpus_search,
                query,
                min(limit, int(budget.get("max_private_results") or 5)),
                policy,
            )
            passages.extend(private_results)
        except DocumentLibraryUnavailable as exc:
            warnings.append(str(exc))
        except Exception as exc:
            logger.warning("[Retrieval] private corpus search failed: %s", exc)
            warnings.append(f"private corpus search failed: {exc}")

    if source_allowed(policy, "user_provided", "native_connector", "keyword_search"):
        user_results = _user_provided_search(query, limit, cfg)
        sources.extend(user_results)

    if source_allowed(policy, "external_system", "mcp", "mcp_search"):
        try:
            mcp_results = await _mcp_search(query, min(limit, int(budget.get("max_external_results") or 4)), cfg)
            sources.extend(mcp_results)
        except Exception as exc:
            logger.warning("[Retrieval] MCP search failed: %s", exc)
            warnings.append(f"MCP search failed: {exc}")

    sources = _dedupe_sources(_filter_domains(sources, policy))[:limit]
    passages = _dedupe_passages(passages)[:limit]
    evidence_items = _evidence_from_results(query, sources, passages)
    result = RetrievalResult(
        query=query,
        policy=policy,
        sources=sources,
        passages=passages,
        evidence_items=evidence_items,
        warnings=warnings,
    )
    return result.to_dict()


async def read_source(
    source_id: str = "",
    url: str = "",
    query: str = "",
    max_chars: int = 12000,
    *,
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    cfg = (config or {}).get("configurable") if isinstance(config, dict) else {}
    cfg = cfg if isinstance(cfg, dict) else {}
    policy = build_retrieval_policy(
        cfg.get("retrieval_policy"),
        user_id=str(cfg.get("user_id") or ""),
        config={"configurable": cfg},
    )
    max_chars = max(1000, min(int(max_chars or 12000), int((policy.get("budget") or {}).get("max_read_chars") or 12000)))

    if source_id.startswith("chk_") and source_allowed(policy, "private_corpus", "file_upload", "deep_read"):
        try:
            chunk = await asyncio.to_thread(
                get_document_library().get_chunk,
                user_id=str((policy.get("corpus_policy") or {}).get("user_id") or cfg.get("user_id") or ""),
                chunk_id=source_id,
            )
            if chunk:
                passage = RetrievedPassage(
                    id=str(chunk.get("chunk_id") or source_id),
                    source_id=str(chunk.get("document_id") or ""),
                    text=str(chunk.get("text") or "")[:max_chars],
                    title=str(chunk.get("filename") or ""),
                    source_origin="private_corpus",
                    access_channel="file_upload",
                    retrieval_method="deep_read",
                    metadata={
                        "document_id": chunk.get("document_id"),
                        "chunk_id": chunk.get("chunk_id"),
                        "user_id": chunk.get("user_id"),
                        "content_hash": chunk.get("content_hash"),
                    },
                ).to_dict()
                return {"passage": passage, "evidence_items": _evidence_from_results(query, [], [passage])}
        except Exception as exc:
            return {"error": f"private source read failed: {exc}"}

    target_url = str(url or "").strip()
    if not target_url and source_id.startswith("http"):
        target_url = source_id
    if target_url and source_allowed(policy, "public_web", "crawler", "crawl"):
        if not _url_allowed(target_url, policy):
            return {"error": "URL blocked by retrieval domain policy."}
        try:
            from tools.crawl.crawler import crawl_url

            crawled = await asyncio.to_thread(crawl_url, target_url, 15)
            text = str(crawled.get("content") or "")[:max_chars]
            source = RetrievedSource(
                id=_source_id(target_url, text),
                title=target_url,
                url=target_url,
                content=text,
                summary=text[:500],
                source_origin="public_web",
                access_channel="crawler",
                retrieval_method="crawl",
                provider="crawler",
            ).to_dict()
            return {"source": source, "evidence_items": _evidence_from_results(query, [source], [])}
        except Exception as exc:
            return {"error": f"crawl failed: {exc}"}

    if target_url and source_allowed(policy, "external_system", "mcp", "mcp_fetch"):
        try:
            result = await _mcp_fetch(target_url, cfg)
            return result
        except Exception as exc:
            return {"error": f"MCP fetch failed: {exc}"}

    return {"error": "No allowed read method matched source_id/url under retrieval_policy."}


def build_retrieval_tools(config: RunnableConfig | None = None) -> list[StructuredTool]:
    async def _retrieve_sources_tool(query: str, max_results: int = 8) -> str:
        result = await retrieve_sources(query=query, max_results=max_results, config=config)
        return json.dumps(result, ensure_ascii=False, default=str)

    async def _read_source_tool(
        source_id: str = "",
        url: str = "",
        query: str = "",
        max_chars: int = 12000,
    ) -> str:
        result = await read_source(
            source_id=source_id,
            url=url,
            query=query,
            max_chars=max_chars,
            config=config,
        )
        return json.dumps(result, ensure_ascii=False, default=str)

    return [
        StructuredTool.from_function(
            coroutine=_retrieve_sources_tool,
            name="retrieve_sources",
            description=(
                "Search all allowed retrieval origins through SoulSearcher's unified "
                "retrieval policy. Returns normalized sources, passages, warnings, "
                "and evidence_items."
            ),
        ),
        StructuredTool.from_function(
            coroutine=_read_source_tool,
            name="read_source",
            description=(
                "Read deeper content for a retrieved source URL or private chunk id, "
                "subject to the active retrieval policy."
            ),
        ),
    ]


def _public_web_search(query: str, limit: int, policy: dict[str, Any]) -> list[dict[str, Any]]:
    from tools.search.multi_search import SearchStrategy, get_search_orchestrator

    strategy = SearchStrategy.FALLBACK
    results = get_search_orchestrator().search(
        query=query,
        max_results=limit,
        strategy=strategy,
        provider_profile=_provider_profile(policy),
    )
    output: list[dict[str, Any]] = []
    for result in results:
        output.append(
            RetrievedSource(
                id=_source_id(result.url, result.snippet),
                title=result.title,
                url=result.url,
                summary=result.snippet,
                content=result.content,
                source_origin="public_web",
                access_channel="search_api",
                retrieval_method="web_search",
                profile=_primary_profile(policy),
                provider=result.provider,
                score=float(result.score or 0.0),
                metadata={"published_date": result.published_date},
            ).to_dict()
        )
    return output


def _academic_search(query: str, limit: int) -> list[dict[str, Any]]:
    from tools.search.academic import ArxivProvider, PubMedProvider, SemanticScholarProvider

    providers = [ArxivProvider(), SemanticScholarProvider(), PubMedProvider()]
    output: list[dict[str, Any]] = []
    for provider in providers:
        if len(output) >= limit:
            break
        try:
            results = provider.search(query, max_results=max(1, limit - len(output)))
        except Exception:
            results = []
        for result in results:
            output.append(
                RetrievedSource(
                    id=_source_id(result.url, result.snippet),
                    title=result.title,
                    url=result.url,
                    summary=result.snippet,
                    content=result.content,
                    source_origin="public_web",
                    access_channel="search_api",
                    retrieval_method="academic_search",
                    profile="academic",
                    provider=result.provider,
                    score=float(result.score or 0.0),
                    metadata=result.raw_data,
                ).to_dict()
            )
            if len(output) >= limit:
                break
    return output


def _private_corpus_search(query: str, limit: int, policy: dict[str, Any]) -> list[dict[str, Any]]:
    corpus = policy.get("corpus_policy") if isinstance(policy.get("corpus_policy"), dict) else {}
    if corpus.get("include_user_library") is False:
        return []
    user_id = str(corpus.get("user_id") or "").strip()
    if not user_id:
        return []
    document_ids = corpus.get("document_ids") if isinstance(corpus.get("document_ids"), list) else []
    rows = get_document_library().search(
        user_id=user_id,
        query=query,
        limit=limit,
        document_ids=[str(item) for item in document_ids],
    )
    passages: list[dict[str, Any]] = []
    for row in rows:
        passages.append(
            RetrievedPassage(
                id=str(row.get("chunk_id") or ""),
                source_id=str(row.get("document_id") or ""),
                text=str(row.get("content") or ""),
                title=str(row.get("title") or ""),
                source_origin="private_corpus",
                access_channel="file_upload",
                retrieval_method=str(row.get("retrieval_method") or "vector_search"),
                score=float(row.get("retrieval_score") or 0.0),
                metadata={
                    "document_id": row.get("document_id"),
                    "chunk_id": row.get("chunk_id"),
                    "user_id": row.get("user_id"),
                    "content_hash": row.get("content_hash"),
                    "retrieval_score": row.get("retrieval_score"),
                    "chunk_index": row.get("chunk_index"),
                },
            ).to_dict()
        )
    return passages


def _user_provided_search(query: str, limit: int, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = []
    for key in ("user_injected_sources", "memory_source_candidates"):
        values = cfg.get(key) if isinstance(cfg.get(key), list) else []
        candidates.extend([item for item in values if isinstance(item, dict)])
    q_tokens = set(_tokens(query))
    scored = []
    for item in candidates:
        text = " ".join(
            str(item.get(key) or "")
            for key in ("title", "name", "summary", "snippet", "note", "url", "source_url")
        )
        overlap = len(q_tokens & set(_tokens(text))) / max(1, len(q_tokens))
        scored.append((overlap, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    output = []
    for score, item in scored[:limit]:
        url = str(item.get("url") or item.get("source_url") or item.get("source") or "")
        content = str(item.get("summary") or item.get("snippet") or item.get("note") or "")
        output.append(
            RetrievedSource(
                id=_source_id(url or str(item.get("title") or ""), content),
                title=str(item.get("title") or item.get("name") or "User provided source"),
                url=url,
                summary=content[:500],
                content=content,
                source_origin="user_provided",
                access_channel="native_connector",
                retrieval_method="keyword_search",
                profile="general",
                provider=str(item.get("source") or "user"),
                score=float(score),
                metadata={
                    "requires_current_run_verification": bool(
                        item.get("requires_current_run_verification", True)
                    )
                },
            ).to_dict()
        )
    return output


async def _mcp_search(query: str, limit: int, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    from tools.mcp import init_mcp_tools

    tools = await init_mcp_tools(enabled=True, policy_config=cfg)
    output: list[dict[str, Any]] = []
    for tool in _read_only_mcp_tools(tools):
        if len(output) >= limit:
            break
        name = str(getattr(tool, "name", "") or "")
        if "search" not in name.lower():
            continue
        try:
            result = await tool.ainvoke({"query": query, "max_results": limit}, {"configurable": cfg})
        except Exception as exc:
            logger.debug("[Retrieval] MCP tool %s failed: %s", name, exc)
            continue
        text = str(result or "")
        urls = re.findall(r"https?://[^\s\])>\"']+", text)
        if urls:
            for url in urls[: max(1, limit - len(output))]:
                output.append(
                    RetrievedSource(
                        id=_source_id(url, text),
                        title=url,
                        url=url,
                        summary=text[:500],
                        content=text[:1500],
                        source_origin="external_system",
                        access_channel="mcp",
                        retrieval_method="mcp_search",
                        provider=name,
                    ).to_dict()
                )
        elif text.strip():
            output.append(
                RetrievedSource(
                    id=_source_id(name, text),
                    title=f"MCP result from {name}",
                    summary=text[:500],
                    content=text[:1500],
                    source_origin="external_system",
                    access_channel="mcp",
                    retrieval_method="mcp_search",
                    provider=name,
                ).to_dict()
            )
    return output[:limit]


async def _mcp_fetch(url: str, cfg: dict[str, Any]) -> dict[str, Any]:
    from tools.mcp import init_mcp_tools

    tools = await init_mcp_tools(enabled=True, policy_config=cfg)
    for tool in _read_only_mcp_tools(tools):
        name = str(getattr(tool, "name", "") or "")
        if "fetch" not in name.lower() and "read" not in name.lower():
            continue
        result = await tool.ainvoke({"url": url}, {"configurable": cfg})
        text = str(result or "")
        source = RetrievedSource(
            id=_source_id(url, text),
            title=url,
            url=url,
            summary=text[:500],
            content=text[:12000],
            source_origin="external_system",
            access_channel="mcp",
            retrieval_method="mcp_fetch",
            provider=name,
        ).to_dict()
        return {"source": source, "evidence_items": _evidence_from_results(url, [source], [])}
    return {"error": "No read-only MCP fetch/read tool is available."}


def _read_only_mcp_tools(tools: list[Any]) -> list[Any]:
    denied = ("write", "delete", "create", "update", "patch", "post", "send", "execute", "run", "shell")
    allowed = []
    for tool in tools or []:
        name = str(getattr(tool, "name", "") or "").lower()
        original = str(getattr(tool, "original_name", "") or "").lower()
        joined = f"{name} {original}"
        if any(word in joined for word in denied):
            continue
        if any(word in joined for word in ("search", "fetch", "read", "get", "retrieve")):
            allowed.append(tool)
    return allowed


def _evidence_from_results(
    query: str,
    sources: list[dict[str, Any]],
    passages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for index, item in enumerate(list(sources) + list(passages), 1):
        content = str(item.get("content") or item.get("summary") or item.get("text") or "")
        url = str(item.get("url") or "")
        title = str(item.get("title") or url or item.get("source_id") or "")
        metadata = dict(item.get("metadata") if isinstance(item.get("metadata"), dict) else {})
        for key in ("source_origin", "access_channel", "retrieval_method", "profile", "provider"):
            if item.get(key):
                metadata[key] = item.get(key)
        for key in ("document_id", "chunk_id", "user_id", "content_hash", "retrieval_score"):
            if metadata.get(key) is not None:
                metadata[key] = metadata.get(key)
        normalized = normalize_evidence_item(
            {
                "id": f"retrieval_{hashlib.sha1(f'{query}|{index}|{url}|{content[:80]}'.encode()).hexdigest()[:12]}",
                "title": title,
                "url": url,
                "source": url or title,
                "content": content or title,
                "tool": "retrieve_sources",
                "query": query,
                "score": item.get("score") or item.get("retrieval_score"),
                "metadata": metadata,
            }
        )
        if normalized:
            evidence.append(normalized)
    return evidence


def _filter_domains(sources: list[dict[str, Any]], policy: dict[str, Any]) -> list[dict[str, Any]]:
    return [source for source in sources if _url_allowed(str(source.get("url") or ""), policy)]


def _url_allowed(url: str, policy: dict[str, Any]) -> bool:
    if not url:
        return True
    domain_policy = policy.get("domain_policy") if isinstance(policy.get("domain_policy"), dict) else {}
    allowed = [str(item).lower() for item in domain_policy.get("allowed_domains") or []]
    denied = [str(item).lower() for item in domain_policy.get("denied_domains") or []]
    host = urlparse(url if "://" in url else f"https://{url}").netloc.lower().removeprefix("www.")
    if denied and any(host == d or host.endswith(f".{d}") for d in denied):
        return False
    if allowed and not any(host == d or host.endswith(f".{d}") for d in allowed):
        return False
    return True


def _dedupe_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    seen = set()
    for source in sources:
        key = str(source.get("url") or source.get("id") or source.get("title") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(source)
    return output


def _dedupe_passages(passages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    seen = set()
    for passage in passages:
        key = str(passage.get("id") or passage.get("text") or "")[:180]
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(passage)
    return output


def _source_id(url: str, content: str = "") -> str:
    base = str(url or "") or str(content or "")[:200]
    return f"src_{hashlib.sha1(base.encode('utf-8')).hexdigest()[:12]}"


def _provider_profile(policy: dict[str, Any]) -> list[str]:
    profiles = set(policy.get("profiles") or [])
    if "academic" in profiles:
        return ["semantic_scholar", "arxiv", "pubmed", "tavily"]
    if "news" in profiles:
        return ["tavily", "serper", "brave"]
    return []


def _primary_profile(policy: dict[str, Any]) -> str:
    profiles = policy.get("profiles") if isinstance(policy.get("profiles"), list) else []
    return str(profiles[0]) if profiles else "general"


def _tokens(value: str) -> list[str]:
    return [token for token in re.split(r"\W+", str(value or "").lower()) if len(token) > 1]
