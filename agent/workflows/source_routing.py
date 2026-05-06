from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SourceAccessPolicy:
    owner_id: str = ""
    group_id: str = ""
    visibility: str = "private"
    allowed_providers: list[str] = field(default_factory=list)
    denied_domains: list[str] = field(default_factory=list)
    allowed_domains: list[str] = field(default_factory=list)
    mcp_preset_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _compact(asdict(self))


@dataclass
class ResearchSourceCollection:
    id: str
    name: str
    source_type: str = "documents"
    provider: str = "rag"
    access_policy: dict[str, Any] = field(default_factory=dict)
    freshness_days: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return _compact(asdict(self))


@dataclass
class ResearchConnector:
    id: str
    name: str
    connector_type: str
    source_type: str = "documents"
    auth_mode: str = "none"
    access_policy: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return _compact(asdict(self))


@dataclass
class SourceIndexAttempt:
    id: str
    collection_id: str
    connector_id: str = ""
    status: str = "pending"
    document_count: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _compact(asdict(self))


@dataclass
class ResearchSourceRoutingPolicy:
    mode: str = "web_only"
    providers: list[str] = field(default_factory=lambda: ["web"])
    collections: list[ResearchSourceCollection] = field(default_factory=list)
    connectors: list[ResearchConnector] = field(default_factory=list)
    index_attempts: list[SourceIndexAttempt] = field(default_factory=list)
    access_policy: SourceAccessPolicy = field(default_factory=SourceAccessPolicy)
    freshness_requirement: str = ""
    citation_policy: str = "required"
    budget_policy: dict[str, Any] = field(default_factory=dict)
    mcp_governance: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["collections"] = [item.to_dict() for item in self.collections]
        payload["connectors"] = [item.to_dict() for item in self.connectors]
        payload["index_attempts"] = [item.to_dict() for item in self.index_attempts]
        payload["access_policy"] = self.access_policy.to_dict()
        return _compact(payload)


def build_source_routing_policy(
    *,
    brief: Any = None,
    config: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = _configurable(config or {})
    state = state if isinstance(state, dict) else {}
    raw = (
        cfg.get("source_routing")
        or state.get("source_routing")
        or getattr(brief, "source_routing", {})
        or {}
    )
    raw = raw if isinstance(raw, dict) else {}
    mode = _normalize_mode(
        raw.get("mode")
        or raw.get("source_policy")
        or cfg.get("source_policy")
        or getattr(brief, "source_policy", "web"),
        bool(cfg.get("use_rag") or state.get("use_rag")),
    )
    providers = _clean_list(
        raw.get("providers")
        or cfg.get("evidence_providers")
        or cfg.get("source_providers")
    ) or _providers_for_mode(mode)
    collections = _collections_from(
        raw.get("collections") or cfg.get("source_collections") or []
    )
    connectors = _connectors_from(
        raw.get("connectors") or cfg.get("source_connectors") or []
    )
    index_attempts = _index_attempts_from(
        raw.get("index_attempts") or cfg.get("source_index_attempts") or []
    )
    if not collections and (
        "rag" in providers or mode in {"local_docs_only", "hybrid", "private_first"}
    ):
        collection_name = str(
            cfg.get("rag_collection_name")
            or cfg.get("collection_name")
            or "weaver_documents"
        ).strip()
        collections = [
            ResearchSourceCollection(id=collection_name, name=collection_name)
        ]
    access = SourceAccessPolicy(
        owner_id=str(state.get("user_id") or cfg.get("user_id") or "").strip(),
        group_id=str(state.get("group_id") or cfg.get("group_id") or "").strip(),
        visibility=str(
            raw.get("visibility") or state.get("visibility") or "private"
        ).strip()
        or "private",
        allowed_providers=providers,
        denied_domains=_clean_list(
            raw.get("denied_domains") or cfg.get("denied_domains")
        ),
        allowed_domains=_clean_list(
            raw.get("allowed_domains") or cfg.get("allowed_domains")
        ),
        mcp_preset_ids=_clean_list(
            raw.get("mcp_preset_ids") or cfg.get("mcp_preset_ids")
        ),
    )
    return ResearchSourceRoutingPolicy(
        mode=mode,
        providers=providers,
        collections=collections,
        connectors=connectors,
        index_attempts=index_attempts,
        access_policy=access,
        freshness_requirement=str(
            raw.get("freshness_requirement")
            or getattr(brief, "freshness_requirement", "")
            or ""
        ).strip(),
        citation_policy=str(
            raw.get("citation_policy")
            or getattr(brief, "citation_policy", "required")
            or "required"
        ),
        budget_policy=dict(
            raw.get("budget_policy")
            if isinstance(raw.get("budget_policy"), dict)
            else {}
        ),
        mcp_governance=_mcp_governance(_configurable(config or {}), providers, access),
    ).to_dict()


def source_policy_from_routing(source_routing: dict[str, Any]) -> str:
    mode = str((source_routing or {}).get("mode") or "web_only").strip().lower()
    if mode == "web_only":
        return "web"
    if mode == "local_docs_only":
        return "local"
    if mode == "private_first":
        return "private-first"
    if mode == "mcp_only":
        return "mcp"
    return mode


def _normalize_mode(value: Any, use_rag: bool = False) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized in {"web", "web_only", "webonly"}:
        return "hybrid" if use_rag else "web_only"
    if normalized in {"rag", "local", "local_docs", "local_docs_only", "private"}:
        return "local_docs_only"
    if normalized in {"private_first", "privatefirst"}:
        return "private_first"
    if normalized in {"hybrid", "web_rag", "rag_web"}:
        return "hybrid"
    if normalized in {"mcp", "mcp_only"}:
        return "mcp_only"
    return "hybrid" if use_rag else "web_only"


def _providers_for_mode(mode: str) -> list[str]:
    if mode == "local_docs_only":
        return ["rag"]
    if mode == "private_first":
        return ["rag", "web"]
    if mode == "hybrid":
        return ["web", "rag"]
    if mode == "mcp_only":
        return ["mcp"]
    return ["web"]


def _configurable(config: dict[str, Any]) -> dict[str, Any]:
    cfg = config.get("configurable") if isinstance(config, dict) else {}
    return cfg if isinstance(cfg, dict) else {}


def _clean_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",")]
    if not isinstance(value, list):
        return []
    output: list[str] = []
    seen = set()
    for item in value:
        text = str(item or "").strip()
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            output.append(text)
    return output


