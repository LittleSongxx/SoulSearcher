from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from agent.workflows.evidence import build_evidence_items
from agent.workflows.research_brief import ResearchBrief


SearchFunc = Callable[[str, int, Dict[str, Any], Optional[List[str]]], List[Dict[str, Any]]]


@dataclass
class ProviderSearchResult:
    provider: str
    query: str
    results: List[Dict[str, Any]] = field(default_factory=list)
    evidence_items: List[Dict[str, Any]] = field(default_factory=list)
    error: str = ""


@dataclass
class ProviderCapability:
    name: str
    web_search: bool = False
    native_web_search: bool = False
    raw_content: bool = False
    academic: bool = False
    local_docs: bool = False
    mcp: bool = False
    requires_auth: bool = False
    tool_whitelist: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "web_search": self.web_search,
            "native_web_search": self.native_web_search,
            "raw_content": self.raw_content,
            "academic": self.academic,
            "local_docs": self.local_docs,
            "mcp": self.mcp,
            "requires_auth": self.requires_auth,
            "tool_whitelist": self.tool_whitelist,
        }


class WebEvidenceProvider:
    name = "web"

    def __init__(self, search_func: SearchFunc, provider_profile: Optional[List[str]] = None):
        self.search_func = search_func
        self.provider_profile = provider_profile

    def search(self, query: str, max_results: int, config: Dict[str, Any]) -> ProviderSearchResult:
        try:
            results = self.search_func(query, max_results, config, self.provider_profile)
            results = results if isinstance(results, list) else []
            return ProviderSearchResult(
                provider=self.name,
                query=query,
                results=results,
                evidence_items=build_evidence_items(search_runs=[{"query": query, "results": results}]),
            )
        except Exception as exc:
            return ProviderSearchResult(provider=self.name, query=query, error=str(exc))


class RAGEvidenceProvider:
    name = "rag"

    def __init__(self, collection_name: Optional[str] = None):
        self.collection_name = collection_name

    def search(self, query: str, max_results: int, config: Dict[str, Any]) -> ProviderSearchResult:
        try:
            from tools.rag.rag_tool import get_rag_tool

            rag = get_rag_tool(collection_name=self.collection_name)
            if rag is None:
                return ProviderSearchResult(provider=self.name, query=query, error="rag_not_enabled")
            results = rag.search(query, n_results=max_results)
            results = results if isinstance(results, list) else []
            enriched = [dict(item, query=query) for item in results if isinstance(item, dict)]
            return ProviderSearchResult(
                provider=self.name,
                query=query,
                results=enriched,
                evidence_items=build_evidence_items(rag_results=enriched),
            )
        except Exception as exc:
            return ProviderSearchResult(provider=self.name, query=query, error=str(exc))


class MCPEvidenceProvider:
    name = "mcp"

    def search(self, query: str, max_results: int, config: Dict[str, Any]) -> ProviderSearchResult:
        cfg = _configurable(config)
        if bool(cfg.get("mcp_auth_required") or cfg.get("mcp_requires_auth")):
            return ProviderSearchResult(provider=self.name, query=query, error="mcp_auth_required")
        raw_results = cfg.get("mcp_evidence_results") or cfg.get("mcp_results") or []
        if isinstance(raw_results, dict):
            raw_results = raw_results.get(query) or raw_results.get("results") or []
        if not isinstance(raw_results, list):
            raw_results = []
        allowed_tools = _mcp_tools_to_include(cfg)
        results: List[Dict[str, Any]] = []
        evidence_items: List[Dict[str, Any]] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            tool_name = _mcp_tool_name(item)
            if allowed_tools and tool_name and tool_name not in allowed_tools:
                continue
            enriched = dict(item)
            enriched.setdefault("provider", self.name)
            enriched.setdefault("source_type", "mcp")
            enriched.setdefault("query", query)
            enriched.setdefault("tool", tool_name)
            results.append(enriched)
            evidence_items.append(_mcp_evidence_item(enriched, query=query))
            if len(results) >= max(1, int(max_results or 1)):
                break
        if not results:
            return ProviderSearchResult(provider=self.name, query=query, error="mcp_evidence_provider_not_configured")
        return ProviderSearchResult(provider=self.name, query=query, results=results, evidence_items=evidence_items)


def _configurable(config: Dict[str, Any]) -> Dict[str, Any]:
    cfg = config.get("configurable") if isinstance(config, dict) else {}
    return cfg if isinstance(cfg, dict) else {}


def _rag_collection_from_config(config: Dict[str, Any]) -> Optional[str]:
    cfg = _configurable(config)
    value = cfg.get("rag_collection_name") or cfg.get("collection_name")
    return str(value).strip() if value else None


