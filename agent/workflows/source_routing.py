from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

_ALLOWED_SOURCE_PROVIDERS = {"web", "academic", "mcp", "rag"}
_MODE_PROVIDERS = {
    "web_only": ["web"],
    "academic_only": ["academic"],
    "mcp_only": ["mcp"],
    "rag_only": ["rag"],
    "hybrid": ["web", "academic", "rag", "mcp"],
    "hybrid_private_web": ["web", "academic", "rag", "mcp"],
}


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
class ResearchSourceRoutingPolicy:
    mode: str = "web_only"
    providers: list[str] = field(default_factory=lambda: ["web"])
    connectors: list[ResearchConnector] = field(default_factory=list)
    access_policy: SourceAccessPolicy = field(default_factory=SourceAccessPolicy)
    freshness_requirement: str = ""
    citation_policy: str = "required"
    budget_policy: dict[str, Any] = field(default_factory=dict)
    mcp_governance: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 2

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["connectors"] = [item.to_dict() for item in self.connectors]
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
    )
    providers = _clean_list(
        raw.get("providers")
        or cfg.get("evidence_providers")
        or cfg.get("source_providers")
    ) or _providers_for_mode(mode)
    providers = _allowed_providers(providers)
    if not providers:
        providers = _providers_for_mode(mode)
    connectors = _connectors_from(
        raw.get("connectors") or cfg.get("source_connectors") or []
    )
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
        connectors=connectors,
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
        budget_policy=_budget_policy(raw.get("budget_policy"), cfg, mode),
        mcp_governance=_mcp_governance(_configurable(config or {}), providers, access),
    ).to_dict()


def source_policy_from_routing(source_routing: dict[str, Any]) -> str:
    mode = str((source_routing or {}).get("mode") or "web_only").strip().lower()
    if mode == "web_only":
        return "web"
    if mode == "academic_only":
        return "academic"
    if mode == "mcp_only":
        return "mcp"
    if mode == "rag_only":
        return "rag"
    if mode in {"hybrid", "hybrid_private_web"}:
        return "hybrid"
    return "web"


def _normalize_mode(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized in {"web", "web_only", "webonly", "public_web"}:
        return "web_only"
    if normalized in {"academic", "academic_only", "academiconly", "scholar"}:
        return "academic_only"
    if normalized in {"mcp", "mcp_only", "mcponly"}:
        return "mcp_only"
    if normalized in {"rag", "rag_only", "ragonly", "private", "private_only"}:
        return "rag_only"
    if normalized in {"hybrid", "all", "mixed", "auto"}:
        return "hybrid"
    if normalized in {
        "hybrid_private_web",
        "hybrid_private",
        "private_web",
        "web_private",
    }:
        return "hybrid_private_web"
    return "web_only"


def _providers_for_mode(mode: str) -> list[str]:
    return list(_MODE_PROVIDERS.get(mode, ["web"]))


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


def _allowed_providers(providers: list[str]) -> list[str]:
    output: list[str] = []
    seen = set()
    for provider in providers:
        key = str(provider or "").strip().lower()
        if key in _ALLOWED_SOURCE_PROVIDERS and key not in seen:
            seen.add(key)
            output.append(key)
    return output


def _budget_policy(value: Any, cfg: dict[str, Any], mode: str) -> dict[str, Any]:
    policy = dict(value) if isinstance(value, dict) else {}
    for key in (
        "web",
        "academic",
        "mcp",
        "rag",
        "max_sources",
        "min_sources",
        "max_tool_calls",
        "max_searches",
    ):
        cfg_key = f"source_budget_{key}"
        if key not in policy and cfg.get(cfg_key) is not None:
            policy[key] = cfg.get(cfg_key)
    policy.setdefault("mode", mode)
    return policy


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
