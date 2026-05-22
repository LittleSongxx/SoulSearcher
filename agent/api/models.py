from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# Request/Response models
class Message(BaseModel):
    role: str
    content: str


class SearchMode(BaseModel):
    useWebSearch: bool = False
    useAgent: bool = False
    useDeepSearch: bool = False


def _coerce_search_mode_input(value: Any) -> SearchMode | None:
    """
    Coerce legacy search_mode inputs into the structured SearchMode contract.

    We keep the runtime tolerant (strings / dicts) while exposing a strict OpenAPI
    schema (SearchMode object) for frontend/backend alignment.
    """
    if value is None:
        return None

    if isinstance(value, SearchMode):
        return value

    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"", "direct"}:
            return SearchMode()
        if lowered == "deep":
            return SearchMode(useWebSearch=True, useAgent=True, useDeepSearch=True)
        raise ValueError("search_mode must be direct/deep flags")

    if isinstance(value, dict):
        use_web = bool(value.get("useWebSearch", value.get("use_web", False)))
        use_deep = bool(value.get("useDeepSearch", value.get("use_deep", False)))
        use_agent = bool(use_deep)

        # If booleans were not provided but a mode string exists, derive flags from it.
        if not (use_web or use_agent or use_deep) and isinstance(
            value.get("mode"), str
        ):
            mode_lower = value["mode"].strip().lower()
            if mode_lower == "deep":
                use_agent = True
                use_deep = True
            elif mode_lower not in {"", "direct"}:
                raise ValueError("search_mode.mode must be direct or deep")

        # Deep enables web and agent.
        if use_deep:
            use_web = True
            use_agent = True

        return SearchMode(
            useWebSearch=use_web, useAgent=use_agent, useDeepSearch=use_deep
        )

    return None


class ProviderCircuitSnapshot(BaseModel):
    is_open: bool
    consecutive_failures: int
    opened_for_seconds: Optional[float] = None
    resets_in_seconds: Optional[float] = None


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


class ToolRegistryRefreshResponse(BaseModel):
    discovered: int
    total_tools: int


class AgentHealthResponse(BaseModel):
    agents_count: int
    agent_ids: list[str]
    tool_registry_total_tools: int
    enhanced_tool_discovery_enabled: bool
    enhanced_tool_discovery_recursive: bool
    rag_enabled: bool
    search_strategy: str
    search_engines: list[str]
    search_providers_available: list[str]


class PublicConfigDefaults(BaseModel):
    port: int
    primary_model: str
    reasoning_model: str


class PublicConfigFeatures(BaseModel):
    mcp_enabled: bool
    sandbox_mode: str
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
    hit_rate: float


class SearchCacheStatsResponse(BaseModel):
    stats: SearchCacheStats


class SearchCacheClearResponse(BaseModel):
    cleared: bool


class MemoryStatusResponse(BaseModel):
    backend: str
    url_configured: bool
    checkpointer: bool
    mem0_enabled: bool


class ExportTemplateItem(BaseModel):
    id: str
    name: str
    description: str


class ExportTemplatesResponse(BaseModel):
    templates: list[ExportTemplateItem]


class DocumentUploadResponse(BaseModel):
    success: bool
    filename: str
    chunks: int
    message: str


class DocumentListResponse(BaseModel):
    total_chunks: int
    documents: list[dict[str, Any]]


class DocumentDeleteResponse(BaseModel):
    success: bool
    message: str


class DocumentSearchResponse(BaseModel):
    query: str
    results: list[dict[str, Any]]


class ImagePayload(BaseModel):
    name: Optional[str] = None
    data: str
    mime: Optional[str] = None


class ResearchRequest(BaseModel):
    query: str
    model: Optional[str] = None
    search_mode: Optional[SearchMode] = None
    user_id: Optional[str] = None
    images: Optional[list[ImagePayload]] = None
    deepsearch_config: dict[str, Any] = Field(default_factory=dict)
    research_brief: dict[str, Any] = Field(default_factory=dict)

    @field_validator("search_mode", mode="before")
    @classmethod
    def _coerce_search_mode(cls, value: Any) -> SearchMode | None:
        return _coerce_search_mode_input(value)


_RESEARCH_DEEPSEARCH_CONFIG_KEYS = {
    "deepsearch_strategy",
    "strategy",
    "deepsearch_mode",
    "deepsearch_max_epochs",
    "deepsearch_max_seconds",
    "deepsearch_max_tokens",
    "deepsearch_max_research_units",
    "deepsearch_max_tool_calls_per_unit",
    "deepsearch_max_skills",
    "deepsearch_reflection_loops",
    "deepsearch_supervisor_rounds",
    "deepsearch_supervisor_max_workers",
    "deepsearch_supervisor_queries_per_worker",
    "deepsearch_supervisor_parallel_workers",
    "deepsearch_supervisor_think_enabled",
    "deepsearch_supervisor_max_depth",
    "deepsearch_supervisor_depth_confidence_threshold",
    "deepsearch_max_seconds_per_worker",
    "deepsearch_claim_verifier_use_passages",
    "deepsearch_claim_verifier_min_overlap_tokens",
    "deepsearch_claim_verifier_max_evidence_per_claim",
    "deepsearch_claim_verifier_max_claims",
    "deepsearch_guardrail_denied_tools",
    "deepsearch_guardrail_allowed_domains",
    "deepsearch_summary_trigger_tokens",
    "deepsearch_summary_trigger_messages",
    "deepsearch_summary_keep_recent",
    # Per-task model overrides
    "supervisor_model",
    "planner_model",
    "planning_model",
    "query_model",
    "query_gen_model",
    "search_summary_model",
    "summary_model",
    "synthesis_model",
    "worker_model",
    "researcher_model",
    "research_model",
    "compression_model",
    "writer_model",
    "final_report_model",
    "writing_model",
    "verifier_model",
    "evaluator_model",
    "evaluation_model",
    "reasoning_model",
    # Source/MCP configuration
    "source_policy",
    "source_routing",
    "source_providers",
    "source_collections",
    "source_connectors",
    "source_index_attempts",
    "allowed_domains",
    "denied_domains",
    "mcp_preset_ids",
    "mcp_results",
    "mcp_auth_required",
    "mcp_requires_auth",
    "mcp_tools_to_include",
    "mcp_tool_whitelist",
    "mcp_max_tools",
    "use_rag",
    "use_reflection_loop",
}


