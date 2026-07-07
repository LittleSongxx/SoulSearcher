from dataclasses import dataclass
from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field


class ProviderCircuitSnapshot(BaseModel):
    is_open: bool
    consecutive_failures: int
    opened_for_seconds: Optional[float] = None
    resets_in_seconds: Optional[float] = None
    total_calls: int = 0
    attempted_calls: int = 0
    success_count: int = 0
    failure_count: int = 0
    skipped_open_count: int = 0
    retry_count: int = 0
    circuit_open_count: int = 0
    last_failure: Optional[str] = None
    last_failure_age_seconds: Optional[float] = None


class SearchProviderSnapshot(BaseModel):
    name: str
    available: bool
    healthy: bool
    total_calls: int
    success_count: int
    error_count: int
    success_rate: float
    avg_latency_ms: float
    avg_result_quality: float
    last_error: Optional[str] = None
    last_error_time: Optional[str] = None
    circuit: ProviderCircuitSnapshot


class SearchProvidersResponse(BaseModel):
    providers: list[SearchProviderSnapshot]


class SearchProvidersResetResponse(BaseModel):
    reset: bool


class LLMReliabilityResponse(BaseModel):
    providers: dict[str, Any]


class LLMReliabilityResetResponse(BaseModel):
    reset: bool


class ToolRegistryMostUsed(BaseModel):
    name: str
    call_count: int


class ToolRegistryStats(BaseModel):
    total_tools: int
    enabled_tools: int
    deprecated_tools: int
    total_calls: int
    total_successes: int
    overall_success_rate: float
    most_used: list[ToolRegistryMostUsed]
    by_type: dict[str, int]
    tags: list[str]


class ToolRegistryTool(BaseModel):
    name: str
    description: str = ""
    tool_type: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    return_type: Optional[str] = None
    module_name: str = ""
    class_name: str = ""
    function_name: str = ""
    version: str = "1.0.0"
    tags: list[str] = Field(default_factory=list)
    call_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    last_called: Optional[str] = None
    average_duration_ms: float = 0.0
    enabled: bool = True
    deprecated: bool = False
    deprecation_message: Optional[str] = None
    success_rate: float = 0.0


class ToolRegistryResponse(BaseModel):
    stats: ToolRegistryStats
    tools: list[ToolRegistryTool]


class PublicConfigDefaults(BaseModel):
    port: int
    primary_model: str
    reasoning_model: str


class PublicConfigFeatures(BaseModel):
    mcp_enabled: bool
    prometheus_enabled: bool
    tracing_enabled: bool


class PublicConfigStreamEndpoint(BaseModel):
    protocol: str
    endpoint: str


class PublicConfigStreaming(BaseModel):
    research: PublicConfigStreamEndpoint


class PublicConfigModels(BaseModel):
    default: str
    options: list[str]


class PublicConfigResponse(BaseModel):
    version: str
    defaults: PublicConfigDefaults
    features: PublicConfigFeatures
    streaming: PublicConfigStreaming
    models: PublicConfigModels


class SearchCacheStats(BaseModel):
    size: int
    max_size: int
    hits: int
    similar_hits: int
    misses: int
    sets: int = 0
    evictions: int = 0
    expired: int = 0
    total_requests: int = 0
    hit_rate: float
    capacity_utilization: float = 0.0
    ttl_seconds: float = 0.0
    similarity_threshold: float = 0.0


class SearchCacheStatsResponse(BaseModel):
    stats: SearchCacheStats


class SearchCacheClearResponse(BaseModel):
    cleared: bool


@dataclass(frozen=True)
class AdminRouterDeps:
    settings: Any
    app_version: str
    mcp_enabled: bool
    checkpointer_type: str
    llm_reliability_manager: Any
    get_search_orchestrator: Any