def _mcp_tools_to_include(cfg: Dict[str, Any]) -> List[str]:
    value = cfg.get("mcp_tools_to_include") or cfg.get("mcp_tool_whitelist") or []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def _mcp_tool_name(item: Dict[str, Any]) -> str:
    return str(item.get("tool") or item.get("tool_name") or item.get("name") or "").strip()


def _mcp_evidence_item(item: Dict[str, Any], *, query: str) -> Dict[str, Any]:
    tool_name = _mcp_tool_name(item)
    title = str(item.get("title") or tool_name or "MCP evidence").strip()
    snippet = str(item.get("snippet") or item.get("content") or item.get("text") or item.get("result") or "").strip()
    document_id = str(item.get("document_id") or item.get("id") or tool_name).strip()
    raw = "|".join([tool_name, title, snippet[:200], query])
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return {
        "id": f"ev_mcp_{digest}",
        "source_type": "mcp",
        "provider": "mcp",
        "document_id": document_id,
        "title": title,
        "snippet": snippet,
        "retrieved_at": str(item.get("retrieved_at") or datetime.now(timezone.utc).isoformat()),
        "query": query,
        "metadata": {
            key: value
            for key, value in item.items()
            if key not in {"id", "document_id", "title", "snippet", "content", "text", "result", "retrieved_at"}
        },
    }


def select_provider_names(brief: ResearchBrief, config: Dict[str, Any]) -> List[str]:
    cfg = _configurable(config)
    override = cfg.get("evidence_providers") or cfg.get("source_providers")
    if isinstance(override, str) and override.strip():
        names = [part.strip().lower() for part in override.split(",") if part.strip()]
        return names or ["web"]
    if isinstance(override, list):
        names = [str(part).strip().lower() for part in override if str(part).strip()]
        return names or ["web"]
    policy = (brief.source_policy or "web").strip().lower()
    if policy in {"rag", "local"}:
        return ["rag"]
    if policy in {"hybrid", "private-first"}:
        return ["rag", "web"] if policy == "private-first" else ["web", "rag"]
    if bool(cfg.get("use_rag")):
        return ["web", "rag"]
    return ["web"]


def build_evidence_providers(
    *,
    brief: ResearchBrief,
    config: Dict[str, Any],
    search_func: SearchFunc,
    provider_profile: Optional[List[str]] = None,
) -> List[Any]:
    providers: List[Any] = []
    for name in select_provider_names(brief, config):
        if name == "web":
            providers.append(WebEvidenceProvider(search_func=search_func, provider_profile=provider_profile))
        elif name in {"rag", "local"}:
            providers.append(RAGEvidenceProvider(collection_name=_rag_collection_from_config(config)))
        elif name == "mcp":
            providers.append(MCPEvidenceProvider())
    return providers or [WebEvidenceProvider(search_func=search_func, provider_profile=provider_profile)]


def provider_capability(provider: Any, config: Optional[Dict[str, Any]] = None) -> ProviderCapability:
    name = str(getattr(provider, "name", "") or "").strip().lower()
    cfg = _configurable(config or {})
    if name == "web":
        native_search = bool(cfg.get("native_web_search") or cfg.get("deepsearch_native_web_search"))
        return ProviderCapability(name=name, web_search=True, native_web_search=native_search)
    if name == "rag":
        return ProviderCapability(name=name, local_docs=True, raw_content=True)
    if name == "mcp":
        return ProviderCapability(
            name=name,
            mcp=True,
            requires_auth=bool(cfg.get("mcp_auth_required") or cfg.get("mcp_requires_auth")),
            tool_whitelist=_mcp_tools_to_include(cfg),
        )
    return ProviderCapability(name=name or "unknown")


def build_provider_capability_artifact(
    providers: List[Any],
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    capabilities = [provider_capability(provider, config).to_dict() for provider in providers or []]
    return {
        "schema_version": 1,
        "provider_count": len(capabilities),
        "providers": capabilities,
    }


def search_with_evidence_providers(
    *,
    providers: List[Any],
    query: str,
    max_results: int,
    config: Dict[str, Any],
) -> List[ProviderSearchResult]:
    outputs: List[ProviderSearchResult] = []
    for provider in providers:
        outputs.append(provider.search(query, max_results, config))
    return outputs


def merge_provider_results(outputs: List[ProviderSearchResult]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    for output in outputs:
        for item in output.results:
            if not isinstance(item, dict):
                continue
            enriched = dict(item)
            enriched.setdefault("provider", output.provider)
            if output.provider == "rag":
                enriched.setdefault("source_type", "rag")
            merged.append(enriched)
    return merged


def merge_provider_evidence(outputs: List[ProviderSearchResult]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen = set()
    for output in outputs:
        for item in output.evidence_items:
            if not isinstance(item, dict):
                continue
            key = str(item.get("id") or "")
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            merged.append(item)
    return merged