_RESEARCH_DEEPSEARCH_CONFIG_DICT_KEYS = {
    "source_routing",
    "mcp_results",
    "research_brief_review",
}


_RESEARCH_DEEPSEARCH_CONFIG_OBJECT_LIST_KEYS = {
    "source_collections",
    "source_connectors",
    "source_index_attempts",
    "mcp_results",
}


def _normalize_research_deepsearch_strategy(value: Any) -> str:
    mode = str(value or "").strip().lower().replace("-", "_")
    if mode in {"tree", "supervisor_workers"}:
        return mode
    if mode in {"supervisor", "workers", "supervisor_worker"}:
        return "supervisor_workers"
    return "supervisor_workers"


def _safe_research_deepsearch_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key or "").strip()
        if key_text not in _RESEARCH_DEEPSEARCH_CONFIG_KEYS:
            continue
        if isinstance(item, (str, int, float, bool)) or item is None:
            cleaned[key_text] = item
        elif key_text in _RESEARCH_DEEPSEARCH_CONFIG_OBJECT_LIST_KEYS and isinstance(
            item, list
        ):
            cleaned[key_text] = [
                part for part in item if isinstance(part, (dict, str, int, float, bool))
            ]
        elif isinstance(item, list):
            cleaned[key_text] = [
                str(part).strip() for part in item if str(part).strip()
            ]
        elif key_text in _RESEARCH_DEEPSEARCH_CONFIG_DICT_KEYS and isinstance(
            item, dict
        ):
            cleaned[key_text] = item
    for strategy_key in ("deepsearch_strategy", "strategy", "deepsearch_mode"):
        if strategy_key in cleaned:
            cleaned[strategy_key] = _normalize_research_deepsearch_strategy(
                cleaned[strategy_key]
            )
    return cleaned


class ResearchMessageResponse(BaseModel):
    id: str
    content: str
    role: str = "assistant"
    timestamp: str


class GraphInterruptResumeRequest(BaseModel):
    thread_id: str
    payload: Any
    model: Optional[str] = None
    search_mode: Optional[SearchMode] = None

    @field_validator("search_mode", mode="before")
    @classmethod
    def _coerce_search_mode(cls, value: Any) -> SearchMode | None:
        return _coerce_search_mode_input(value)


class CancelRequest(BaseModel):
    """Request body for cancelling a running task."""

    reason: Optional[str] = "User requested cancellation"


def _serialize_interrupts(interrupts: Any) -> list[Any]:
    if not interrupts:
        return []
    result: list[Any] = []
    for item in interrupts:
        if hasattr(item, "value"):
            result.append(item.value)
        elif isinstance(item, dict):
            result.append(item)
        else:
            result.append(str(item))
    return result


def _normalize_interrupt_resume_payload(payload: Any) -> Any:
    """
    Normalize /api/interrupt/resume payloads for LangGraph `interrupt()` resumes.

    Weaver historically used a custom payload shape:
        {"tool_approved": true/false, "tool_calls": [{name, args, ...}, ...]}

    LangChain's official HumanInTheLoopMiddleware expects a HITLResponse:
        {"decisions": [{"type": "approve"|"edit"|"reject", ...}, ...]}

    This helper keeps backwards compatibility while allowing newer clients to
    send the native HITLResponse shape directly.
    """
    if not isinstance(payload, dict):
        return payload

    # Native HITLResponse passthrough
    decisions = payload.get("decisions")
    if isinstance(decisions, list):
        return payload

    # Legacy tool approval payload -> HITLResponse decisions
    if "tool_approved" in payload:
        tool_calls = payload.get("tool_calls")
        if not isinstance(tool_calls, list) or not tool_calls:
            raise ValueError(
                "Legacy resume payload requires non-empty 'tool_calls' when 'tool_approved' is present"
            )

        approved = bool(payload.get("tool_approved"))
        if approved:
            normalized_decisions: list[dict[str, Any]] = []
            for call in tool_calls:
                if not isinstance(call, dict):
                    raise ValueError("tool_calls entries must be objects")
                name = call.get("name")
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("tool_calls[].name is required")
                args = call.get("args") or {}
                if not isinstance(args, dict):
                    args = {}
                normalized_decisions.append(
                    {
                        "type": "edit",
                        "edited_action": {"name": name, "args": args},
                    }
                )
            return {"decisions": normalized_decisions}

        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            message = "User rejected tool execution."
        return {
            "decisions": [{"type": "reject", "message": message} for _ in tool_calls],
        }

    return payload