def build_admin_router(deps: AdminRouterDeps) -> APIRouter:
    router = APIRouter()

    @router.get("/api/tools/registry", response_model=ToolRegistryResponse)
    async def get_tool_registry():
        """Return current ToolRegistry stats + tool metadata (best-effort)."""
        from tools.core.registry import get_global_registry

        registry = get_global_registry()
        raw_stats = registry.get_statistics()

        most_used: list[ToolRegistryMostUsed] = []
        for entry in raw_stats.get("most_used", []) or []:
            try:
                name, call_count = entry
                most_used.append(
                    ToolRegistryMostUsed(name=str(name), call_count=int(call_count))
                )
            except Exception:
                continue

        stats = ToolRegistryStats(
            total_tools=int(raw_stats.get("total_tools", 0) or 0),
            enabled_tools=int(raw_stats.get("enabled_tools", 0) or 0),
            deprecated_tools=int(raw_stats.get("deprecated_tools", 0) or 0),
            total_calls=int(raw_stats.get("total_calls", 0) or 0),
            total_successes=int(raw_stats.get("total_successes", 0) or 0),
            overall_success_rate=float(raw_stats.get("overall_success_rate", 0.0) or 0.0),
            most_used=most_used,
            by_type={str(k): int(v) for k, v in (raw_stats.get("by_type") or {}).items()},
            tags=[str(t) for t in (raw_stats.get("tags") or [])],
        )

        tools_payload: list[ToolRegistryTool] = []
        for m in registry.list_metadata():
            try:
                as_dict = m.to_dict()
                as_dict["success_rate"] = float(getattr(m, "success_rate", 0.0) or 0.0)
                tools_payload.append(ToolRegistryTool(**as_dict))
            except Exception:
                continue

        tools_payload.sort(key=lambda t: t.name)
        return {"stats": stats, "tools": tools_payload}

    @router.get("/api/search/providers", response_model=SearchProvidersResponse)
    async def get_search_providers():
        """Expose multi-search provider availability, health, and circuit-breaker state."""
        orchestrator = deps.get_search_orchestrator()
        providers: list[SearchProviderSnapshot] = []

        for provider in orchestrator.providers:
            circuit = orchestrator.reliability_manager.snapshot(provider.name)
            last_error = provider.stats.last_error
            if last_error:
                try:
                    from tools.search.providers import _sanitize_error_message

                    last_error = _sanitize_error_message(last_error)
                except Exception:
                    pass
            circuit_last_failure = circuit.get("last_failure")
            if circuit_last_failure:
                try:
                    from tools.search.providers import _sanitize_error_message

                    circuit_last_failure = _sanitize_error_message(str(circuit_last_failure))
                except Exception:
                    circuit_last_failure = str(circuit_last_failure)
            providers.append(
                SearchProviderSnapshot(
                    name=provider.name,
                    available=bool(provider.is_available()),
                    healthy=bool(provider.stats.is_healthy),
                    total_calls=int(provider.stats.total_calls),
                    success_count=int(provider.stats.success_count),
                    error_count=int(provider.stats.error_count),
                    success_rate=float(provider.stats.success_rate),
                    avg_latency_ms=float(provider.stats.avg_latency_ms),
                    avg_result_quality=float(provider.stats.avg_result_quality),
                    last_error=last_error,
                    last_error_time=provider.stats.last_error_time,
                    circuit=ProviderCircuitSnapshot(
                        is_open=bool(circuit.get("is_open", False)),
                        consecutive_failures=int(circuit.get("consecutive_failures", 0) or 0),
                        opened_for_seconds=circuit.get("opened_for_seconds"),
                        resets_in_seconds=circuit.get("resets_in_seconds"),
                        total_calls=int(circuit.get("total_calls", 0) or 0),
                        attempted_calls=int(circuit.get("attempted_calls", 0) or 0),
                        success_count=int(circuit.get("success_count", 0) or 0),
                        failure_count=int(circuit.get("failure_count", 0) or 0),
                        skipped_open_count=int(circuit.get("skipped_open_count", 0) or 0),
                        retry_count=int(circuit.get("retry_count", 0) or 0),
                        circuit_open_count=int(circuit.get("circuit_open_count", 0) or 0),
                        last_failure=circuit_last_failure,
                        last_failure_age_seconds=circuit.get("last_failure_age_seconds"),
                    ),
                )
            )

        return {"providers": providers}

    @router.post("/api/search/providers/reset", response_model=SearchProvidersResetResponse)
    async def reset_search_providers():
        """Reset the global multi-search orchestrator (provider stats + circuit breaker state)."""
        from tools.search.multi_search import reset_search_orchestrator

        reset_search_orchestrator()
        return {"reset": True}

    @router.get("/api/llm/reliability", response_model=LLMReliabilityResponse)
    async def get_llm_reliability():
        """Expose LLM provider retry and circuit-breaker state."""
        return {"providers": deps.llm_reliability_manager.snapshot()}

    @router.post("/api/llm/reliability/reset", response_model=LLMReliabilityResetResponse)
    async def reset_llm_reliability():
        """Reset LLM provider retry and circuit-breaker state."""
        deps.llm_reliability_manager.reset()
        return {"reset": True}

    @router.get("/api/search/cache/stats", response_model=SearchCacheStatsResponse)
    async def get_search_cache_stats():
        """Return in-memory search cache statistics (LRU + TTL)."""
        from agent.core.search_cache import get_search_cache

        cache = get_search_cache()
        return {"stats": cache.stats()}

    @router.post("/api/search/cache/clear", response_model=SearchCacheClearResponse)
    async def clear_search_cache_endpoint():
        """Clear the in-memory search cache (best-effort)."""
        from agent.core.search_cache import clear_search_cache

        clear_search_cache()
        return {"cleared": True}

    @router.get("/api/config/public", response_model=PublicConfigResponse)
    async def public_config():
        """
        Public, non-secret runtime configuration for frontend bootstrapping.

        This endpoint is intentionally safe to expose: it must not include API keys,
        tokens, raw MCP server configs, or any user data.
        """

        def _public_model_options() -> list[str]:
            """
            A conservative allowlist for the frontend model dropdown.

            Goal: avoid offering models that are unlikely to work with the configured
            provider/gateway (e.g. selecting GPT-4o when OPENAI_BASE_URL points to DeepSeek).
            """

            def _norm(value: Any) -> str:
                if not isinstance(value, str):
                    return ""
                return value.strip()

            def _is_openai_family(name: str) -> bool:
                lowered = name.lower()
                return (
                    "gpt" in lowered
                    or lowered.startswith("o1")
                    or lowered.startswith("o3")
                )

            def _is_anthropic_family(name: str) -> bool:
                return "claude" in name.lower()

            def _is_deepseek_family(name: str) -> bool:
                return "deepseek" in name.lower()

            def _is_qwen_family(name: str) -> bool:
                return "qwen" in name.lower()

            def _is_glm_family(name: str) -> bool:
                lowered = name.lower()
                return lowered.startswith("glm") or "glm" in lowered

            primary = _norm(deps.settings.primary_model)
            reasoning = _norm(deps.settings.reasoning_model)
            base_url = _norm(deps.settings.openai_base_url).lower()
            opts: list[str] = []
            seen: set[str] = set()

            def _add(name: str) -> None:
                name = _norm(name)
                if not name:
                    return
                key = name.lower()
                if key in seen:
                    return
                seen.add(key)
                opts.append(name)

            _add(primary)

            if _is_deepseek_family(primary) or "deepseek" in base_url:
                _add("deepseek-v4-flash")
                _add("deepseek-reasoner")
                if _is_deepseek_family(reasoning):
                    _add(reasoning)
            elif _is_anthropic_family(primary):
                if (deps.settings.anthropic_api_key or "").strip():
                    _add("claude-sonnet-4-5-20250514")
                    _add("claude-opus-4-20250514")
                    _add("claude-sonnet-4-20250514")
                if _is_anthropic_family(reasoning):
                    _add(reasoning)
            elif _is_openai_family(primary):
                if (deps.settings.openai_api_key or "").strip():
                    _add("gpt-5")
                    _add("gpt-4.1")
                    _add("gpt-4o")
                    _add("o1-mini")
                    if _is_openai_family(reasoning):
                        _add(reasoning)
            elif (
                _is_qwen_family(primary)
                or "dashscope" in base_url
                or "aliyuncs" in base_url
            ):
                _add("qwen-plus")
                _add("qwen3-vl-flash")
                if _is_qwen_family(reasoning):
                    _add(reasoning)
            elif _is_glm_family(primary) or "bigmodel" in base_url or "zhipu" in base_url:
                _add("glm-4.6")
                _add("glm-4.6v")
                if _is_glm_family(reasoning):
                    _add(reasoning)

            for attr in (
                "planner_model",
                "researcher_model",
                "writer_model",
                "evaluator_model",
                "critic_model",
            ):
                _add(_norm(getattr(deps.settings, attr, "")))

            return opts

        return {
            "version": deps.app_version,
            "defaults": {
                "port": deps.settings.port,
                "primary_model": deps.settings.primary_model,
                "reasoning_model": deps.settings.reasoning_model,
            },
            "models": {
                "default": deps.settings.primary_model,
                "options": _public_model_options(),
            },
            "features": {
                "mcp_enabled": bool(deps.mcp_enabled),
                "prometheus_enabled": bool(deps.settings.enable_prometheus),
                "tracing_enabled": bool(deps.settings.enable_tracing),
                "persistence_mode": (
                    "persistent" if deps.checkpointer_type != "memory" else "ephemeral"
                ),
            },
            "streaming": {
                "research": {"protocol": "sse", "endpoint": "/api/research/sse"},
            },
        }

    return router
