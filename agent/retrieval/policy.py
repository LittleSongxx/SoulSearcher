from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SOURCE_ORIGINS = {"public_web", "private_corpus", "user_provided", "external_system"}
ACCESS_CHANNELS = {
    "search_api",
    "browser",
    "crawler",
    "file_upload",
    "mcp",
    "native_connector",
}
RETRIEVAL_METHODS = {
    "web_search",
    "academic_search",
    "vector_search",
    "keyword_search",
    "crawl",
    "deep_read",
    "mcp_search",
    "mcp_fetch",
}
PROFILES = {"general", "academic", "news", "social", "code", "finance", "legal"}
LEGACY_MODES = {
    "web_only",
    "academic_only",
    "rag_only",
    "mcp_only",
    "hybrid",
    "hybrid_private_web",
}


class LegacySourceRoutingError(ValueError):
    """Raised when a caller sends deprecated source_routing fields."""


@dataclass(frozen=True)
class DomainPolicy:
    allowed_domains: list[str] = field(default_factory=list)
    denied_domains: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _compact(asdict(self))


@dataclass(frozen=True)
class CorpusPolicy:
    user_id: str = ""
    include_user_library: bool = True
    document_ids: list[str] = field(default_factory=list)
    max_private_results: int = 5

    def to_dict(self) -> dict[str, Any]:
        return _compact(asdict(self))


@dataclass(frozen=True)
class ConnectorPolicy:
    mcp_tool_whitelist: list[str] = field(default_factory=list)
    mcp_max_tools: int = 0
    mcp_read_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        return _compact(asdict(self))


@dataclass(frozen=True)
class RetrievalBudget:
    max_results: int = 8
    max_public_results: int = 6
    max_private_results: int = 5
    max_external_results: int = 4
    max_read_chars: int = 12000

    def to_dict(self) -> dict[str, Any]:
        return _compact(asdict(self))


@dataclass(frozen=True)
class RetrievalPolicy:
    allowed_origins: list[str] = field(default_factory=lambda: ["public_web"])
    channels: list[str] = field(default_factory=lambda: ["search_api", "crawler"])
    methods: list[str] = field(default_factory=lambda: ["web_search", "crawl", "deep_read"])
    profiles: list[str] = field(default_factory=lambda: ["general"])
    domain_policy: DomainPolicy = field(default_factory=DomainPolicy)
    corpus_policy: CorpusPolicy = field(default_factory=CorpusPolicy)
    connector_policy: ConnectorPolicy = field(default_factory=ConnectorPolicy)
    budget: RetrievalBudget = field(default_factory=RetrievalBudget)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["domain_policy"] = self.domain_policy.to_dict()
        payload["corpus_policy"] = self.corpus_policy.to_dict()
        payload["connector_policy"] = self.connector_policy.to_dict()
        payload["budget"] = self.budget.to_dict()
        return _compact(payload)


def reject_legacy_source_routing(value: Any) -> None:
    """Reject source_routing modes at API/runtime boundaries."""
    if not isinstance(value, dict) or not value:
        return
    mode = str(value.get("mode") or value.get("source_policy") or "").strip().lower()
    if mode in LEGACY_MODES or "providers" in value:
        raise LegacySourceRoutingError(
            "source_routing is no longer accepted. Use retrieval_policy with "
            "allowed_origins/channels/methods/profiles."
        )