def _collections_from(value: Any) -> list[ResearchSourceCollection]:
    if not isinstance(value, list):
        return []
    collections: list[ResearchSourceCollection] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            collections.append(
                ResearchSourceCollection(id=item.strip(), name=item.strip())
            )
        elif isinstance(item, dict):
            collection_id = str(item.get("id") or item.get("name") or "").strip()
            if collection_id:
                collections.append(
                    ResearchSourceCollection(
                        id=collection_id,
                        name=str(item.get("name") or collection_id),
                        source_type=str(item.get("source_type") or "documents"),
                        provider=str(item.get("provider") or "rag"),
                        access_policy=dict(
                            item.get("access_policy")
                            if isinstance(item.get("access_policy"), dict)
                            else {}
                        ),
                        freshness_days=(
                            item.get("freshness_days")
                            if isinstance(item.get("freshness_days"), int)
                            else None
                        ),
                    )
                )
    return collections


def _connectors_from(value: Any) -> list[ResearchConnector]:
    if not isinstance(value, list):
        return []
    connectors: list[ResearchConnector] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        connector_id = str(item.get("id") or item.get("name") or "").strip()
        connector_type = str(
            item.get("connector_type") or item.get("type") or ""
        ).strip()
        if not connector_id or not connector_type:
            continue
        connectors.append(
            ResearchConnector(
                id=connector_id,
                name=str(item.get("name") or connector_id),
                connector_type=connector_type,
                source_type=str(item.get("source_type") or "documents"),
                auth_mode=str(item.get("auth_mode") or "none"),
                access_policy=dict(
                    item.get("access_policy")
                    if isinstance(item.get("access_policy"), dict)
                    else {}
                ),
                enabled=bool(item.get("enabled", True)),
            )
        )
    return connectors


def _index_attempts_from(value: Any) -> list[SourceIndexAttempt]:
    if not isinstance(value, list):
        return []
    attempts: list[SourceIndexAttempt] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        attempt_id = str(item.get("id") or "").strip()
        collection_id = str(item.get("collection_id") or "").strip()
        if not attempt_id or not collection_id:
            continue
        attempts.append(
            SourceIndexAttempt(
                id=attempt_id,
                collection_id=collection_id,
                connector_id=str(item.get("connector_id") or ""),
                status=str(item.get("status") or "pending"),
                document_count=int(item.get("document_count") or 0),
                error=str(item.get("error") or ""),
            )
        )
    return attempts


def _mcp_governance(
    cfg: dict[str, Any], providers: list[str], access: SourceAccessPolicy
) -> dict[str, Any]:
    if "mcp" not in {provider.lower() for provider in providers}:
        return {"enabled": False}
    return {
        "enabled": True,
        "auth_required": bool(
            cfg.get("mcp_auth_required") or cfg.get("mcp_requires_auth")
        ),
        "tool_whitelist": _clean_list(
            cfg.get("mcp_tools_to_include") or cfg.get("mcp_tool_whitelist")
        ),
        "preset_ids": access.mcp_preset_ids,
        "audit_required": True,
    }


def _compact(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in payload.items() if value not in (None, "", [], {})
    }