def build_retrieval_policy(
    raw: Any = None,
    *,
    user_id: str = "",
    config: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = _configurable(config or {})
    state = state if isinstance(state, dict) else {}
    raw_policy = raw
    if raw_policy is None:
        raw_policy = cfg.get("retrieval_policy") or state.get("retrieval_policy") or {}
    raw_policy = raw_policy if isinstance(raw_policy, dict) else {}

    reject_legacy_source_routing(cfg.get("source_routing"))
    reject_legacy_source_routing(state.get("source_routing"))
    reject_legacy_source_routing(raw_policy)
    reject_legacy_source_routing(raw_policy.get("source_routing"))
    schema_version = raw_policy.get("schema_version")
    if schema_version not in (None, ""):
        raise LegacySourceRoutingError(
            "Versioned retrieval/source routing schemas are no longer accepted. "
            "Use retrieval_policy with allowed_origins/channels/methods/profiles."
        )

    allowed_origins = _enum_list(
        raw_policy.get("allowed_origins") or cfg.get("retrieval_allowed_origins"),
        SOURCE_ORIGINS,
    )
    channels = _enum_list(
        raw_policy.get("channels") or cfg.get("retrieval_channels"),
        ACCESS_CHANNELS,
    )
    methods = _enum_list(
        raw_policy.get("methods") or cfg.get("retrieval_methods"),
        RETRIEVAL_METHODS,
    )
    profiles = _enum_list(
        raw_policy.get("profiles") or cfg.get("retrieval_profiles"),
        PROFILES,
    )

    if not allowed_origins:
        allowed_origins = ["public_web"]
    if not channels:
        channels = ["search_api", "crawler"]
    if not methods:
        methods = ["web_search", "crawl", "deep_read"]
    if not profiles:
        profiles = ["general"]

    if "academic" in profiles and "academic_search" not in methods:
        methods.append("academic_search")
    if "private_corpus" in allowed_origins:
        if "file_upload" not in channels:
            channels.append("file_upload")
        for method in ("vector_search", "keyword_search"):
            if method not in methods:
                methods.append(method)
    if "external_system" in allowed_origins:
        if "mcp" not in channels:
            channels.append("mcp")
        if "mcp_search" not in methods:
            methods.append("mcp_search")

    domain_raw = raw_policy.get("domain_policy") if isinstance(raw_policy.get("domain_policy"), dict) else {}
    corpus_raw = raw_policy.get("corpus_policy") if isinstance(raw_policy.get("corpus_policy"), dict) else {}
    connector_raw = (
        raw_policy.get("connector_policy") if isinstance(raw_policy.get("connector_policy"), dict) else {}
    )
    budget_raw = raw_policy.get("budget") if isinstance(raw_policy.get("budget"), dict) else {}
    effective_user_id = str(
        user_id
        or corpus_raw.get("user_id")
        or cfg.get("user_id")
        or state.get("user_id")
        or state.get("_user_id")
        or ""
    ).strip()

    return RetrievalPolicy(
        allowed_origins=allowed_origins,
        channels=channels,
        methods=methods,
        profiles=profiles,
        domain_policy=DomainPolicy(
            allowed_domains=_clean_list(
                domain_raw.get("allowed_domains")
                or raw_policy.get("allowed_domains")
                or cfg.get("allowed_domains")
            ),
            denied_domains=_clean_list(
                domain_raw.get("denied_domains")
                or raw_policy.get("denied_domains")
                or cfg.get("denied_domains")
            ),
        ),
        corpus_policy=CorpusPolicy(
            user_id=effective_user_id,
            include_user_library=bool(corpus_raw.get("include_user_library", True)),
            document_ids=_clean_list(corpus_raw.get("document_ids")),
            max_private_results=_int_value(
                corpus_raw.get("max_private_results"),
                _int_value(budget_raw.get("max_private_results"), 5),
            ),
        ),
        connector_policy=ConnectorPolicy(
            mcp_tool_whitelist=_clean_list(
                connector_raw.get("mcp_tool_whitelist") or cfg.get("mcp_tool_whitelist")
            ),
            mcp_max_tools=_int_value(
                connector_raw.get("mcp_max_tools") or cfg.get("mcp_max_tools"),
                0,
            ),
            mcp_read_only=bool(connector_raw.get("mcp_read_only", True)),
        ),
        budget=RetrievalBudget(
            max_results=_int_value(budget_raw.get("max_results"), 8),
            max_public_results=_int_value(budget_raw.get("max_public_results"), 6),
            max_private_results=_int_value(budget_raw.get("max_private_results"), 5),
            max_external_results=_int_value(budget_raw.get("max_external_results"), 4),
            max_read_chars=_int_value(budget_raw.get("max_read_chars"), 12000),
        ),
    ).to_dict()


def source_allowed(policy: dict[str, Any], origin: str, channel: str, method: str) -> bool:
    origins = set(_clean_list(policy.get("allowed_origins")))
    channels = set(_clean_list(policy.get("channels")))
    methods = set(_clean_list(policy.get("methods")))
    return (
        str(origin).strip() in origins
        and str(channel).strip() in channels
        and str(method).strip() in methods
    )


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
            output.append(key)
    return output


def _enum_list(value: Any, allowed: set[str]) -> list[str]:
    return [item for item in _clean_list(value) if item in allowed]


def _int_value(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, int(default))


def _compact(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if item not in (None, "", [], {})
    }
