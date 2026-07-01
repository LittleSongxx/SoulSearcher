import asyncio
import hashlib
import hmac
import inspect
import json
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from fastapi import (
    FastAPI,
    File,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        REGISTRY,
        Counter,
        Gauge,
        generate_latest,
    )
except ModuleNotFoundError:
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

    class _NoopMetric:
        def labels(self, *args, **kwargs):
            return self

        def inc(self, *args, **kwargs):
            return None

        def dec(self, *args, **kwargs):
            return None

    class _NoopRegistry:
        _names_to_collectors = {}

    REGISTRY = _NoopRegistry()

    def Counter(*args, **kwargs):
        return _NoopMetric()

    def Gauge(*args, **kwargs):
        return _NoopMetric()

    def generate_latest():
        return b""


from pydantic import BaseModel, Field, field_validator

from agent import (
    ToolEvent,
    create_checkpointer,
    create_research_graph,
    get_emitter,
    remove_emitter,
)

# Router modules extracted from main.py for maintainability
from agent.api.tracing import router as tracing_router
from agent.memory import (
    MemoryRecord,
    MemoryScope,
    MemoryType,
    MemoryUnavailableError,
    get_memory_service,
)
from agent.retrieval.documents import (
    DocumentLibraryUnavailable,
    get_document_library,
)
from agent.retrieval.gateway import retrieve_sources
from agent.retrieval.policy import (
    LegacySourceRoutingError,
    build_retrieval_policy,
    reject_legacy_source_routing,
)
from agent.runtime.background_runs import BackgroundRunRequest, background_run_manager
from agent.runtime.idempotency import (
    IdempotencyConflictError,
    canonical_request_hash,
    idempotency_store,
)
from agent.core.llm_reliability import llm_reliability_manager
from agent.runtime.middleware.shared import get_token_summary
from agent.runtime.request_builder import (
    ResearchRuntimeRequest,
    build_research_runtime,
)
from agent.runtime.runs import RunStatus, run_manager
from agent.workflows.evidence_extractor import extract_message_sources
from agent.workflows.research_brief import build_research_brief
from common.cancellation import TaskStatus, cancellation_manager
from common.config import settings
from common.evidence_store import build_evidence_store_snapshot
from common.logger import get_logger, setup_logging
from common.metrics import metrics_registry
from common.proxy_env import normalize_socks_proxy_env
from common.research_events import build_research_run_event
from common.sse import (
    format_sse_event,
    format_sse_retry,
    iter_abort_on_disconnect,
    iter_with_sse_keepalive,
)
from common.stream_translate import data_stream_line_to_payload, translate_data_stream_line_to_sse
from common.thread_ownership import get_thread_owner, set_thread_owner
from common.tracing import SpanKind, record_span, trace_request
from tools.browser.browser_session import browser_sessions
from tools.core.registry import register_tools
from tools.mcp import close_mcp_tools, init_mcp_tools
from tools.sandbox import sandbox_browser_sessions
from tools.search.multi_search import get_search_orchestrator

# Initialize logging
setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await startup_event()
    try:
        yield
    finally:
        # Shutdown
        await shutdown_event()


# Initialize FastAPI app
app = FastAPI(
    title="SoulSearcher Research Agent API",
    description="Deep  research AI agent with code execution capabilities",
    version="0.1.0",
    lifespan=lifespan,
)

APP_STARTED_AT = time.monotonic()


# Prometheus metrics (optional, made idempotent to survive double imports under reload)
def _get_or_create_counter(name: str, *args, **kwargs):
    existing = REGISTRY._names_to_collectors.get(name)  # type: ignore[attr-defined]
    if existing:
        return existing
    return Counter(name, *args, **kwargs)


def _get_or_create_gauge(name: str, *args, **kwargs):
    existing = REGISTRY._names_to_collectors.get(name)  # type: ignore[attr-defined]
    if existing:
        return existing
    return Gauge(name, *args, **kwargs)


http_requests_total = (
    _get_or_create_counter(
        "soulsearcher_http_requests_total",
        "Total HTTP requests",
        ["method", "path", "status"],
    )
    if settings.enable_prometheus
    else None
)
http_inprogress = (
    _get_or_create_gauge("soulsearcher_http_inprogress", "In-flight HTTP requests")
    if settings.enable_prometheus
    else None
)

# Streaming connection gauges (always registered; cheap + useful for debugging).
sse_active_connections = _get_or_create_gauge(
    "soulsearcher_sse_active_connections",
    "Active SSE connections",
    ["endpoint"],
)


# Request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all HTTP requests, enforce internal auth, and apply basic rate limiting."""
    request_id = (request.headers.get("X-Request-ID") or "").strip() or str(
        uuid.uuid4()
    )[:8]
    start_time = time.time()

    if http_inprogress:
        http_inprogress.inc()

    internal_key = (getattr(settings, "internal_api_key", "") or "").strip()
    auth_user_header = (
        getattr(settings, "auth_user_header", "") or ""
    ).strip() or "X-SoulSearcher-User"
    path = request.url.path
    method = request.method.upper()
    rate_limit_enabled = bool(getattr(settings, "rate_limit_enabled_effective", True))

    principal_id = (
        (request.headers.get(auth_user_header) or "").strip() if internal_key else ""
    ).strip()
    request.state.principal_id = principal_id or (
        "internal" if internal_key else "anonymous"
    )

    logger.info(
        f"Request started | {request.method} {request.url.path} | "
        f"ID: {request_id} | Client: {request.client.host if request.client else 'unknown'}"
    )

    try:
        should_auth = internal_key and path.startswith("/api/") and method != "OPTIONS"
        provided = ""
        if should_auth:
            auth_header = (request.headers.get("Authorization") or "").strip()
            if auth_header.lower().startswith("bearer "):
                provided = auth_header[7:].strip()
            if not provided:
                provided = (request.headers.get("X-API-Key") or "").strip()

        authorized = (not should_auth) or (
            provided and hmac.compare_digest(provided, internal_key)
        )

        # In-memory rate limiting via encapsulated token-bucket RateLimiter.
        rate_limit_result = None
        if (
            rate_limit_enabled
            and path not in _RATE_LIMIT_EXEMPT
            and method != "OPTIONS"
        ):
            identity = (
                (getattr(request.state, "principal_id", "") or "").strip()
                if internal_key and authorized
                else _get_client_ip(request)
            )
            is_research_stream = path == "/api/research/sse"
            rate_limit_result = _rate_limiter.check(
                identity, is_research=is_research_stream
            )

        if rate_limit_result and not rate_limit_result.allowed:
            response = JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many requests. Please slow down.",
                    "retry_after": rate_limit_result.retry_after,
                },
                headers={"Retry-After": str(rate_limit_result.retry_after)},
            )
        elif not authorized:
            response = JSONResponse(
                status_code=401,
                content={
                    "error": "Unauthorized",
                    "status_code": 401,
                    "request_id": request_id,
                    "timestamp": datetime.now().isoformat(),
                },
                headers={"WWW-Authenticate": "Bearer"},
            )
        else:
            response = await call_next(request)

        response.headers["X-Request-ID"] = request_id
        if rate_limit_result:
            _apply_rate_limit_headers(
                response,
                limit=rate_limit_result.limit,
                remaining=rate_limit_result.remaining,
                reset_ts=rate_limit_result.reset_ts,
            )
        duration = time.time() - start_time

        logger.info(
            f"Request completed | {request.method} {request.url.path} | "
            f"ID: {request_id} | Status: {response.status_code} | "
            f"Duration: {duration:.3f}s"
        )
        if http_requests_total:
            http_requests_total.labels(
                request.method, request.url.path, response.status_code
            ).inc()

        return response
    except Exception as e:
        duration = time.time() - start_time
        logger.error(
            f"? Request failed | {request.method} {request.url.path} | "
            f"ID: {request_id} | Duration: {duration:.3f}s | Error: {e!s}",
            exc_info=True,
        )
        if http_requests_total:
            http_requests_total.labels(request.method, request.url.path, 500).inc()
        raise
    finally:
        if http_inprogress:
            http_inprogress.dec()


# Configure CORS
cors_origin_regex = None
try:
    env = (getattr(settings, "app_env", "") or "").strip().lower()
    if bool(getattr(settings, "debug", False)) or env in {
        "dev",
        "debug",
        "local",
        "test",
    }:
        # Dev ergonomics: allow the UI to run on any local port (e.g. 3100, 5173, random e2e ports).
        cors_origin_regex = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
except Exception:
    cors_origin_regex = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_origin_regex=cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "X-Thread-ID",
        "X-Request-ID",
    ],  # Allow frontend to read these headers
)


# ---------------------------------------------------------------------------
# Rate Limiting Middleware (in-memory token bucket)
# ---------------------------------------------------------------------------
from common.rate_limiter import build_rate_limiter
from common.stream_registry import StreamRegistry

_rate_limiter = build_rate_limiter(
    backend=str(getattr(settings, "rate_limit_backend", "memory") or "memory"),
    redis_url=str(getattr(settings, "redis_url", "") or ""),
    general_per_minute=int(getattr(settings, "rate_limit_general_per_minute", 60)),
    research_per_minute=int(getattr(settings, "rate_limit_research_per_minute", 20)),
    window_seconds=int(getattr(settings, "rate_limit_window_seconds", 60)),
    max_buckets=int(getattr(settings, "rate_limit_max_buckets", 10_000) or 10_000),
    redis_fail_open=bool(getattr(settings, "rate_limit_redis_fail_open", True)),
)
_stream_registry = StreamRegistry()
_RATE_LIMIT_EXEMPT = {"/", "/health", "/metrics", "/docs", "/openapi.json", "/redoc"}
_RATE_LIMIT_EXEMPT.add("/ready")


def _get_client_ip(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For behind a reverse proxy."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _apply_rate_limit_headers(
    response: Any, *, limit: int, remaining: int, reset_ts: int
) -> None:
    if not hasattr(response, "headers"):
        return
    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = str(max(remaining, 0))
    response.headers["X-RateLimit-Reset"] = str(reset_ts)


def _require_thread_owner(request: Request, thread_id: str) -> None:
    """
    Enforce per-user thread isolation when internal auth is enabled.

    Uses best-effort ownership sources:
    - In-memory thread ownership registry (SSE-created threads)
    - Persisted session state via checkpointer (if present)
    """
    internal_key = (getattr(settings, "internal_api_key", "") or "").strip()
    if not internal_key:
        return

    principal_id = (getattr(request.state, "principal_id", "") or "").strip()
    if not principal_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    owner_id = (get_thread_owner(thread_id) or "").strip()
    if owner_id and owner_id != principal_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    if not checkpointer:
        return

    try:
        from common.session_manager import get_session_manager

        manager = get_session_manager(checkpointer)
        session_state = manager.get_session_state(thread_id)
        if not session_state or not isinstance(session_state.state, dict):
            return
        persisted_owner = session_state.state.get("user_id")
        if (
            isinstance(persisted_owner, str)
            and persisted_owner.strip()
            and persisted_owner.strip() != principal_id
        ):
            raise HTTPException(status_code=403, detail="Forbidden")
    except HTTPException:
        raise
    except Exception:
        # Authorization is best-effort; never 500 due to ownership lookups.
        return


def _request_user_id(request: Request, explicit_user_id: str | None = None) -> str:
    explicit = (explicit_user_id or "").strip()
    internal_key = (getattr(settings, "internal_api_key", "") or "").strip()
    principal_id = (getattr(request.state, "principal_id", "") or "").strip()
    if internal_key and principal_id:
        if explicit and explicit != principal_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        return principal_id
    if explicit:
        return explicit
    return (getattr(settings, "memory_user_id", "") or "default").strip() or "default"


def _idempotency_key(request: Request) -> str:
    return (
        request.headers.get("Idempotency-Key")
        or request.headers.get("X-Idempotency-Key")
        or ""
    ).strip()


def _stable_id_from_idempotency_key(prefix: str, *, key: str, user_id: str) -> str:
    digest = hashlib.sha1(f"{prefix}:{user_id}:{key}".encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _begin_idempotency(
    request: Request,
    *,
    scope: str,
    user_id: str,
    payload: Any,
):
    key = _idempotency_key(request)
    if not key:
        return None
    try:
        return idempotency_store.begin(
            key=key,
            scope=scope,
            user_id=user_id,
            request_hash=canonical_request_hash(payload),
        )
    except IdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _idempotent_response(record: Any) -> JSONResponse | None:
    if record is not None and getattr(record, "status", "") == "completed":
        return JSONResponse(
            status_code=int(getattr(record, "http_status", 200) or 200),
            content=dict(getattr(record, "response", {}) or {}),
            headers={"X-Idempotency-Replayed": "true"},
        )
    return None


def _memory_source_candidates(memory_result: Any) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in getattr(memory_result, "records", []) or []:
        source_urls = [
            str(url).strip()
            for url in getattr(record, "source_urls", []) or []
            if str(url).strip()
        ]
        if not source_urls:
            continue
        for url in source_urls[:3]:
            if url in seen:
                continue
            seen.add(url)
            candidates.append(
                {
                    "url": url,
                    "title": str(getattr(record, "summary", "") or getattr(record, "content", ""))[:160],
                    "summary": str(getattr(record, "summary", "") or getattr(record, "content", ""))[:500],
                    "source": "memory",
                    "tool": "memory_retrieval",
                    "memory_record_id": str(getattr(record, "id", "")),
                    "memory_record_type": str(getattr(record, "type", "")),
                    "memory_source_evidence_ids": list(
                        getattr(record, "source_evidence_ids", []) or []
                    )[:8],
                    "requires_current_run_verification": True,
                }
            )
            if len(candidates) >= 12:
                return candidates
    return candidates



# Initialize agent graphs with short-term memory (checkpointer)
if settings.database_url:
    checkpointer = create_checkpointer(settings.database_url)
    _checkpointer_type = "postgres"
else:
    # Fallback to in-memory checkpointer for short-term memory
    checkpointer = MemorySaver()
    _checkpointer_type = "memory"
    logger.warning(
        "\n"
        "  ⚠️  Using in-memory checkpointer (MemorySaver). "
        "All session state will be LOST on process restart.\n"
        "  → Set DATABASE_URL in .env to enable persistent checkpointing.\n"
    )


research_graph = create_research_graph(
    checkpointer=checkpointer,
    interrupt_before=settings.interrupt_nodes_list,
    store=None,
)
mcp_thread_id = (
    "default"  # thread id for MCP event emission; per-request tools will override
)
mcp_enabled = settings.enable_mcp
mcp_servers_config = settings.mcp_servers
mcp_loaded_tools = 0


def _apply_mcp_thread_id(config: Any, thread_id: str) -> Any:
    """
    Ensure MCP server config carries a __thread_id__ hint for event emitters.

    Accepts dict or JSON string and returns the same type with injected field.
    """
    if not thread_id:
        return config

    if isinstance(config, str):
        try:
            parsed = json.loads(config)
        except json.JSONDecodeError:
            return config
        parsed["__thread_id__"] = thread_id
        return parsed

    if isinstance(config, dict):
        updated = dict(config)
        updated["__thread_id__"] = thread_id
        return updated

    return config


async def startup_event():
    """Initialize application on startup."""
    logger.info("=" * 80)
    logger.info("SoulSearcher Research Agent Starting...")
    logger.info("=" * 80)

    # Normalize common proxy env quirks (e.g. `ALL_PROXY=socks://...`) so
    # downstream httpx/OpenAI clients don't crash during request handling.
    try:
        normalize_socks_proxy_env()
    except Exception as e:
        logger.warning(f"Proxy env normalization failed: {e}")

    # Ensure the rate-limit bucket cleanup task is running (lifespan-managed).
    if getattr(settings, "rate_limit_enabled_effective", False):
        await _rate_limiter.start_cleanup_loop(interval=300.0)

    # Validate critical configuration and warn early about misconfigurations.
    from common.config import validate_critical_config
    config_warnings = validate_critical_config(settings)
    for cw in config_warnings:
        logger.warning(f"[config] {cw}")

    # Log configuration
    logger.info(f"Environment: {'DEBUG' if settings.debug else 'PRODUCTION'}")
    logger.info(f"Primary Model: {settings.primary_model}")
    logger.info(f"Reasoning Model: {settings.reasoning_model}")
    logger.info(
        f"Database: {'Configured' if settings.database_url else 'Not configured'}"
    )
    logger.info(f"Checkpointer: {'Enabled' if checkpointer else 'Disabled'}")
    if getattr(settings, "memory_enabled", True):
        try:
            memory_service = get_memory_service()
            memory_service.setup()
            status = memory_service.status()
            logger.info(
                "Unified memory: %s (%s records, pgvector=%s)",
                status.get("backend", "unknown"),
                status.get("record_count", 0),
                status.get("pgvector_available", False),
            )
        except MemoryUnavailableError as e:
            logger.warning("[Memory] Unified memory disabled: %s", e)
        except Exception as e:
            logger.warning("[Memory] Unified memory initialization failed: %s", e)
    else:
        logger.info("Unified memory disabled by MEMORY_ENABLED=false")

    # Initialize MCP tools
    global mcp_loaded_tools, mcp_servers_config
    try:
        logger.info("Initializing MCP tools...")
        servers_cfg = _apply_mcp_thread_id(mcp_servers_config, mcp_thread_id)
        mcp_servers_config = servers_cfg
        mcp_tools = await init_mcp_tools(
            servers_override=servers_cfg, enabled=mcp_enabled
        )
        if mcp_tools:
            register_tools(mcp_tools)
            mcp_loaded_tools = len(mcp_tools)
            logger.info(f"Successfully registered {mcp_loaded_tools} MCP tools")
        else:
            logger.info("No MCP tools to register")
            mcp_loaded_tools = 0
    except Exception as e:
        logger.warning(f"MCP tools initialization failed: {e}", exc_info=settings.debug)
        mcp_loaded_tools = 0

    # Start IM channel service (Feishu/Lark, etc.)
    try:
        if getattr(settings, "channels_enabled", False):
            from channels.service import start_channel_service
            await start_channel_service()
            logger.info("Channel service started")
    except Exception as e:
        logger.warning(f"Channel service startup failed: {e}", exc_info=settings.debug)

    try:
        stale_count = background_run_manager.mark_stale_active_runs_failed()
        if stale_count:
            logger.warning(
                "[BackgroundRun] Marked %d stale active background run(s) failed on startup",
                stale_count,
            )
    except Exception as e:
        logger.warning(
            "[BackgroundRun] stale active run cleanup failed: %s",
            e,
            exc_info=settings.debug,
        )

    # Prime skills cache
    try:
        from agent.skills.storage import get_skill_storage
        get_skill_storage().load_skills(enabled_only=True)
        logger.info("Skills cache primed")
    except Exception as e:
        logger.warning(f"Skills cache priming failed: {e}", exc_info=settings.debug)

    logger.info("=" * 80)
    logger.info("SoulSearcher Research Agent Ready")
    logger.info("=" * 80)


async def shutdown_event():
    """Cleanup on application shutdown."""
    logger.info("=" * 80)
    logger.info("SoulSearcher Research Agent Shutting Down...")
    logger.info("=" * 80)

    # Stop channel service
    try:
        from channels.service import stop_channel_service
        await stop_channel_service()
        logger.info("Channel service stopped")
    except Exception as e:
        logger.debug(f"Channel service stop skipped: {e}")

    # Stop lifespan-managed background tasks.
    await _rate_limiter.stop_cleanup_loop()
    _stream_registry.cancel_all()

    try:
        logger.info("Closing MCP tools...")
        await close_mcp_tools()
        logger.info("MCP tools closed successfully")
    except Exception as e:
        logger.error(f"Error closing MCP tools: {e}", exc_info=True)

    # Best-effort stop all Daytona sandboxes
    try:
        from tools.sandbox.daytona_client import daytona_stop_all

        daytona_stop_all(thread_id=mcp_thread_id)
    except Exception as e:
        logger.warning(f"Error stopping Daytona sandboxes: {e}")

    logger.info("=" * 80)
    logger.info("Shutdown Complete")
    logger.info("=" * 80)


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
    Coerce compact search_mode inputs into the structured SearchMode contract.

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


class AgentHealthResponse(BaseModel):
    tool_registry_total_tools: int
    search_strategy: str
    search_engines: list[str]
    search_providers_available: list[str]
    rate_limiter: dict[str, Any] = Field(default_factory=dict)
    background_leases: dict[str, Any] = Field(default_factory=dict)
    llm_reliability: dict[str, Any] = Field(default_factory=dict)


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


class MemoryStatusResponse(BaseModel):
    backend: str
    available: bool = False
    pgvector_available: bool = False
    embedding_model: str = ""
    embedding_dim: int = 0
    record_count: int = 0
    entity_count: int = 0
    relation_count: int = 0
    skill_evolution_count: int = 0
    error: Optional[str] = None


class MemoryListResponse(BaseModel):
    records: list[dict[str, Any]]
    count: int
    query: str = ""
    user_id: str


class MemoryRecordCreateRequest(BaseModel):
    content: str
    user_id: Optional[str] = None
    scope: str = MemoryScope.user.value
    type: str = MemoryType.fact.value
    summary: str = ""
    confidence: float = 0.75
    importance: float = 0.5
    quality_score: float = 0.0
    source_thread_id: str = ""
    source_run_id: str = ""
    source_evidence_ids: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    expires_at: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryRecordResponse(BaseModel):
    record: dict[str, Any]


class MemoryDeleteResponse(BaseModel):
    deleted: bool


class MemoryRetrieveRequest(BaseModel):
    query: str
    user_id: Optional[str] = None
    type: str = ""
    scope: str = ""
    limit: int = 12
    include_context: bool = True


class MemoryRetrieveResponse(BaseModel):
    records: list[dict[str, Any]]
    entities: list[dict[str, Any]]
    relations: list[dict[str, Any]]
    scoring: list[dict[str, Any]]
    context: str = ""
    user_id: str


class MemoryGraphResponse(BaseModel):
    entities: list[dict[str, Any]] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)


class MemorySkillEvolutionResponse(BaseModel):
    proposals: list[dict[str, Any]]
    count: int


class ExportTemplateItem(BaseModel):
    id: str
    name: str
    description: str


class ExportTemplatesResponse(BaseModel):
    templates: list[ExportTemplateItem]


class ImagePayload(BaseModel):
    name: Optional[str] = None
    data: str
    mime: Optional[str] = None


class ResearchRequest(BaseModel):
    query: str
    model: Optional[str] = None
    search_mode: Optional[SearchMode] = None
    user_id: Optional[str] = None
    skill_ids: Optional[list[str]] = None
    images: Optional[list[ImagePayload]] = None
    retrieval_policy: dict[str, Any] = Field(default_factory=dict)
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
    "deepsearch_guardrail_denied_domains",
    "deepsearch_summary_trigger_tokens",
    "deepsearch_summary_trigger_messages",
    "deepsearch_summary_keep_recent",
    "deep_research_strict_citations",
    "tool_policy_strict",
    "legacy_citation_mode",
    "allow_sandbox_tools",
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
    "retrieval_policy",
    "retrieval_allowed_origins",
    "retrieval_channels",
    "retrieval_methods",
    "retrieval_profiles",
    "allowed_domains",
    "denied_domains",
    "mcp_preset_ids",
    "mcp_results",
    "mcp_auth_required",
    "mcp_requires_auth",
    "mcp_tools_to_include",
    "mcp_tool_whitelist",
    "mcp_max_tools",
    "use_reflection_loop",
    "skill_ids",
    "deepsearch_skill_ids",
}


_RESEARCH_DEEPSEARCH_CONFIG_DICT_KEYS = {
    "retrieval_policy",
    "mcp_results",
    "research_brief_review",
}


_RESEARCH_DEEPSEARCH_CONFIG_OBJECT_LIST_KEYS = {
    "source_connectors",
    "user_injected_sources",
    "mcp_results",
}


def _normalize_research_deepsearch_strategy(value: Any) -> str:
    mode = str(value or "").strip().lower().replace("-", "_")
    if mode in {"supervisor", "workers", "supervisor_worker"}:
        return "supervisor_workers"
    return "supervisor_workers"


def _safe_research_deepsearch_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    reject_legacy_source_routing(value.get("source_routing"))
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
            if key_text == "retrieval_policy":
                reject_legacy_source_routing(item.get("source_routing"))
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


class BackgroundRunSubmitRequest(ResearchRequest):
    thread_id: Optional[str] = None
    webhook_url: Optional[str] = None


class UserSourceInjectionRequest(BaseModel):
    sources: list[dict[str, Any]] = Field(default_factory=list)
    note: str = ""


# Active streaming tasks managed via StreamRegistry (see common/stream_registry.py)
active_streams = _stream_registry._tasks


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

    SoulSearcher clients may send a shorthand payload shape:
        {"tool_approved": true/false, "tool_calls": [{name, args, ...}, ...]}

    LangChain's official HumanInTheLoopMiddleware expects a HITLResponse:
        {"decisions": [{"type": "approve"|"edit"|"reject", ...}, ...]}

    This helper accepts that shorthand while also allowing clients to send the
    native HITLResponse shape directly.
    """
    if not isinstance(payload, dict):
        return payload

    # Native HITLResponse passthrough
    decisions = payload.get("decisions")
    if isinstance(decisions, list):
        return payload

    # Client shorthand approval payload -> HITLResponse decisions
    if "tool_approved" in payload:
        tool_calls = payload.get("tool_calls")
        if not isinstance(tool_calls, list) or not tool_calls:
            raise ValueError(
                "Resume payload requires non-empty 'tool_calls' when 'tool_approved' is present"
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


# ---------------------------------------------------------------------------
# Global Exception Handlers — consistent JSON error responses
# ---------------------------------------------------------------------------
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse as StarletteJSONResponse


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Return human-readable validation errors instead of raw Pydantic output."""
    request_id = (request.headers.get("X-Request-ID") or "").strip() or str(
        uuid.uuid4()
    )[:8]
    errors = []
    for err in exc.errors():
        field = " → ".join(str(loc) for loc in err.get("loc", []))
        errors.append(
            {"field": field, "message": err.get("msg", ""), "type": err.get("type", "")}
        )
    logger.warning(f"Validation error | ID: {request_id} | Errors: {errors}")
    return StarletteJSONResponse(
        status_code=422,
        content={
            "error": "Validation Error",
            "detail": errors,
            "request_id": request_id,
            "timestamp": datetime.now().isoformat(),
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Consistent JSON format for all HTTP exceptions."""
    request_id = (request.headers.get("X-Request-ID") or "").strip() or str(
        uuid.uuid4()
    )[:8]
    return StarletteJSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail if isinstance(exc.detail, str) else "HTTP Error",
            "status_code": exc.status_code,
            "request_id": request_id,
            "timestamp": datetime.now().isoformat(),
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Catch-all handler — never leak stack traces in production."""
    request_id = (request.headers.get("X-Request-ID") or "").strip() or str(
        uuid.uuid4()
    )[:8]
    logger.error(
        f"Unhandled exception | ID: {request_id} | {type(exc).__name__}: {exc}",
        exc_info=True,
    )
    detail = str(exc) if settings.debug else "An internal server error occurred."
    return StarletteJSONResponse(
        status_code=500,
        content={
            "error": "Internal Server Error",
            "detail": detail,
            "request_id": request_id,
            "timestamp": datetime.now().isoformat(),
        },
    )


app.include_router(tracing_router)


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "healthy", "service": "SoulSearcher Research Agent", "version": "0.1.0"}


@app.get("/health")
async def health():
    """Detailed health check."""
    return {
        "status": "healthy",
        "database": "configured" if settings.database_url else "not configured",
        "checkpointer_type": _checkpointer_type,
        "persistence_mode": "persistent" if _checkpointer_type != "memory" else "ephemeral",
        "version": app.version,
        "uptime_seconds": time.monotonic() - APP_STARTED_AT,
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/ready")
async def ready():
    """Readiness check for production routing decisions."""
    checks: dict[str, Any] = {
        "database": {"configured": bool(settings.database_url), "ok": True},
        "rate_limiter": _rate_limiter_status(),
        "background_leases": _background_lease_status(),
        "llm_reliability": _llm_reliability_status(),
        "memory": {"enabled": bool(getattr(settings, "memory_enabled", True)), "ok": True},
        "search": {"ok": True, "providers_available": []},
    }

    if settings.database_url:
        try:
            import psycopg

            with psycopg.connect(settings.database_url, connect_timeout=2) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
        except Exception as exc:
            checks["database"] = {
                "configured": True,
                "ok": False,
                "error": _sanitize_error_message(str(exc)),
            }

    if getattr(settings, "memory_enabled", True):
        try:
            status = get_memory_service().status()
            checks["memory"].update(status)
            checks["memory"]["ok"] = bool(status.get("available", True))
        except Exception as exc:
            checks["memory"] = {
                "enabled": True,
                "ok": False,
                "error": _sanitize_error_message(str(exc)),
            }

    try:
        orchestrator = get_search_orchestrator()
        providers = [p.name for p in orchestrator.get_available_providers()]
        checks["search"] = {"ok": bool(providers), "providers_available": providers}
    except Exception as exc:
        checks["search"] = {
            "ok": False,
            "providers_available": [],
            "error": _sanitize_error_message(str(exc)),
        }

    ready_ok = all(bool(item.get("ok", False)) for item in checks.values())
    status_code = 200 if ready_ok else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if ready_ok else "not_ready",
            "checks": checks,
            "timestamp": datetime.now().isoformat(),
        },
    )


def _rate_limiter_status() -> dict[str, Any]:
    status = getattr(_rate_limiter, "backend_status", None)
    if isinstance(status, dict):
        payload = dict(status)
        payload["ok"] = bool(payload.get("available") or payload.get("fail_open"))
        return payload
    return {
        "backend": "memory",
        "ok": True,
        "bucket_count": int(getattr(_rate_limiter, "bucket_count", 0) or 0),
    }


def _background_lease_status() -> dict[str, Any]:
    payload = dict(getattr(background_run_manager, "lease_status", {}) or {})
    payload["ok"] = bool(payload.get("available", True))
    return payload


def _llm_reliability_status() -> dict[str, Any]:
    providers = llm_reliability_manager.snapshot()
    open_providers = [
        name for name, snapshot in providers.items() if bool(snapshot.get("is_open"))
    ]
    return {
        "enabled": bool(getattr(settings, "llm_reliability_enabled", True)),
        "ok": not (providers and len(open_providers) == len(providers)),
        "open_providers": open_providers,
        "provider_count": len(providers),
    }


@app.get("/api/health/agent", response_model=AgentHealthResponse)
async def agent_health():
    """Lightweight agent subsystem health snapshot (no sandbox side effects)."""
    from tools.core.registry import get_global_registry

    registry = get_global_registry()

    orchestrator = get_search_orchestrator()
    try:
        available = [p.name for p in orchestrator.get_available_providers()]
    except Exception:
        available = []

    return {
        "tool_registry_total_tools": len(registry.list_names()),
        "search_strategy": str(
            getattr(settings, "search_strategy", "fallback") or "fallback"
        ),
        "search_engines": list(getattr(settings, "search_engines_list", [])),
        "search_providers_available": sorted(
            [str(n) for n in available if str(n).strip()]
        ),
        "rate_limiter": _rate_limiter_status(),
        "background_leases": _background_lease_status(),
        "llm_reliability": _llm_reliability_status(),
    }


# ==================== Task Cancellation API ====================


@app.post("/api/research/cancel/{thread_id}")
async def cancel_research(
    thread_id: str, request: Request, payload: CancelRequest | None = None
):
    """
    Cancel a running research task.
    Args:
        thread_id: Task thread ID
        request: Optional cancellation reason payload
    """
    _require_thread_owner(request, thread_id)

    reason = payload.reason if payload else "User requested cancellation"
    logger.info(f"Cancel request received for thread: {thread_id}, reason: {reason}")

    # 1. 通过 cancellation_manager 取消令牌
    cancelled = await cancellation_manager.cancel(thread_id, reason)

    # 2. 取消对应的异步任务（如果存在）
    if thread_id in active_streams:
        task = active_streams[thread_id]
        task.cancel()
        del active_streams[thread_id]
        logger.info(f"Async task for {thread_id} cancelled")

    if cancelled:
        return {
            "status": "cancelled",
            "thread_id": thread_id,
            "reason": reason,
            "timestamp": datetime.now().isoformat(),
        }
    else:
        return {
            "status": "not_found",
            "thread_id": thread_id,
            "message": "Task not found or already completed",
        }


def _task_is_visible_to_principal(
    task_id: str, task_info: dict[str, Any], principal_id: str
) -> bool:
    owner_id = (get_thread_owner(task_id) or "").strip()
    if owner_id:
        return owner_id == principal_id
    metadata = task_info.get("metadata", {})
    if isinstance(metadata, dict):
        meta_user_id = metadata.get("user_id")
        if isinstance(meta_user_id, str) and meta_user_id.strip():
            return meta_user_id.strip() == principal_id
    return False


@app.post("/api/research/cancel-all")
async def cancel_all_research(request: Request):
    """Cancel all currently running tasks."""
    logger.info("Cancel all tasks requested")

    reason = "Batch cancellation requested"
    internal_key = (getattr(settings, "internal_api_key", "") or "").strip()
    if internal_key:
        principal_id = (getattr(request.state, "principal_id", "") or "").strip()

        # Cancel only tasks belonging to this principal to avoid cross-user cancellation.
        active = cancellation_manager.get_active_tasks()
        owned_task_ids = [
            task_id
            for task_id, info in active.items()
            if _task_is_visible_to_principal(task_id, info, principal_id)
        ]
        for task_id in owned_task_ids:
            await cancellation_manager.cancel(task_id, reason)
            task = active_streams.pop(task_id, None)
            if task:
                task.cancel()

        cancelled_count = len(owned_task_ids)
    else:
        # 取消所有令牌
        await cancellation_manager.cancel_all(reason)

        # 取消所有异步任务
        cancelled_count = len(active_streams)
        for task in active_streams.values():
            task.cancel()
        active_streams.clear()

    return {
        "status": "all_cancelled",
        "cancelled_count": cancelled_count,
        "timestamp": datetime.now().isoformat(),
    }


# ==================== Session Fork API ====================


class ForkSessionRequest(BaseModel):
    """Request payload for forking a research session.

    Follows Claude Code's session fork pattern: creates an independent copy
    of a session's checkpoint state so the user can explore alternative
    research paths without affecting the original.
    """

    source_thread_id: str = Field(..., description="Thread ID to fork from")
    new_thread_id: Optional[str] = Field(
        default=None,
        description="Optional target thread ID. Auto-generated if omitted.",
    )
    visibility: str = Field(
        default="private",
        description="Visibility for the forked session (private/group/public).",
    )


@app.post("/api/research/fork")
async def fork_research_session(
    request: Request,
    payload: ForkSessionRequest,
):
    """Fork a research session to explore alternative paths.

    Copies the latest checkpoint from source_thread_id to a new (or specified)
    thread_id, creating an independent branch. Both sessions can evolve
    independently from that point.

    Reference: Claude Code Agent SDK — "you can fork a session to explore
    alternative approaches."
    """
    if not checkpointer:
        raise HTTPException(
            status_code=400,
            detail="Fork requires a persistent checkpointer (PostgreSQL). "
                   "Set DATABASE_URL in .env.",
        )

    _require_thread_owner(request, payload.source_thread_id)

    try:
        from common.session_manager import get_session_manager

        manager = get_session_manager(checkpointer)

        # Verify the source session exists
        source_session = manager.get_session(payload.source_thread_id)
        if not source_session:
            raise HTTPException(
                status_code=404,
                detail=f"Source session not found: {payload.source_thread_id}",
            )

        # Determine owner for the new fork
        owner_id = ""
        internal_key = (getattr(settings, "internal_api_key", "") or "").strip()
        if internal_key:
            owner_id = (getattr(request.state, "principal_id", "") or "").strip()

        forked = manager.fork_session(
            source_thread_id=payload.source_thread_id,
            new_thread_id=payload.new_thread_id,
            owner_id=owner_id,
        )

        if not forked:
            raise HTTPException(
                status_code=500,
                detail="Fork operation failed. Check server logs for details.",
            )

        # Register thread ownership for the fork
        if owner_id:
            from common.thread_ownership import set_thread_owner

            set_thread_owner(forked.thread_id, owner_id)

        logger.info(
            "[Fork] Session forked: %s → %s",
            payload.source_thread_id,
            forked.thread_id,
        )

        return {
            "status": "forked",
            "source_thread_id": payload.source_thread_id,
            "forked_thread_id": forked.thread_id,
            "session": forked.to_dict(),
            "timestamp": datetime.now().isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("[Fork] Unexpected error: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Fork failed: {e!s}",
        )


@app.get("/api/tasks/active")
async def get_active_tasks(request: Request):
    """Get all active tasks."""
    active_tasks = cancellation_manager.get_active_tasks()

    internal_key = (getattr(settings, "internal_api_key", "") or "").strip()
    if internal_key:
        principal_id = (getattr(request.state, "principal_id", "") or "").strip()
        active_tasks = {
            task_id: info
            for task_id, info in active_tasks.items()
            if _task_is_visible_to_principal(task_id, info, principal_id)
        }

        # Compute a user-scoped stats payload (avoid leaking global counts).
        stats = {status.value: 0 for status in TaskStatus}
        for info in active_tasks.values():
            st = info.get("status")
            if isinstance(st, str) and st in stats:
                stats[st] += 1
        stats["total"] = len(active_tasks)

        stream_count = sum(
            1
            for thread_id in active_streams
            if _task_is_visible_to_principal(
                thread_id,
                active_tasks.get(thread_id, {}),
                principal_id,
            )
        )
    else:
        stats = cancellation_manager.get_stats()
        stream_count = len(active_streams)

    return {
        "active_tasks": active_tasks,
        "stats": stats,
        "stream_count": stream_count,
        "timestamp": datetime.now().isoformat(),
    }


# ==================== Stream Event Formatting ====================


async def format_stream_event(event_type: str, data: Any) -> str:
    """
    Format events in SoulSearcher's internal data-stream envelope.

    Format: {type}:{json_data}\n
    """
    payload = {"type": event_type, "data": data}
    return f"0:{json.dumps(payload)}\n"


def _should_emit_main_text_for_node(node_name: str) -> bool:
    """
    Decide whether streamed LLM tokens should be forwarded to the *main answer*
    (the assistant bubble), based on which LangGraph node is currently running.

    Goal: keep the main answer clean (OpenAI/DeepSeek-style) by preventing
    planner/query-gen/decomposition nodes from leaking their intermediate output.
    """
    name = (node_name or "").strip().lower()
    if not name:
        return False

    # Only allow "final writing" style nodes to stream tokens into the main answer.
    # Everything else should be represented via status/process events.
    allow_tokens = (
        "writer",
        "direct_answer",
        "final_report",
        "reviser",
    )
    return any(token in name for token in allow_tokens)


def _should_emit_thinking_summary_for_node(node_name: str) -> bool:
    """
    Decide whether a node's `output.messages` should be surfaced as a
    human-readable "thinking summary" in the UI accordion.

    This is NOT chain-of-thought. It is a short, user-facing progress narrative
    (e.g. a research plan + queries), kept separate from the final answer.
    """
    name = (node_name or "").strip().lower()
    if not name:
        return False

    # Only allow planning/clarification style nodes. Avoid writer/evaluator/deepsearch
    # outputs which can be long or contain structured blobs that don't read well.
    allow = (
        "planner",
        "web_plan",
        "refine_plan",
        "clarify",
        "research_brief",
        "supervisor",
        "classify",
    )
    return any(token in name for token in allow)


def _looks_like_structured_blob(text: str) -> bool:
    """
    Heuristic guardrail: skip large JSON/python-like dumps from appearing in the
    thinking accordion, which would recreate the "python list / json block" UX.
    """
    t = (text or "").strip()
    if not t:
        return False
    if "```" in t:
        return True
    if t.startswith(("{", "[")) and len(t) > 40:
        return True
    # Common keys seen in deepsearch/planner structured dumps
    if '"subtopics"' in t or "'subtopics'" in t or '"queries"' in t:
        if "{" in t or "[" in t:
            return True
    return False


def _sanitize_thinking_text(text: str, max_len: int = 1800) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    if len(t) > max_len:
        t = t[:max_len].rstrip() + "..."
    return t


def _contains_cjk(text: str) -> bool:
    try:
        return bool(re.search(r"[\u4e00-\u9fff]", text or ""))
    except Exception:
        return False


def _thinking_intro_for_node(node_name: str, *, use_zh: bool) -> str:
    name = (node_name or "").strip().lower()
    if not name:
        return ""

    # Keep these as short, user-facing progress narratives (NOT chain-of-thought).
    if "clarify" in name:
        return (
            "我先确认是否需要你补充信息，避免跑偏。"
            if use_zh
            else "I'll check if any clarification is needed so we don't go off-track."
        )
    if "research_brief" in name:
        return (
            "我在将你的问题转化为结构化的研究概要。"
            if use_zh
            else "I'm turning your question into a structured research brief."
        )
    if "classify_complexity" in name:
        return (
            "我在分析任务的复杂度以选择最优执行路径。"
            if use_zh
            else "I'm analyzing task complexity to select the best execution strategy."
        )
    if "supervisor" in name:
        return (
            "研究主管正在制定研究策略并协调子研究员。"
            if use_zh
            else "The research supervisor is planning strategy and coordinating sub-researchers."
        )
    if "planner" in name or "web_plan" in name or "refine_plan" in name:
        return (
            "我会先拆解问题并生成一组检索关键词。"
            if use_zh
            else "I'll break the question down and generate targeted search queries."
        )
    if "researcher" in name and "research_supervisor" not in name:
        return (
            "子研究员正在搜索并收集相关来源的信息。"
            if use_zh
            else "A sub-researcher is searching and gathering information from sources."
        )
    if "compress" in name:
        return (
            "我会去重并压缩信息，保留最相关证据。"
            if use_zh
            else "I'll deduplicate and compress sources, keeping the most relevant evidence."
        )
    if "perform_parallel_search" in name or (
        ("search" in name) and "research" not in name
    ):
        return (
            "接下来我会检索并收集资料，多来源交叉验证。"
            if use_zh
            else "Next I'll search and collect sources, cross-checking across providers."
        )
    if "deepsearch" in name:
        return (
            "我会进行迭代式深度检索（生成查询 → 搜索 → 阅读 → 汇总），直到覆盖充分。"
            if use_zh
            else "I'll run an iterative deep-search loop (query → search → read → summarize) until coverage is solid."
        )
    if "compressor" in name:
        return (
            "我会去重并压缩信息，保留最相关证据。"
            if use_zh
            else "I'll deduplicate and compress sources, keeping the most relevant evidence."
        )
    if "final_report" in name or "writer" in name:
        return (
            "我会把证据整理成结构化的最终回答。"
            if use_zh
            else "I'll synthesize the evidence into a clear final answer."
        )
    if "evaluator" in name:
        return (
            "我会自检覆盖度/准确性，必要时补充检索或修订。"
            if use_zh
            else "I'll self-check coverage/accuracy and revise or research more if needed."
        )
    if "reviser" in name or "revise" in name:
        return (
            "我会根据自检结果修订答案，让表述更清晰。"
            if use_zh
            else "I'll revise the draft for clarity and completeness."
        )
    return ""


def _compact_tool_args(tool_input: Any) -> dict[str, Any]:
    """
    Best-effort compact tool args for streaming UI previews.

    This must remain:
    - small (avoid large blobs / file contents)
    - safe (avoid leaking secrets; only include a small allowlist)
    - JSON-serializable
    """
    if not isinstance(tool_input, dict):
        return {}

    allowed_keys = (
        "query",
        "url",
        "code",
        "path",
        "command",
        "args",
        "selector",
        "text",
        "title",
        "filename",
    )
    out: dict[str, Any] = {}

    for key in allowed_keys:
        if key not in tool_input:
            continue
        value = tool_input.get(key)
        if isinstance(value, str):
            max_len = 400 if key == "code" else 200
            trimmed = value.strip()
            out[key] = trimmed if len(trimmed) <= max_len else trimmed[:max_len] + "..."
            continue
        if value is None or isinstance(value, (bool, int, float)):
            out[key] = value
            continue
        if isinstance(value, list):
            # Avoid dumping giant lists to the client.
            out[key] = value[:10]
            continue

        # Fallback: stringify a small preview.
        try:
            out[key] = str(value)[:200]
        except Exception:
            pass

    return out


def _normalize_search_mode(
    search_mode: SearchMode | dict[str, Any] | str | None,
) -> dict[str, Any]:
    if isinstance(search_mode, SearchMode):
        use_web = search_mode.useWebSearch
        use_deep = search_mode.useDeepSearch
        use_agent = bool(use_deep)
        use_deep_prompt = use_deep
    elif isinstance(search_mode, dict):
        # Support both camelCase (frontend payload) and snake_case (already-normalized)
        use_web = bool(
            search_mode.get("useWebSearch", search_mode.get("use_web", False))
        )
        use_deep = bool(
            search_mode.get("useDeepSearch", search_mode.get("use_deep", False))
        )
        use_agent = bool(use_deep)
        use_deep_prompt = bool(
            search_mode.get(
                "useDeepPrompt", search_mode.get("use_deep_prompt", use_deep)
            )
        )

        # If booleans were not provided but a mode string exists, derive flags from it
        if not (use_web or use_agent or use_deep) and isinstance(
            search_mode.get("mode"), str
        ):
            mode_lower = search_mode["mode"].strip().lower()
            if mode_lower == "deep":
                use_agent = True
                use_deep = True
                use_deep_prompt = use_deep
            elif mode_lower not in {"", "direct"}:
                raise ValueError("search_mode.mode must be direct or deep")
    elif isinstance(search_mode, str):
        lowered = search_mode.lower().strip()

        if lowered in {"direct", ""}:
            use_web = False
            use_agent = False
            use_deep = False
            use_deep_prompt = False
        elif lowered == "deep":
            use_web = True
            use_agent = True
            use_deep = True
            use_deep_prompt = True
        else:
            use_web = False
            use_agent = False
            use_deep = False
            use_deep_prompt = False
    else:
        use_web = False
        use_agent = False
        use_deep = False
        use_deep_prompt = False

    if use_deep:
        use_web = True
        use_agent = True
        use_deep_prompt = True

    mode = "deep" if use_deep else "direct"

    return {
        "use_web": use_web,
        "use_agent": use_agent,
        "use_deep": use_deep,
        "mode": mode,
        "use_deep_prompt": use_deep_prompt,
    }


def _persist_run_stream_event(
    *,
    run_id: str,
    thread_id: str,
    seq: int,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        return run_manager.append_event(
            run_id=run_id,
            thread_id=thread_id,
            seq=seq,
            type=event_type,
            payload=payload,
            status=str(payload.get("status") or data.get("status") or ""),
        )
    except Exception:
        logger.debug("Failed to persist run stream event", exc_info=True)
    return None


def _normalize_images_payload(
    images: Optional[list[ImagePayload]],
) -> list[dict[str, Any]]:
    """
    Normalize incoming image payloads; strip data URL prefix if present.
    """
    normalized: list[dict[str, Any]] = []
    if not images:
        return normalized

    for img in images:
        if not img or not img.data:
            continue
        data = img.data
        if data.startswith("data:") and "," in data:
            data = data.split(",", 1)[1]
        normalized.append(
            {"name": img.name or "", "mime": img.mime or "", "data": data}
        )
    return normalized


async def stream_agent_events(
    input_text: str,
    thread_id: str = "default",
    run_id: Optional[str] = None,
    model: str | None = None,
    search_mode: dict[str, Any] | None = None,
    images: Optional[list[dict[str, Any]]] = None,
    user_id: Optional[str] = None,
    request: Optional[Request] = None,
    deepsearch_config: Optional[dict[str, Any]] = None,
    research_brief: Optional[dict[str, Any]] = None,
):
    """
    Stream agent execution events in real-time.

    Converts LangGraph events to Vercel AI SDK format.
    Supports cancellation via cancellation_manager.
    """
    event_count = 0
    start_time = time.time()
    was_interrupted = False
    emit_main_text = False
    use_zh = _contains_cjk(input_text)
    last_thinking_text = ""
    images = images or []
    user_id = user_id or settings.memory_user_id
    model = (model or settings.primary_model).strip()
    final_quality_summary: dict[str, Any] = {}

    # Optional per-thread log handler for easier debugging
    thread_handler = None
    root_logger = logging.getLogger()
    if settings.enable_file_logging:
        try:
            log_path = Path(settings.log_file)
            thread_log_dir = log_path.parent / "threads"
            thread_log_dir.mkdir(parents=True, exist_ok=True)
            thread_log_file = thread_log_dir / f"{thread_id}.log"

            # Reuse formatter from existing handlers if available
            formatter = None
            if root_logger.handlers:
                formatter = root_logger.handlers[0].formatter
            thread_handler = logging.FileHandler(thread_log_file, encoding="utf-8")
            if formatter:
                thread_handler.setFormatter(formatter)
            thread_handler.setLevel(root_logger.level)
            root_logger.addHandler(thread_handler)
            logger.info(f"Thread log file attached: {thread_log_file}")
        except Exception as e:
            logger.warning(f"Failed to attach thread log handler: {e}")

    # Create cancellation token
    cancel_token = await cancellation_manager.create_token(
        thread_id,
        metadata={
            "model": model,
            "input_preview": input_text[:100],
            # Used for internal-auth per-user isolation in admin/debug endpoints.
            "user_id": user_id,
        },
    )

    # Set up event emitter for tool visualization
    emitter = await get_emitter(thread_id)
    event_queue: asyncio.Queue = asyncio.Queue()

    async def tool_event_listener(event):
        """Forward tool events to the queue for SSE streaming."""
        await event_queue.put(event)

    emitter.on_event(tool_event_listener)

    _trace: Any = None
    try:
        logger.info(f"Agent stream started | Thread: {thread_id} | Model: {model}")
        logger.debug(f"  Input: {input_text[:100]}...")

        mode_info = _normalize_search_mode(search_mode)
        metrics = metrics_registry.start(
            thread_id, model=model, route=mode_info.get("mode", "")
        )
        safe_deepsearch_config = _safe_research_deepsearch_config(
            deepsearch_config or {}
        )
        if "retrieval_policy" not in safe_deepsearch_config:
            safe_deepsearch_config["retrieval_policy"] = build_retrieval_policy(
                user_id=user_id or "",
                config={"configurable": {"user_id": user_id or ""}},
            )

        # Load unified long-term memory as request-scoped hidden context.
        messages: list[Any] = []
        memory_source_candidates: list[dict[str, Any]] = []
        memory_retrieval_payload: dict[str, Any] = {}
        if settings.memory_enabled:
            try:
                memory_result = get_memory_service().retrieve(
                    user_id=user_id or "default",
                    query=input_text,
                    include_context=True,
                )
                memory_source_candidates = _memory_source_candidates(memory_result)
                memory_retrieval_payload = {
                    "record_ids": [
                        str(getattr(record, "id", ""))
                        for record in (getattr(memory_result, "records", []) or [])
                        if str(getattr(record, "id", ""))
                    ],
                    "source_candidates": memory_source_candidates,
                    "scoring": list(getattr(memory_result, "scoring", []) or []),
                    "usage": (
                        "Memory is research context only; memory-backed sources must be "
                        "verified in the current run before citation."
                    ),
                }
                if memory_result.context:
                    messages.append(
                        SystemMessage(
                            content=memory_result.context,
                            additional_kwargs={"hide_from_ui": True, "memory_context": True},
                        )
                    )
            except Exception as e:
                logger.warning("[Memory] Retrieval skipped: %s", e)
        if memory_source_candidates:
            safe_deepsearch_config["memory_source_candidates"] = memory_source_candidates
        if memory_retrieval_payload:
            safe_deepsearch_config["memory_retrieval"] = memory_retrieval_payload

        runtime_bundle = build_research_runtime(
            ResearchRuntimeRequest(
                input_text=input_text,
                thread_id=thread_id,
                model=model,
                mode_info=mode_info,
                user_id=user_id,
                images=images,
                research_brief=research_brief if isinstance(research_brief, dict) else None,
                context_messages=messages,
                deepsearch_config=safe_deepsearch_config,
                base_configurable={
                    "thread_id": thread_id,
                    "run_id": run_id or thread_id,
                    "model": model,
                    "search_mode": mode_info,
                    "user_id": user_id,
                    "allow_interrupts": bool(checkpointer),
                    "tool_approval": settings.tool_approval or False,
                    "human_review": settings.human_review or False,
                    "max_revisions": settings.max_revisions,
                },
            )
        )
        initial_state = runtime_bundle.initial_state
        initial_state["cancel_token_id"] = thread_id
        initial_state["is_cancelled"] = False
        config = runtime_bundle.config
        safe_deepsearch_config = runtime_bundle.deepsearch_config
        runtime_context = config.get("configurable", {}).get("runtime_context")
        run_id = getattr(runtime_context, "run_id", thread_id)
        run_manager.start(
            run_id=run_id,
            thread_id=thread_id,
            model=model,
            route=mode_info.get("mode", ""),
            user_id=user_id or "",
            workspace=runtime_bundle.workspace,
            metadata={"input_preview": input_text[:200]},
        )

        async def _drain_pending_tool_events() -> None:
            while not event_queue.empty():
                try:
                    tool_event = event_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                if tool_event.type == ToolEvent.TOOL_START:
                    yield_event = await format_stream_event(
                        "tool_start", tool_event.data
                    )
                elif tool_event.type == ToolEvent.TOOL_SCREENSHOT:
                    yield_event = await format_stream_event(
                        "screenshot", tool_event.data
                    )
                elif tool_event.type == ToolEvent.TOOL_RESULT:
                    yield_event = await format_stream_event(
                        "tool_result", tool_event.data
                    )
                elif tool_event.type == ToolEvent.TOOL_ERROR:
                    yield_event = await format_stream_event(
                        "tool_error", tool_event.data
                    )
                elif tool_event.type == ToolEvent.TASK_UPDATE:
                    yield_event = await format_stream_event(
                        "task_update", tool_event.data
                    )
                elif tool_event.type == ToolEvent.TASK_CREATE:
                    yield_event = await format_stream_event(
                        "task_create", tool_event.data
                    )
                elif tool_event.type == ToolEvent.THINKING:
                    yield_event = await format_stream_event("thinking", tool_event.data)
                elif tool_event.type == ToolEvent.RESEARCH_NODE_START:
                    yield_event = await format_stream_event(
                        "research_node_start", tool_event.data
                    )
                elif tool_event.type == ToolEvent.RESEARCH_NODE_COMPLETE:
                    yield_event = await format_stream_event(
                        "research_node_complete", tool_event.data
                    )
                elif tool_event.type == ToolEvent.RESEARCH_TREE_UPDATE:
                    yield_event = await format_stream_event(
                        "research_tree_update", tool_event.data
                    )
                elif tool_event.type == ToolEvent.PLAN_GRAPH_UPDATE:
                    yield_event = await format_stream_event(
                        "plan_graph_update", tool_event.data
                    )
                elif tool_event.type == ToolEvent.REPLAN_REQUESTED:
                    yield_event = await format_stream_event(
                        "replan_requested", tool_event.data
                    )
                elif tool_event.type == ToolEvent.REPLAN_APPLIED:
                    yield_event = await format_stream_event(
                        "replan_applied", tool_event.data
                    )
                elif tool_event.type == ToolEvent.QUALITY_UPDATE:
                    yield_event = await format_stream_event(
                        "quality_update", tool_event.data
                    )
                elif tool_event.type == ToolEvent.SEARCH:
                    yield_event = await format_stream_event("search", tool_event.data)
                else:
                    continue

                yield yield_event

        # Send initial status
        yield await format_stream_event(
            "status",
            {
                "text": "Initializing research agent...",
                "step": "init",
                "thread_id": thread_id,
            },
        )

        # Initialize tracing context for this request
        _trace = trace_request(thread_id)
        _trace.__enter__()

        # Stream graph execution
        graph_events = research_graph.astream_events(initial_state, config=config)
        graph_iter = graph_events.__aiter__()

        while True:
            async for queued_chunk in _drain_pending_tool_events():
                yield queued_chunk

            # Check cancellation status
            if cancel_token.is_cancelled:
                logger.info(f"Stream cancelled for thread {thread_id}")
                yield await format_stream_event(
                    "cancelled",
                    {"message": "Task was cancelled by user", "thread_id": thread_id},
                )
                return

            next_event_task = asyncio.create_task(graph_iter.__anext__())
            try:
                while True:
                    done, _pending = await asyncio.wait({next_event_task}, timeout=0.05)
                    async for queued_chunk in _drain_pending_tool_events():
                        yield queued_chunk

                    if cancel_token.is_cancelled:
                        next_event_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await next_event_task
                        logger.info(f"Stream cancelled for thread {thread_id}")
                        yield await format_stream_event(
                            "cancelled",
                            {
                                "message": "Task was cancelled by user",
                                "thread_id": thread_id,
                            },
                        )
                        return

                    if done:
                        break

                event = next_event_task.result()
            except StopAsyncIteration:
                break

            event_type = event.get("event")
            name = event.get("name", "") or event.get("run_name", "")
            data_dict = event.get("data", {})
            node_name = name.lower() if isinstance(name, str) else ""

            # Handle different event types
            if event_type in {"on_chain_start", "on_node_start", "on_graph_start"}:
                event_count += 1
                metrics.mark_event(event_type, node_name)
                emit_main_text = _should_emit_main_text_for_node(node_name)
                # Emit a short, safe narrative line to make the accordion feel more "DeepSeek-like".
                try:
                    intro = _thinking_intro_for_node(node_name, use_zh=use_zh)
                    intro = _sanitize_thinking_text(intro, max_len=240)
                    if intro and intro != last_thinking_text:
                        last_thinking_text = intro
                        yield await format_stream_event(
                            "thinking",
                            {"text": intro, "node": node_name},
                        )
                except Exception:
                    pass
                if "clarify" in node_name:
                    logger.debug(f"  Clarify node started | Thread: {thread_id}")
                    yield await format_stream_event(
                        "status",
                        {
                            "text": "Checking if clarification is needed...",
                            "step": "clarifying",
                        },
                    )
                elif "research_brief" in node_name:
                    logger.debug(f"  Research brief node started | Thread: {thread_id}")
                    yield await format_stream_event(
                        "status",
                        {"text": "Drafting research brief...", "step": "planning"},
                    )
                elif "classify_complexity" in node_name:
                    logger.debug(f"  Complexity classifier started | Thread: {thread_id}")
                    yield await format_stream_event(
                        "status",
                        {"text": "Analyzing task complexity...", "step": "planning"},
                    )
                elif "planner" in node_name:
                    logger.debug(f"  Planning node started | Thread: {thread_id}")
                    yield await format_stream_event(
                        "status",
                        {"text": "Creating research plan...", "step": "planning"},
                    )
                elif "deepsearch" in node_name:
                    logger.debug(f"  Deep research node started | Thread: {thread_id}")
                    text = (
                        "正在进行 Deep Research（多轮检索→阅读→汇总），可能需要几分钟…"
                        if use_zh
                        else "Running Deep Research (iterative search → read → synthesize)…"
                    )
                    yield await format_stream_event(
                        "status",
                        {"text": text, "step": "deep_research"},
                    )
                elif "supervisor" in node_name:
                    logger.debug(f"  Supervisor node started | Thread: {thread_id}")
                    yield await format_stream_event(
                        "status",
                        {
                            "text": "Orchestrating research strategy...",
                            "step": "supervisor",
                        },
                    )
                elif "researcher" in node_name and "research_supervisor" not in node_name:
                    logger.debug(f"  Researcher node started | Thread: {thread_id}")
                    yield await format_stream_event(
                        "status",
                        {"text": "Conducting research...", "step": "researching"},
                    )
                elif "compress_research" in node_name:
                    logger.debug(f"  Compression node started | Thread: {thread_id}")
                    yield await format_stream_event(
                        "status",
                        {"text": "Compressing research findings...", "step": "compressing"},
                    )
                elif "perform_parallel_search" in node_name or "search" in node_name:
                    logger.debug(f"  Search node started | Thread: {thread_id}")
                    yield await format_stream_event(
                        "status",
                        {"text": "Conducting research...", "step": "researching"},
                    )
                elif "final_report" in node_name or "writer" in node_name:
                    logger.debug(f"  Writer node started | Thread: {thread_id}")
                    yield await format_stream_event(
                        "status",
                        {"text": "Synthesizing findings...", "step": "writing"},
                    )
            elif event_type in {"on_chain_end", "on_node_end", "on_graph_end"}:
                output = (
                    data_dict.get("output", {}) if isinstance(data_dict, dict) else {}
                )
                metrics.mark_event(event_type, node_name)

                # Record tracing span for node completion
                record_span(
                    name=name or node_name or "unknown",
                    kind=SpanKind.NODE,
                    attributes={"event_type": event_type},
                )

                # Extract messages from output
                if isinstance(output, dict):
                    # Interrupt handling
                    interrupts = output.get("__interrupt__")
                    if interrupts:
                        was_interrupted = True
                        yield await format_stream_event(
                            "interrupt",
                            {
                                "thread_id": thread_id,
                                "prompts": _serialize_interrupts(interrupts),
                            },
                        )
                        return

                    # Optional "thinking summary" (safe progress narrative) — keep separate from main answer.
                    if _should_emit_thinking_summary_for_node(
                        node_name
                    ) and not output.get("is_complete"):
                        try:
                            messages = output.get("messages", [])
                            for msg in messages or []:
                                content = (
                                    msg.content if hasattr(msg, "content") else str(msg)
                                )
                                safe = _sanitize_thinking_text(content)
                                if not safe or _looks_like_structured_blob(safe):
                                    continue
                                yield await format_stream_event(
                                    "thinking",
                                    {"text": safe, "node": node_name},
                                )
                        except Exception:
                            pass

                    # Check for completion and final report artifact.
                    is_graph_complete = (
                        output.get("is_complete") or
                        event_type == "on_graph_end"
                    )
                    final_report = output.get("final_report", "")
                    report_format = output.get("report_format", "markdown")
                    quality_summary = output.get("quality_summary")
                    if isinstance(quality_summary, dict) and quality_summary:
                        final_quality_summary = quality_summary
                    else:
                        deepsearch_artifacts = output.get("deepsearch_artifacts", {})
                        if isinstance(deepsearch_artifacts, dict):
                            nested_quality = deepsearch_artifacts.get("quality_summary")
                            if isinstance(nested_quality, dict) and nested_quality:
                                final_quality_summary = nested_quality

                    if is_graph_complete and final_report:
                        if final_report:
                            try:
                                candidates: list[dict[str, Any]] = []
                                scraped_content = output.get("scraped_content")
                                if isinstance(scraped_content, list):
                                    candidates.extend(scraped_content)
                                raw_sources = output.get("sources")
                                if isinstance(raw_sources, list):
                                    candidates.extend(raw_sources)

                                sources = extract_message_sources(candidates)
                                if sources:
                                    yield await format_stream_event(
                                        "sources", {"items": sources}
                                    )
                            except Exception:
                                pass

                            yield await format_stream_event(
                                "completion",
                                {
                                    "content": final_report,
                                    "format": report_format,
                                },
                            )

                            # Emit as artifact with format-aware type
                            artifact_type = (
                                "html-report" if report_format == "html" else "report"
                            )
                            artifact_title = (
                                "Research Report (HTML)"
                                if report_format == "html"
                                else "Research Report"
                            )
                            yield await format_stream_event(
                                "artifact",
                                {
                                    "id": f"report_{datetime.now().timestamp()}",
                                    "type": artifact_type,
                                    "title": artifact_title,
                                    "content": final_report,
                                    "format": report_format,
                                },
                            )
            elif event_type == "on_tool_start":
                tool_name = name or str(data_dict.get("name", "") or "") or "unknown"
                tool_input = data_dict.get("input", {})
                tool_call_id = str(event.get("run_id") or "") or None

                args_preview = _compact_tool_args(tool_input)
                payload: dict[str, Any] = {
                    "name": tool_name,
                    "status": "running",
                }
                if tool_call_id:
                    payload["toolCallId"] = tool_call_id
                if args_preview:
                    payload["args"] = args_preview
                    query = args_preview.get("query")
                    if isinstance(query, str) and query:
                        payload["query"] = query

                yield await format_stream_event("tool", payload)

            elif event_type == "on_tool_error":
                tool_name = name or str(data_dict.get("name", "") or "") or "unknown"
                tool_input = data_dict.get("input", {})
                tool_call_id = str(event.get("run_id") or "") or None

                args_preview = _compact_tool_args(tool_input)
                payload: dict[str, Any] = {
                    "name": tool_name,
                    "status": "failed",
                }
                if tool_call_id:
                    payload["toolCallId"] = tool_call_id
                if args_preview:
                    payload["args"] = args_preview
                    query = args_preview.get("query")
                    if isinstance(query, str) and query:
                        payload["query"] = query

                yield await format_stream_event("tool", payload)

            elif event_type == "on_tool_end":
                tool_name = name or str(data_dict.get("name", "") or "") or "unknown"
                output = data_dict.get("output", {})
                tool_call_id = str(event.get("run_id") or "") or None

                payload: dict[str, Any] = {
                    "name": tool_name,
                    "status": "completed",
                }
                if tool_call_id:
                    payload["toolCallId"] = tool_call_id

                yield await format_stream_event("tool", payload)

                # Record tracing span for tool call
                record_span(
                    name=tool_name,
                    kind=SpanKind.TOOL_CALL,
                    attributes={"tool_call_id": tool_call_id or ""},
                )

                # Check for artifacts from code execution
                if tool_name == "execute_python_code" and isinstance(output, dict):
                    image_data = output.get("image")

                    if image_data:
                        yield await format_stream_event(
                            "artifact",
                            {
                                "id": f"art_{datetime.now().timestamp()}",
                                "type": "chart",
                                "title": "Generated Visualization",
                                "content": "Chart generated from Python code",
                                "image": image_data,
                            },
                        )
                # Browser screenshots (optional Playwright)
                if tool_name == "browser_screenshot" and isinstance(output, dict):
                    image_data = output.get("image")
                    url = output.get("url", "")
                    if image_data:
                        yield await format_stream_event(
                            "artifact",
                            {
                                "id": f"art_{datetime.now().timestamp()}",
                                "type": "chart",
                                "title": "Browser Screenshot",
                                "content": url or "Screenshot",
                                "image": image_data,
                            },
                        )
                # Sandbox browser tools (E2B + Playwright CDP)
                if tool_name.startswith("sb_browser_") and isinstance(output, dict):
                    image_data = output.get("image")
                    url = output.get("url", "")
                    if isinstance(image_data, str) and image_data.strip():
                        yield await format_stream_event(
                            "artifact",
                            {
                                "id": f"art_{datetime.now().timestamp()}",
                                "type": "chart",
                                "title": f"Sandbox Browser ({tool_name})",
                                "content": url or tool_name,
                                "image": image_data,
                            },
                        )

            elif event_type in {"on_chat_model_stream", "on_llm_stream"}:
                # Stream LLM tokens
                chunk = data_dict.get("chunk") or data_dict.get("output")
                if chunk is not None:
                    content = None
                    if hasattr(chunk, "content"):
                        content = chunk.content
                    elif isinstance(chunk, dict):
                        content = chunk.get("content")
                    if content and emit_main_text:
                        yield await format_stream_event("text", {"content": content})

        async for queued_chunk in _drain_pending_tool_events():
            yield queued_chunk

        # Send final completion
        duration = time.time() - start_time
        cancel_token.mark_completed()
        metrics_registry.finish(thread_id, cancelled=False)
        run_manager.finish(
            thread_id,
            status=RunStatus.completed,
            token_summary=get_token_summary(config),
            quality_summary=final_quality_summary,
            workspace=runtime_bundle.workspace,
        )
        logger.info(
            f"вњ?Agent stream completed | Thread: {thread_id} | "
            f"Events: {event_count} | Duration: {duration:.2f}s"
        )
        yield await format_stream_event(
            "done",
            {
                "timestamp": datetime.now().isoformat(),
                "metrics": (
                    metrics_registry.get(thread_id).to_dict()
                    if metrics_registry.get(thread_id)
                    else {}
                ),
            },
        )

    except asyncio.CancelledError:
        duration = time.time() - start_time
        metrics_registry.finish(thread_id, cancelled=True)
        run_manager.finish(
            thread_id,
            status=RunStatus.cancelled,
            token_summary=get_token_summary(config) if "config" in locals() else {},
            quality_summary=final_quality_summary,
            workspace=runtime_bundle.workspace if "runtime_bundle" in locals() else {},
        )
        logger.info(
            f"? Agent stream cancelled | Thread: {thread_id} | Duration: {duration:.2f}s"
        )
        yield await format_stream_event(
            "cancelled",
            {
                "message": "Task was cancelled",
                "thread_id": thread_id,
                "duration": duration,
            },
        )

    except Exception as e:
        duration = time.time() - start_time
        cancel_token.mark_failed(str(e))
        metrics_registry.finish(thread_id, cancelled=False)
        run_manager.finish(
            thread_id,
            status=RunStatus.failed,
            error=str(e),
            token_summary=get_token_summary(config) if "config" in locals() else {},
            quality_summary=final_quality_summary,
            workspace=runtime_bundle.workspace if "runtime_bundle" in locals() else {},
        )
        logger.error(
            f"? Agent stream error | Thread: {thread_id} | "
            f"Duration: {duration:.2f}s | Error: {e!s}",
            exc_info=True,
        )
        yield await format_stream_event("error", {"message": str(e)})

    finally:
        # Finalize tracing
        if _trace is not None:
            try:
                _trace.__exit__(None, None, None)
            except Exception:
                pass

        # Cleanup event emitter listener
        try:
            emitter.off_event(tool_event_listener)
            await remove_emitter(thread_id)
        except Exception:
            pass
        # ???????
        active_streams.pop(thread_id, None)
        if thread_handler:
            try:
                root_logger.removeHandler(thread_handler)
                thread_handler.close()
            except Exception:
                pass
        # Clean up browser sessions when the run is truly finished.
        # If the graph interrupted (HITL), keep sessions so /api/interrupt/resume can continue.
        if not was_interrupted:
            try:
                browser_sessions.reset(thread_id)
            except Exception:
                pass
            try:
                asyncio.create_task(
                    asyncio.to_thread(sandbox_browser_sessions.reset, thread_id)
                )
            except Exception:
                try:
                    sandbox_browser_sessions.reset(thread_id)
                except Exception:
                    pass


def _stream_agent_events_call(input_text: str, **kwargs: Any):
    try:
        signature = inspect.signature(stream_agent_events)
        params = signature.parameters
        accepts_var_kwargs = any(
            param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()
        )
        if not accepts_var_kwargs:
            kwargs = {key: value for key, value in kwargs.items() if key in params}
    except (TypeError, ValueError):
        pass
    return stream_agent_events(input_text, **kwargs)


@app.post("/api/interrupt/resume")
async def resume_interrupt(request: Request, payload: GraphInterruptResumeRequest):
    """
    Resume a LangGraph execution after an interrupt.
    """
    if not checkpointer:
        raise HTTPException(status_code=400, detail="Interrupts require a checkpointer")

    mode_info = _normalize_search_mode(payload.search_mode)
    model = (payload.model or settings.primary_model).strip()
    # Fast path: avoid invoking the graph when no checkpoint exists for this thread.
    if not payload.thread_id or not str(payload.thread_id).strip():
        raise HTTPException(status_code=400, detail="thread_id is required")
    _require_thread_owner(request, payload.thread_id)
    existing = checkpointer.get_tuple(
        {"configurable": {"thread_id": payload.thread_id}}
    )
    if not existing:
        raise HTTPException(
            status_code=404, detail="No checkpoint found for this thread_id"
        )
    config = {
        "configurable": {
            "thread_id": payload.thread_id,
            "model": model,
            "search_mode": mode_info,
            "allow_interrupts": True,
            "tool_approval": settings.tool_approval or False,
            "human_review": settings.human_review or False,
            "max_revisions": settings.max_revisions,
        },
        "recursion_limit": 50,
    }

    try:
        resume_payload = _normalize_interrupt_resume_payload(payload.payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    result = await research_graph.ainvoke(Command(resume=resume_payload), config=config)
    interrupts = _serialize_interrupts(result.get("__interrupt__"))
    if interrupts:
        return {"status": "interrupted", "interrupts": interrupts}

    final_report = result.get("final_report", "")
    return ResearchMessageResponse(
        id=f"msg_{datetime.now().timestamp()}",
        content=final_report,
        timestamp=datetime.now().isoformat(),
    )


@app.get("/api/tools/registry", response_model=ToolRegistryResponse)
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


@app.get("/api/search/providers", response_model=SearchProvidersResponse)
async def get_search_providers():
    """Expose multi-search provider availability, health, and circuit-breaker state."""
    orchestrator = get_search_orchestrator()
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
                    consecutive_failures=int(
                        circuit.get("consecutive_failures", 0) or 0
                    ),
                    opened_for_seconds=circuit.get("opened_for_seconds"),
                    resets_in_seconds=circuit.get("resets_in_seconds"),
                    total_calls=int(circuit.get("total_calls", 0) or 0),
                    attempted_calls=int(circuit.get("attempted_calls", 0) or 0),
                    success_count=int(circuit.get("success_count", 0) or 0),
                    failure_count=int(circuit.get("failure_count", 0) or 0),
                    skipped_open_count=int(
                        circuit.get("skipped_open_count", 0) or 0
                    ),
                    retry_count=int(circuit.get("retry_count", 0) or 0),
                    circuit_open_count=int(
                        circuit.get("circuit_open_count", 0) or 0
                    ),
                    last_failure=circuit_last_failure,
                    last_failure_age_seconds=circuit.get(
                        "last_failure_age_seconds"
                    ),
                ),
            )
        )

    return {"providers": providers}


@app.post("/api/search/providers/reset", response_model=SearchProvidersResetResponse)
async def reset_search_providers():
    """Reset the global multi-search orchestrator (provider stats + circuit breaker state)."""
    from tools.search.multi_search import reset_search_orchestrator

    reset_search_orchestrator()
    return {"reset": True}


@app.get("/api/llm/reliability", response_model=LLMReliabilityResponse)
async def get_llm_reliability():
    """Expose LLM provider retry and circuit-breaker state."""
    return {"providers": llm_reliability_manager.snapshot()}


@app.post("/api/llm/reliability/reset", response_model=LLMReliabilityResetResponse)
async def reset_llm_reliability():
    """Reset LLM provider retry and circuit-breaker state."""
    llm_reliability_manager.reset()
    return {"reset": True}


@app.get("/api/search/cache/stats", response_model=SearchCacheStatsResponse)
async def get_search_cache_stats():
    """Return in-memory search cache statistics (LRU + TTL)."""
    from agent.core.search_cache import get_search_cache

    cache = get_search_cache()
    return {"stats": cache.stats()}


@app.post("/api/search/cache/clear", response_model=SearchCacheClearResponse)
async def clear_search_cache_endpoint():
    """Clear the in-memory search cache (best-effort)."""
    from agent.core.search_cache import clear_search_cache

    clear_search_cache()
    return {"cleared": True}


@app.get("/api/runs")
async def list_runs(request: Request):
    """List in-memory run metrics (per thread)."""
    runs_by_id = {str(run.get("run_id")): run for run in metrics_registry.all()}
    for run in run_manager.all():
        thread_key = str(run.get("thread_id") or run.get("run_id"))
        merged = dict(runs_by_id.get(thread_key, {}))
        merged.update(run)
        if "run_id" not in merged:
            merged["run_id"] = thread_key
        runs_by_id[thread_key] = merged
    runs = list(runs_by_id.values())
    internal_key = (getattr(settings, "internal_api_key", "") or "").strip()
    if internal_key:
        principal_id = (getattr(request.state, "principal_id", "") or "").strip()
        runs = [
            run
            for run in runs
            if (
                get_thread_owner(str(run.get("thread_id") or run.get("run_id") or ""))
                or ""
            ).strip()
            == principal_id
        ]
    return {"runs": runs}


@app.post("/api/runs/background")
async def submit_background_run(request: Request, payload: BackgroundRunSubmitRequest):
    """Submit a long-running research job without holding an SSE connection."""
    if not settings.background_runs_enabled:
        raise HTTPException(
            status_code=403,
            detail="Background runs are disabled. Set BACKGROUND_RUNS_ENABLED=true.",
        )
    owner_id = (getattr(request.state, "principal_id", "") or payload.user_id or "").strip()
    idem_key = _idempotency_key(request)
    thread_id = (payload.thread_id or "").strip()
    if not thread_id and idem_key:
        thread_id = _stable_id_from_idempotency_key(
            "bg",
            key=idem_key,
            user_id=owner_id or payload.user_id or "anonymous",
        )
    if not thread_id:
        thread_id = f"bg_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
    if owner_id:
        set_thread_owner(thread_id, owner_id)
    idempotency_record = _begin_idempotency(
        request,
        scope="POST /api/runs/background",
        user_id=owner_id or payload.user_id or "anonymous",
        payload={
            "thread_id": thread_id,
            "query": payload.query,
            "model": payload.model or settings.primary_model,
            "search_mode": payload.search_mode,
            "user_id": payload.user_id,
            "deepsearch_config": payload.deepsearch_config,
            "retrieval_policy": payload.retrieval_policy,
            "skill_ids": payload.skill_ids,
            "webhook_url": payload.webhook_url,
            "research_brief": payload.research_brief,
        },
    )
    replay = _idempotent_response(idempotency_record)
    if replay is not None:
        return replay
    mode_info = _normalize_search_mode(payload.search_mode)
    safe_deepsearch_config = _safe_research_deepsearch_config(
        payload.deepsearch_config or {}
    )
    try:
        reject_legacy_source_routing((payload.deepsearch_config or {}).get("source_routing"))
        safe_deepsearch_config["retrieval_policy"] = build_retrieval_policy(
            payload.retrieval_policy or safe_deepsearch_config.get("retrieval_policy"),
            user_id=payload.user_id or owner_id,
            config={
                "configurable": {
                    **safe_deepsearch_config,
                    "user_id": payload.user_id or owner_id,
                }
            },
        )
    except LegacySourceRoutingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload.skill_ids:
        safe_deepsearch_config["skill_ids"] = payload.skill_ids
    if payload.webhook_url:
        safe_deepsearch_config["webhook_url"] = payload.webhook_url
    existing_record = run_manager.get(thread_id)
    if existing_record and isinstance(existing_record.metadata, dict):
        injected_sources = existing_record.metadata.get("user_injected_sources")
        if isinstance(injected_sources, list) and injected_sources:
            safe_deepsearch_config["user_injected_sources"] = injected_sources
    images = [
        image.model_dump()
        for image in (payload.images or [])
        if isinstance(image, ImagePayload)
    ]
    try:
        status = await background_run_manager.submit(
            BackgroundRunRequest(
                input_text=payload.query,
                thread_id=thread_id,
                model=(payload.model or settings.primary_model).strip(),
                search_mode=mode_info,
                images=images,
                user_id=payload.user_id,
                deepsearch_config=safe_deepsearch_config,
                research_brief=payload.research_brief or None,
            ),
            stream_factory=stream_agent_events,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response_payload = {"status": "queued", "run": status}
    idempotency_store.complete(idempotency_record, response=response_payload)
    return response_payload


class RunEvidenceSummary(BaseModel):
    sources_count: int
    unsupported_claims_count: int
    freshness_ratio_30d: Optional[float] = None
    citation_coverage: Optional[float] = None
    query_coverage_score: Optional[float] = None
    freshness_warning: Optional[str] = None
    claim_verifier_total: Optional[int] = None
    claim_verifier_verified: Optional[int] = None
    claim_verifier_unsupported: Optional[int] = None
    claim_verifier_contradicted: Optional[int] = None


class RunMetricsResponse(BaseModel):
    run_id: str
    model: str
    route: str = ""
    started_at: str
    ended_at: Optional[str] = None
    duration_ms: float
    event_count: int
    nodes_started: dict[str, int]
    nodes_completed: dict[str, int]
    errors: list[str]
    cancelled: bool
    evidence_summary: RunEvidenceSummary
    status: Optional[str] = None
    token_summary: dict[str, Any] = Field(default_factory=dict)
    quality_summary: dict[str, Any] = Field(default_factory=dict)


class RunEventResponse(BaseModel):
    run_id: str
    thread_id: str
    seq: int
    type: str
    status: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""


class RunEventsResponse(BaseModel):
    thread_id: str
    after_seq: int = 0
    count: int = 0
    events: list[RunEventResponse] = Field(default_factory=list)


def _build_run_evidence_summary(thread_id: str) -> RunEvidenceSummary:
    sources_count = 0
    unsupported_claims_count = 0
    freshness_ratio_30d: Optional[float] = None
    citation_coverage: Optional[float] = None
    query_coverage_score: Optional[float] = None
    freshness_warning: Optional[str] = None
    claim_verifier_total: Optional[int] = None
    claim_verifier_verified: Optional[int] = None
    claim_verifier_unsupported: Optional[int] = None
    claim_verifier_contradicted: Optional[int] = None

    if not checkpointer:
        return RunEvidenceSummary(
            sources_count=sources_count,
            unsupported_claims_count=unsupported_claims_count,
            freshness_ratio_30d=freshness_ratio_30d,
            citation_coverage=citation_coverage,
            query_coverage_score=query_coverage_score,
            freshness_warning=freshness_warning,
            claim_verifier_total=claim_verifier_total,
            claim_verifier_verified=claim_verifier_verified,
            claim_verifier_unsupported=claim_verifier_unsupported,
            claim_verifier_contradicted=claim_verifier_contradicted,
        )

    try:
        from common.session_manager import get_session_manager

        manager = get_session_manager(checkpointer)
        session_state = manager.get_session_state(thread_id)
        if not session_state:
            raise ValueError("session not found")

        artifacts = session_state.deepsearch_artifacts or {}
        if not isinstance(artifacts, dict):
            raise TypeError("deepsearch_artifacts is not a dict")

        sources = artifacts.get("sources", [])
        if isinstance(sources, list):
            sources_count = len(sources)

        claims = artifacts.get("claims", [])
        if isinstance(claims, list):
            for claim in claims:
                if not isinstance(claim, dict):
                    continue
                status = (claim.get("status") or "").strip().lower()
                if status in ("unsupported", "contradicted"):
                    unsupported_claims_count += 1

        freshness_summary = artifacts.get("freshness_summary", {})
        if isinstance(freshness_summary, dict):
            ratio = freshness_summary.get("fresh_30_ratio")
            if ratio is not None:
                try:
                    freshness_ratio_30d = float(ratio)
                except (TypeError, ValueError):
                    freshness_ratio_30d = None

        quality_summary = artifacts.get("quality_summary", {})
        if isinstance(quality_summary, dict):
            raw_citation = quality_summary.get(
                "citation_coverage", quality_summary.get("citation_coverage_score")
            )
            if raw_citation is not None:
                try:
                    citation_coverage = float(raw_citation)
                except (TypeError, ValueError):
                    citation_coverage = None

            raw_query_coverage = quality_summary.get("query_coverage_score")
            if raw_query_coverage is None:
                nested_coverage = quality_summary.get("query_coverage")
                if isinstance(nested_coverage, dict):
                    raw_query_coverage = nested_coverage.get("score")
            if raw_query_coverage is None:
                nested_query_coverage = artifacts.get("query_coverage")
                if isinstance(nested_query_coverage, dict):
                    raw_query_coverage = nested_query_coverage.get("score")
            if raw_query_coverage is not None:
                try:
                    query_coverage_score = float(raw_query_coverage)
                except (TypeError, ValueError):
                    query_coverage_score = None

            freshness_warning_raw = quality_summary.get("freshness_warning")
            if isinstance(freshness_warning_raw, str) and freshness_warning_raw.strip():
                freshness_warning = freshness_warning_raw.strip()

            def _maybe_int(value: Any) -> Optional[int]:
                if value is None:
                    return None
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None

            claim_verifier_total = _maybe_int(
                quality_summary.get("claim_verifier_total")
            )
            claim_verifier_verified = _maybe_int(
                quality_summary.get("claim_verifier_verified")
            )
            claim_verifier_unsupported = _maybe_int(
                quality_summary.get("claim_verifier_unsupported")
            )
            claim_verifier_contradicted = _maybe_int(
                quality_summary.get("claim_verifier_contradicted")
            )

    except Exception:
        # Evidence summary is best-effort; never fail the metrics endpoint for this.
        pass

    return RunEvidenceSummary(
        sources_count=sources_count,
        unsupported_claims_count=unsupported_claims_count,
        freshness_ratio_30d=freshness_ratio_30d,
        citation_coverage=citation_coverage,
        query_coverage_score=query_coverage_score,
        freshness_warning=freshness_warning,
        claim_verifier_total=claim_verifier_total,
        claim_verifier_verified=claim_verifier_verified,
        claim_verifier_unsupported=claim_verifier_unsupported,
        claim_verifier_contradicted=claim_verifier_contradicted,
    )


@app.get("/api/runs/{thread_id}", response_model=RunMetricsResponse)
async def get_run_metrics(thread_id: str, request: Request):
    """Get metrics for a specific run/thread."""
    internal_key = (getattr(settings, "internal_api_key", "") or "").strip()
    if internal_key:
        principal_id = (getattr(request.state, "principal_id", "") or "").strip()
        owner_id = (get_thread_owner(thread_id) or "").strip()
        if owner_id and principal_id and owner_id != principal_id:
            raise HTTPException(status_code=403, detail="Forbidden")

    metrics = metrics_registry.get(thread_id)
    if not metrics:
        record = run_manager.get(thread_id)
        if not record:
            raise HTTPException(status_code=404, detail="Run not found")
        payload = {
            "run_id": record.run_id,
            "model": record.model,
            "route": record.route,
            "started_at": record.created_at,
            "ended_at": record.updated_at,
            "duration_ms": 0.0,
            "event_count": 0,
            "nodes_started": {},
            "nodes_completed": {},
            "errors": [record.error] if record.error else [],
            "cancelled": record.status == RunStatus.cancelled,
        }
    else:
        payload = metrics.to_dict()
    record = run_manager.get(thread_id)
    if record:
        payload["status"] = record.status.value
        payload["token_summary"] = record.token_summary
        payload["quality_summary"] = record.quality_summary
    return RunMetricsResponse(
        **payload,
        evidence_summary=_build_run_evidence_summary(thread_id),
    )


@app.get("/api/runs/{thread_id}/events", response_model=RunEventsResponse)
async def get_run_events(
    thread_id: str,
    request: Request,
    after_seq: int = 0,
    limit: int = 500,
):
    """Return persisted run events after a sequence number."""
    _require_thread_owner(request, thread_id)
    events = run_manager.events_after(thread_id, after_seq=after_seq, limit=limit)
    return {
        "thread_id": thread_id,
        "after_seq": int(after_seq or 0),
        "count": len(events),
        "events": events,
    }


@app.get("/api/runs/{thread_id}/events/sse")
async def replay_run_events_sse(
    thread_id: str,
    request: Request,
    after_seq: int = 0,
    limit: int = 500,
):
    """Replay persisted run events as standard SSE frames."""
    _require_thread_owner(request, thread_id)

    last_event_id = (request.headers.get("Last-Event-ID") or "").strip()
    if last_event_id:
        try:
            after_seq = max(int(after_seq or 0), int(last_event_id))
        except ValueError:
            pass

    async def _replay_generator():
        for event in run_manager.events_after(
            thread_id,
            after_seq=after_seq,
            limit=limit,
        ):
            payload = event.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            event_type = str(event.get("type") or payload.get("type") or "event")
            yield format_sse_event(
                event=event_type,
                data=payload,
                event_id=int(event.get("seq") or 0),
            )

    return StreamingResponse(
        _replay_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Thread-ID": thread_id,
        },
    )


@app.get("/api/runs/{thread_id}/background")
async def get_background_run(thread_id: str, request: Request):
    _require_thread_owner(request, thread_id)
    status = background_run_manager.status(thread_id)
    if not run_manager.get(thread_id):
        raise HTTPException(status_code=404, detail="Run not found")
    return {"run": status}


def _request_user_id(request: Request, explicit_user_id: str | None = None) -> str:
    internal_key = (getattr(settings, "internal_api_key", "") or "").strip()
    principal_id = (getattr(request.state, "principal_id", "") or "").strip()
    if internal_key and principal_id:
        explicit = (explicit_user_id or "").strip()
        if explicit and explicit != principal_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        return principal_id
    return (explicit_user_id or settings.memory_user_id or "default_user").strip()


@app.get("/api/library/status")
async def document_library_status():
    return get_document_library().status()


@app.post("/api/library/documents")
async def upload_library_document(
    request: Request,
    file: UploadFile = File(...),
    user_id: Optional[str] = None,
):
    owner_id = _request_user_id(request, user_id)
    try:
        data = await file.read()
        idem_key = _idempotency_key(request)
        idempotency_record = _begin_idempotency(
            request,
            scope="POST /api/library/documents",
            user_id=owner_id,
            payload={
                "filename": file.filename or "document.txt",
                "content_type": file.content_type or "",
                "sha256": hashlib.sha256(data or b"").hexdigest(),
            },
        )
        replay = _idempotent_response(idempotency_record)
        if replay is not None:
            return replay
        result = get_document_library().upload_document(
            user_id=owner_id,
            filename=file.filename or "document.txt",
            content_type=file.content_type or "",
            data=data,
            idempotency_key=idem_key,
        )
        response_payload = {"document": result}
        idempotency_store.complete(idempotency_record, response=response_payload)
        return response_payload
    except DocumentLibraryUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Document upload failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/library/documents")
async def list_library_documents(request: Request, user_id: Optional[str] = None):
    owner_id = _request_user_id(request, user_id)
    try:
        return {"documents": get_document_library().list_documents(user_id=owner_id)}
    except DocumentLibraryUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Document list failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.delete("/api/library/documents/{document_id}")
async def delete_library_document(
    document_id: str,
    request: Request,
    user_id: Optional[str] = None,
):
    owner_id = _request_user_id(request, user_id)
    try:
        deleted = get_document_library().delete_document(
            user_id=owner_id,
            document_id=document_id,
        )
        if not deleted:
            raise HTTPException(status_code=404, detail="Document not found")
        return {"deleted": True, "document_id": document_id}
    except HTTPException:
        raise
    except DocumentLibraryUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Document delete failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/library/documents/{document_id}/reindex")
async def reindex_library_document(
    document_id: str,
    request: Request,
    user_id: Optional[str] = None,
):
    owner_id = _request_user_id(request, user_id)
    try:
        return {
            "document": get_document_library().reindex_document(
                user_id=owner_id,
                document_id=document_id,
            )
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Document not found") from exc
    except DocumentLibraryUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Document reindex failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/library/search")
async def search_library(
    request: Request,
    q: str,
    limit: int = 5,
    user_id: Optional[str] = None,
):
    owner_id = _request_user_id(request, user_id)
    try:
        results = get_document_library().search(
            user_id=owner_id,
            query=q,
            limit=limit,
        )
        return {"query": q, "results": results}
    except DocumentLibraryUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Document search failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class RetrievalSearchRequest(BaseModel):
    query: str
    user_id: Optional[str] = None
    retrieval_policy: dict[str, Any] = Field(default_factory=dict)
    max_results: int = 8


@app.post("/api/retrieval/search")
async def retrieval_search_debug(request: Request, payload: RetrievalSearchRequest):
    owner_id = _request_user_id(request, payload.user_id)
    try:
        policy = build_retrieval_policy(payload.retrieval_policy, user_id=owner_id)
        result = await retrieve_sources(
            payload.query,
            max_results=payload.max_results,
            config={"configurable": {"user_id": owner_id, "retrieval_policy": policy}},
        )
        return result
    except LegacySourceRoutingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/runs/{thread_id}/background/cancel")
async def cancel_background_run(
    thread_id: str, request: Request, payload: CancelRequest | None = None
):
    _require_thread_owner(request, thread_id)
    reason = payload.reason if payload else "User requested cancellation"
    await background_run_manager.cancel(thread_id, reason or "User requested cancellation")
    return {"status": "cancelled", "thread_id": thread_id}


@app.post("/api/runs/{thread_id}/resume")
async def mark_run_resumed(thread_id: str, request: Request):
    _require_thread_owner(request, thread_id)
    record = run_manager.get(thread_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    if settings.background_runs_enabled and (record.metadata or {}).get("background"):
        try:
            run = await background_run_manager.resume(
                thread_id,
                stream_factory=stream_agent_events,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"status": "resumed", "run": run}
    record = run_manager.update(thread_id, status=RunStatus.resumed)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"status": "resumed", "run": record.to_dict()}


@app.post("/api/runs/{thread_id}/sources")
async def inject_user_sources(
    thread_id: str,
    request: Request,
    payload: UserSourceInjectionRequest,
):
    _require_thread_owner(request, thread_id)
    sources = [source for source in payload.sources if isinstance(source, dict)]
    if not sources:
        raise HTTPException(status_code=400, detail="sources must contain at least one item")
    record = run_manager.get(thread_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    metadata = dict(record.metadata or {})
    injected = list(metadata.get("user_injected_sources") or [])
    injected.extend(
        {
            **source,
            "source": source.get("source") or "user",
            "injected_at": datetime.now().isoformat(),
            "note": payload.note,
            "requires_current_run_verification": True,
        }
        for source in sources
    )
    run_manager.update(
        thread_id,
        metadata={
            "user_injected_sources": injected,
            "last_user_source_injection_at": datetime.now().isoformat(),
        },
    )
    return {"status": "accepted", "thread_id": thread_id, "source_count": len(injected)}


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    data = generate_latest()
    return StreamingResponse(iter([data]), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/memory/status", response_model=MemoryStatusResponse)
async def memory_status():
    """Return unified memory backend status and configuration."""
    if not getattr(settings, "memory_enabled", True):
        return {
            "backend": getattr(settings, "memory_backend", "postgres"),
            "available": False,
            "pgvector_available": False,
            "embedding_model": getattr(settings, "memory_embedding_model", ""),
            "embedding_dim": int(getattr(settings, "memory_embedding_dim", 0) or 0),
            "record_count": 0,
            "entity_count": 0,
            "relation_count": 0,
            "skill_evolution_count": 0,
            "error": "memory disabled",
        }
    try:
        service = get_memory_service()
        status = service.status()
        return {
            **status,
            "embedding_model": service.embedding_model,
            "embedding_dim": service.embedding_dim,
        }
    except Exception as exc:
        return {
            "backend": getattr(settings, "memory_backend", "postgres"),
            "available": False,
            "pgvector_available": False,
            "embedding_model": getattr(settings, "memory_embedding_model", ""),
            "embedding_dim": int(getattr(settings, "memory_embedding_dim", 0) or 0),
            "record_count": 0,
            "entity_count": 0,
            "relation_count": 0,
            "skill_evolution_count": 0,
            "error": str(exc),
        }


# ── DeerFlow-aligned: Channels API ──

class ChannelStatusResponse(BaseModel):
    service_running: bool
    channels: dict[str, Any] = {}


class ChannelRestartResponse(BaseModel):
    success: bool
    message: str


@app.get("/api/channels", response_model=ChannelStatusResponse)
async def channels_status():
    """Get the status of all IM channels."""
    try:
        from channels.service import get_channel_service
        svc = get_channel_service()
        if svc is None:
            return ChannelStatusResponse(service_running=False, channels={})
        status = svc.get_status()
        return ChannelStatusResponse(**status)
    except Exception:
        return ChannelStatusResponse(service_running=False, channels={})


@app.post("/api/channels/{name}/restart", response_model=ChannelRestartResponse)
async def channels_restart(name: str):
    """Restart a specific IM channel."""
    try:
        from channels.service import get_channel_service
        svc = get_channel_service()
        if svc is None:
            raise HTTPException(status_code=503, detail="Channel service not running")
        success = await svc.restart_channel(name)
        if success:
            return ChannelRestartResponse(success=True, message=f"Channel {name} restarted")
        raise HTTPException(status_code=404, detail=f"Channel {name} not found")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── DeerFlow-aligned: Skills API ──

class SkillListResponse(BaseModel):
    skills: list[dict[str, Any]]


class SkillDetailResponse(BaseModel):
    name: str
    description: str
    category: str
    enabled: bool
    allowed_tools: list[str] | None = None
    license: str | None = None
    path: str
    container_path: str


class SkillInstallResponse(BaseModel):
    success: bool
    skill_name: str
    message: str


class SkillHistoryResponse(BaseModel):
    name: str
    history: list[dict[str, Any]]


@app.get("/api/skills", response_model=SkillListResponse)
async def list_skills(enabled_only: bool = False):
    """List all available skills (public and custom)."""
    try:
        from agent.skills.storage import get_or_new_skill_storage
        storage = get_or_new_skill_storage()
        skills = storage.load_skills(enabled_only=enabled_only)
        return {
            "skills": [
                {
                    "name": s.name,
                    "description": s.description,
                    "category": s.category.value,
                    "enabled": s.enabled,
                    "allowed_tools": s.allowed_tools,
                    "license": s.license,
                    "path": s.skill_path,
                }
                for s in skills
            ]
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/skills/{name}", response_model=SkillDetailResponse)
async def get_skill(name: str):
    """Get details for a specific skill, including container path info."""
    try:
        from agent.skills.storage import get_or_new_skill_storage
        storage = get_or_new_skill_storage()
        skills = storage.load_skills()
        for s in skills:
            if s.name == name:
                return {
                    "name": s.name,
                    "description": s.description,
                    "category": s.category.value,
                    "enabled": s.enabled,
                    "allowed_tools": s.allowed_tools,
                    "license": s.license,
                    "path": s.skill_path,
                    "container_path": s.get_container_file_path(storage.get_container_root()),
                }
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.put("/api/skills/{name}")
async def update_skill(name: str, enabled: bool = True):
    """Enable or disable a skill by updating extensions_config.json."""
    try:
        from common.extensions_config import ExtensionsConfig, reload_extensions_config
        config = ExtensionsConfig.from_file()
        config.skills[name] = {"enabled": enabled}
        # Write updated config back
        import json
        config_path = ExtensionsConfig.resolve_config_path()
        if config_path:
            config_path.write_text(json.dumps(config.model_dump(by_alias=True), indent=2, ensure_ascii=False), encoding="utf-8")
        reload_extensions_config()
        from agent.skills.prompt import clear_skills_prompt_cache
        clear_skills_prompt_cache()
        return {"name": name, "enabled": enabled}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/skills/install", response_model=SkillInstallResponse)
async def install_skill(file: UploadFile = None):
    """Install a skill from a .skill ZIP archive."""
    if not file or not file.filename or not file.filename.endswith(".skill"):
        raise HTTPException(status_code=400, detail="A .skill file is required")
    try:
        import tempfile
        from pathlib import Path

        from agent.skills.storage import get_or_new_skill_storage
        storage = get_or_new_skill_storage()
        with tempfile.NamedTemporaryFile(suffix=".skill", delete=False) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = Path(tmp.name)
        try:
            result = await storage.ainstall_skill_from_archive(str(tmp_path))
        finally:
            tmp_path.unlink(missing_ok=True)
        from agent.skills.prompt import clear_skills_prompt_cache
        clear_skills_prompt_cache()
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/skills/custom")
async def list_custom_skills():
    """List only custom (user-authored) skills."""
    try:
        from agent.skills.storage import get_or_new_skill_storage
        storage = get_or_new_skill_storage()
        skills = storage.load_skills(enabled_only=False)
        custom = [s for s in skills if s.category.value == "custom"]
        return {
            "skills": [
                {
                    "name": s.name,
                    "description": s.description,
                    "enabled": s.enabled,
                    "allowed_tools": s.allowed_tools,
                    "license": s.license,
                    "path": s.skill_path,
                }
                for s in custom
            ]
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/skills/custom/{name}")
async def get_custom_skill(name: str):
    """Get the full SKILL.md content for a custom skill."""
    try:
        from agent.skills.storage import get_or_new_skill_storage
        storage = get_or_new_skill_storage()
        if not storage.custom_skill_exists(name):
            raise HTTPException(status_code=404, detail=f"Custom skill '{name}' not found")
        content = storage.read_custom_skill(name)
        return {"name": name, "content": content}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.put("/api/skills/custom/{name}")
async def edit_custom_skill(name: str, content: str):
    """Edit a custom skill's SKILL.md content."""
    try:
        from agent.skills.storage import get_or_new_skill_storage
        storage = get_or_new_skill_storage()
        storage.ensure_custom_skill_is_editable(name)
        storage.validate_skill_markdown_content(name, content)
        from agent.skills.security_scanner import scan_skill_content
        result = await scan_skill_content(content, executable=False, location=f"{name}/SKILL.md")
        if result.decision == "block":
            raise HTTPException(status_code=400, detail=f"Security scan blocked: {result.reason}")
        storage.write_custom_skill(name, "SKILL.md", content)
        from agent.skills.prompt import clear_skills_prompt_cache
        clear_skills_prompt_cache()
        return {"name": name, "message": f"Custom skill '{name}' updated"}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete("/api/skills/custom/{name}")
async def delete_custom_skill(name: str):
    """Delete a custom skill."""
    try:
        from agent.skills.storage import get_or_new_skill_storage
        storage = get_or_new_skill_storage()
        storage.delete_custom_skill(name)
        from agent.skills.prompt import clear_skills_prompt_cache
        clear_skills_prompt_cache()
        return {"name": name, "message": f"Custom skill '{name}' deleted"}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/skills/custom/{name}/history", response_model=SkillHistoryResponse)
async def get_skill_history(name: str):
    """Get the edit history for a custom skill."""
    try:
        from agent.skills.storage import get_or_new_skill_storage
        storage = get_or_new_skill_storage()
        history = storage.read_history(name)
        return {"name": name, "history": history}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/skills/custom/{name}/rollback")
async def rollback_skill(name: str, version: int = 0):
    """Rollback a custom skill to a previous version in its history."""
    try:
        from agent.skills.storage import get_or_new_skill_storage
        storage = get_or_new_skill_storage()
        history = storage.read_history(name)
        if not history:
            raise HTTPException(status_code=404, detail=f"No history found for skill '{name}'")
        if version < 0 or version >= len(history):
            raise HTTPException(status_code=400, detail=f"Invalid version index {version}. Valid range: 0-{len(history)-1}")
        entry = history[version]
        prev = entry.get("prev_content")
        if not prev:
            raise HTTPException(status_code=400, detail="Selected version has no previous content to restore")
        storage.write_custom_skill(name, "SKILL.md", prev)
        from agent.skills.prompt import clear_skills_prompt_cache
        clear_skills_prompt_cache()
        return {"name": name, "message": f"Rolled back to version {version}", "version": version}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# Unified Memory API


@app.get("/api/memory", response_model=MemoryListResponse)
async def list_memory_records(
    request: Request,
    query: str = "",
    type: str = "",
    scope: str = "",
    limit: int = 50,
    user_id: Optional[str] = None,
):
    """List unified memory records for the current user."""
    if not getattr(settings, "memory_enabled", True):
        raise HTTPException(status_code=503, detail="Memory is disabled")
    resolved_user_id = _request_user_id(request, user_id)
    try:
        records = get_memory_service().list_records(
            user_id=resolved_user_id,
            query=query,
            type=type,
            scope=scope,
            limit=max(1, min(int(limit or 50), 200)),
        )
        return {
            "records": [record.to_dict() for record in records],
            "count": len(records),
            "query": query,
            "user_id": resolved_user_id,
        }
    except MemoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/memory/records", response_model=MemoryRecordResponse)
async def create_memory_record(request: Request, payload: MemoryRecordCreateRequest):
    """Manually add or update a unified memory record."""
    if not getattr(settings, "memory_enabled", True):
        raise HTTPException(status_code=503, detail="Memory is disabled")
    content = (payload.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    resolved_user_id = _request_user_id(request, payload.user_id)
    try:
        record = MemoryRecord(
            user_id=resolved_user_id,
            scope=payload.scope,
            type=payload.type,
            content=content,
            summary=payload.summary,
            confidence=payload.confidence,
            importance=payload.importance,
            quality_score=payload.quality_score,
            source_thread_id=payload.source_thread_id,
            source_run_id=payload.source_run_id,
            source_evidence_ids=payload.source_evidence_ids,
            source_urls=payload.source_urls,
            valid_from=payload.valid_from,
            valid_to=payload.valid_to,
            expires_at=payload.expires_at,
            metadata={**payload.metadata, "manual": True},
        )
        saved = get_memory_service().upsert_record(record)
        return {"record": saved.to_dict()}
    except MemoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete("/api/memory/records/{record_id}", response_model=MemoryDeleteResponse)
async def delete_memory_record(
    record_id: str,
    request: Request,
    user_id: Optional[str] = None,
):
    """Soft-delete a unified memory record."""
    if not getattr(settings, "memory_enabled", True):
        raise HTTPException(status_code=503, detail="Memory is disabled")
    resolved_user_id = _request_user_id(request, user_id)
    try:
        deleted = get_memory_service().delete_record(
            record_id,
            user_id=resolved_user_id,
        )
        if not deleted:
            raise HTTPException(status_code=404, detail="Memory record not found")
        return {"deleted": True}
    except HTTPException:
        raise
    except MemoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/memory/retrieve", response_model=MemoryRetrieveResponse)
async def retrieve_memory(request: Request, payload: MemoryRetrieveRequest):
    """Debug hybrid memory recall and score decomposition."""
    if not getattr(settings, "memory_enabled", True):
        raise HTTPException(status_code=503, detail="Memory is disabled")
    query = (payload.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    resolved_user_id = _request_user_id(request, payload.user_id)
    try:
        result = get_memory_service().retrieve(
            user_id=resolved_user_id,
            query=query,
            type=payload.type,
            scope=payload.scope,
            limit=max(1, min(int(payload.limit or 12), 50)),
            include_context=payload.include_context,
        )
        return {
            "records": [record.to_dict() for record in result.records],
            "entities": [entity.to_dict() for entity in result.entities],
            "relations": [relation.to_dict() for relation in result.relations],
            "scoring": result.scoring,
            "context": result.context,
            "user_id": resolved_user_id,
        }
    except MemoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/memory/graph", response_model=MemoryGraphResponse)
async def get_memory_graph(
    request: Request,
    entity: str = "",
    limit: int = 50,
    user_id: Optional[str] = None,
):
    """Return memory entity graph records and relations."""
    if not getattr(settings, "memory_enabled", True):
        raise HTTPException(status_code=503, detail="Memory is disabled")
    resolved_user_id = _request_user_id(request, user_id)
    try:
        return get_memory_service().graph(
            user_id=resolved_user_id,
            entity=entity,
            limit=max(1, min(int(limit or 50), 200)),
        )
    except MemoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/memory/skill-evolution", response_model=MemorySkillEvolutionResponse)
async def list_memory_skill_evolution(
    request: Request,
    limit: int = 50,
    user_id: Optional[str] = None,
):
    """List auto skill-evolution proposals produced from procedural memory."""
    if not getattr(settings, "memory_enabled", True):
        raise HTTPException(status_code=503, detail="Memory is disabled")
    resolved_user_id = _request_user_id(request, user_id)
    try:
        proposals = get_memory_service().list_skill_evolution(
            user_id=resolved_user_id,
            limit=max(1, min(int(limit or 50), 200)),
        )
        return {"proposals": proposals, "count": len(proposals)}
    except MemoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/config/public", response_model=PublicConfigResponse)
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
                "gpt" in lowered or lowered.startswith("o1") or lowered.startswith("o3")
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

        primary = _norm(settings.primary_model)
        reasoning = _norm(settings.reasoning_model)
        base_url = _norm(settings.openai_base_url).lower()
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

        # Always allow the configured primary model.
        _add(primary)

        # Provider-aware suggestions.
        if _is_deepseek_family(primary) or "deepseek" in base_url:
            _add("deepseek-v4-flash")
            _add("deepseek-reasoner")
            if _is_deepseek_family(reasoning):
                _add(reasoning)
        elif _is_anthropic_family(primary):
            if (settings.anthropic_api_key or "").strip():
                _add("claude-sonnet-4-5-20250514")
                _add("claude-opus-4-20250514")
                _add("claude-sonnet-4-20250514")
            if _is_anthropic_family(reasoning):
                _add(reasoning)
        elif _is_openai_family(primary):
            if (settings.openai_api_key or "").strip():
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

        # Include task-specific overrides if they are explicitly configured.
        for attr in (
            "planner_model",
            "researcher_model",
            "writer_model",
            "evaluator_model",
            "critic_model",
        ):
            _add(_norm(getattr(settings, attr, "")))

        return opts

    return {
        "version": app.version,
        "defaults": {
            "port": settings.port,
            "primary_model": settings.primary_model,
            "reasoning_model": settings.reasoning_model,
        },
        "models": {
            "default": settings.primary_model,
            "options": _public_model_options(),
        },
        "features": {
            "mcp_enabled": bool(mcp_enabled),
            "sandbox_mode": settings.sandbox_mode,
            "prometheus_enabled": bool(settings.enable_prometheus),
            "tracing_enabled": bool(settings.enable_tracing),
            "persistence_mode": "persistent" if _checkpointer_type != "memory" else "ephemeral",
        },
        "streaming": {
            "research": {"protocol": "sse", "endpoint": "/api/research/sse"},
        },
    }


# ==================== Report Export API ====================


class ExportRequest(BaseModel):
    """Export request for generating reports in various formats."""

    format: str = "html"  # html, pdf, docx
    title: Optional[str] = None


@app.get("/api/export/templates", response_model=ExportTemplatesResponse)
async def list_export_templates():
    """
    List available export templates.

    Note: This route must be registered before `/api/export/{thread_id}`.
    Otherwise, the dynamic route will capture `templates` as a thread_id and
    this endpoint becomes unreachable.
    """
    return {
        "templates": [
            {
                "id": "default",
                "name": "Default",
                "description": "Standard research report format with SoulSearcher branding",
            },
            {
                "id": "academic",
                "name": "Academic",
                "description": "Formal serif font style for research papers with proper citations",
            },
            {
                "id": "business",
                "name": "Business",
                "description": "Professional business report with gradient header and modern layout",
            },
            {
                "id": "minimal",
                "name": "Minimal",
                "description": "Clean, distraction-free formatting focused on content",
            },
        ]
    }


@app.get("/api/export/{thread_id}")
async def export_report_endpoint(
    thread_id: str,
    request: Request,
    format: str = "html",
    title: Optional[str] = None,
    template: str = "default",
):
    """
    Export a research report for a given thread.

    Args:
        thread_id: Thread ID to export report for
        format: Output format (html, pdf, docx)
        title: Optional custom title for the report
        template: Template style (default, academic, business, minimal)
    """
    if not checkpointer:
        raise HTTPException(status_code=400, detail="No checkpointer configured")

    try:
        _require_thread_owner(request, thread_id)
        config = {"configurable": {"thread_id": thread_id}}
        checkpoint = checkpointer.get_tuple(config)
        if not checkpoint:
            raise HTTPException(
                status_code=404, detail=f"No checkpoint found for thread {thread_id}"
            )

        state = checkpoint.checkpoint.get("channel_values", {})
        final_report = state.get("final_report", "")
        if not final_report:
            raise HTTPException(
                status_code=404, detail="No report found for this thread"
            )

        scraped = state.get("scraped_content", [])
        extracted_sources = []
        try:
            if isinstance(scraped, list):
                extracted_sources = extract_message_sources(scraped)
        except Exception:
            extracted_sources = []

        source_urls = [
            s.get("url")
            for s in extracted_sources
            if isinstance(s, dict) and isinstance(s.get("url"), str) and s.get("url")
        ]

        report_title = title or "Research Report"
        format_lower = format.lower().strip()

        if format_lower == "json":
            deepsearch_artifacts = state.get("deepsearch_artifacts", {}) or {}
            if not isinstance(deepsearch_artifacts, dict):
                deepsearch_artifacts = {}

            sources_payload = deepsearch_artifacts.get("sources")
            if not isinstance(sources_payload, list):
                sources_payload = extracted_sources

            claims_payload = deepsearch_artifacts.get("claims")
            if not isinstance(claims_payload, list):
                claims_payload = []
                try:
                    from agent.workflows.claim_verifier import ClaimVerifier

                    scraped_list = scraped if isinstance(scraped, list) else []
                    passages_payload = deepsearch_artifacts.get("passages")
                    passages_list = (
                        passages_payload if isinstance(passages_payload, list) else None
                    )

                    if (
                        (scraped_list or passages_list)
                        and isinstance(final_report, str)
                        and final_report.strip()
                    ):
                        verifier = ClaimVerifier()
                        checks = verifier.verify_report(
                            final_report,
                            scraped_list,
                            passages=passages_list,
                        )
                        claims_payload = [
                            {
                                "claim": c.claim,
                                "status": c.status.value,
                                "evidence_urls": c.evidence_urls,
                                "evidence_passages": c.evidence_passages,
                                "score": c.score,
                                "notes": c.notes,
                            }
                            for c in checks
                        ]
                except Exception:
                    claims_payload = []

            quality_payload = deepsearch_artifacts.get("quality_summary")
            if not isinstance(quality_payload, dict):
                quality_payload = state.get("quality_summary", {}) or {}
                if not isinstance(quality_payload, dict):
                    quality_payload = {}

            return StarletteJSONResponse(
                status_code=200,
                content={
                    "thread_id": thread_id,
                    "title": report_title,
                    "report": final_report,
                    "research_brief": deepsearch_artifacts.get("research_brief", {}),
                    "sources": sources_payload,
                    "evidence_items": deepsearch_artifacts.get("evidence_items", []),
                    "citation_annotations": deepsearch_artifacts.get(
                        "citation_annotations", []
                    ),
                    "timeline": deepsearch_artifacts.get("timeline", []),
                    "supervisor_decisions": deepsearch_artifacts.get(
                        "supervisor_decisions", []
                    ),
                    "worker_runs": deepsearch_artifacts.get("worker_runs", []),
                    "intermediate_steps": deepsearch_artifacts.get(
                        "intermediate_steps", []
                    ),
                    "continue_requests": deepsearch_artifacts.get(
                        "continue_requests", []
                    ),
                    "claims": claims_payload,
                    "quality": quality_payload,
                    "quality_details": deepsearch_artifacts.get("quality_details", {}),
                    "quality_gates": deepsearch_artifacts.get("quality_gates", []),
                    "exported_at": datetime.now().isoformat(),
                },
                headers={
                    "Content-Disposition": f'attachment; filename="report_{thread_id}.json"'
                },
            )

        if format_lower == "html":
            from tools.export import export_report as do_export

            html_content = do_export(
                final_report,
                format="html",
                title=report_title,
                thread_id=thread_id,
                sources=source_urls,
            )
            return StreamingResponse(
                iter(
                    [
                        (
                            html_content.encode("utf-8")
                            if isinstance(html_content, str)
                            else html_content
                        )
                    ]
                ),
                media_type="text/html",
                headers={
                    "Content-Disposition": f'inline; filename="report_{thread_id}.html"'
                },
            )

        elif format_lower == "pdf":
            try:
                from tools.export import export_report as do_export

                pdf_bytes = do_export(
                    final_report,
                    format="pdf",
                    title=report_title,
                    thread_id=thread_id,
                    sources=source_urls,
                )
                return StreamingResponse(
                    iter(
                        [
                            (
                                pdf_bytes
                                if isinstance(pdf_bytes, bytes)
                                else pdf_bytes.encode("utf-8")
                            )
                        ]
                    ),
                    media_type="application/pdf",
                    headers={
                        "Content-Disposition": f'attachment; filename="report_{thread_id}.pdf"'
                    },
                )
            except ImportError as e:
                raise HTTPException(
                    status_code=501, detail=f"PDF export requires WeasyPrint: {e}"
                )

        elif format_lower in ("docx", "doc"):
            try:
                from tools.export import export_report as do_export

                docx_bytes = do_export(
                    final_report,
                    format="docx",
                    title=report_title,
                    thread_id=thread_id,
                    sources=source_urls,
                )
                return StreamingResponse(
                    iter(
                        [
                            (
                                docx_bytes
                                if isinstance(docx_bytes, bytes)
                                else docx_bytes.encode("utf-8")
                            )
                        ]
                    ),
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={
                        "Content-Disposition": f'attachment; filename="report_{thread_id}.docx"'
                    },
                )
            except ImportError as e:
                raise HTTPException(
                    status_code=501, detail=f"DOCX export requires python-docx: {e}"
                )

        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported format: {format}. Use html, pdf, or docx.",
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Export error for thread {thread_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Sessions API ====================


class SessionSummary(BaseModel):
    thread_id: str
    status: str
    topic: str
    created_at: str
    updated_at: str
    route: str
    has_report: bool
    revision_count: int
    message_count: int
    owner_id: str = ""
    group_id: str = ""
    visibility: str = "private"


class SessionsListResponse(BaseModel):
    count: int
    sessions: list[SessionSummary]


class EvidenceSource(BaseModel):
    title: str = ""
    url: str
    rawUrl: Optional[str] = None
    domain: Optional[str] = None
    provider: Optional[str] = None
    publishedDate: Optional[str] = None


class EvidenceClaimEvidence(BaseModel):
    url: str
    snippet_hash: Optional[str] = None
    quote: Optional[str] = None
    heading_path: Optional[list[str]] = None


class EvidenceClaim(BaseModel):
    claim: str
    status: str
    evidence_urls: list[str] = []
    evidence_passages: list[EvidenceClaimEvidence] = []
    score: float = 0.0
    notes: str = ""


class FetchedPageItem(BaseModel):
    url: str
    raw_url: str
    method: str
    text: Optional[str] = None
    title: Optional[str] = None
    published_date: Optional[str] = None
    retrieved_at: Optional[str] = None
    markdown: Optional[str] = None
    http_status: Optional[int] = None
    error: Optional[str] = None
    attempts: int = 1


class EvidencePassageItem(BaseModel):
    url: str
    text: str
    start_char: int
    end_char: int
    heading: Optional[str] = None
    heading_path: Optional[list[str]] = None
    page_title: Optional[str] = None
    retrieved_at: Optional[str] = None
    method: Optional[str] = None
    quote: Optional[str] = None
    snippet_hash: Optional[str] = None


class EvidenceItemResponse(BaseModel):
    id: str
    source_type: str
    provider: Optional[str] = None
    url: Optional[str] = None
    document_id: Optional[str] = None
    title: Optional[str] = None
    snippet: Optional[str] = None
    content_ref: Optional[str] = None
    published_date: Optional[str] = None
    retrieved_at: Optional[str] = None
    query: Optional[str] = None
    quality_score: Optional[float] = None
    freshness_score: Optional[float] = None
    citation_id: Optional[str] = None
    metadata: dict[str, Any] = {}


class CitationAnnotationResponse(BaseModel):
    id: str
    citation_id: str
    marker: str
    source_index: Optional[int] = None
    start_char: int
    end_char: int
    section: str = ""
    title: Optional[str] = None
    url: Optional[str] = None
    rawUrl: Optional[str] = None
    domain: Optional[str] = None
    provider: Optional[str] = None
    publishedDate: Optional[str] = None
    evidence_ids: list[str] = []
    occurrence: int = 0


class TimelineEventResponse(BaseModel):
    id: str
    order: int
    event_type: str
    title: str
    timestamp: Optional[str] = None
    query: Optional[str] = None
    result_count: Optional[int] = None
    providers: list[str] = []
    run_index: Optional[int] = None
    url: Optional[str] = None
    rawUrl: Optional[str] = None
    provider: Optional[str] = None
    publishedDate: Optional[str] = None
    citation_id: Optional[str] = None
    source_index: Optional[int] = None
    evidence_id: Optional[str] = None
    source_type: Optional[str] = None
    document_id: Optional[str] = None
    stage: Optional[str] = None
    epoch: Optional[int] = None
    gate_count: Optional[int] = None
    failed_count: Optional[int] = None


class SupervisorDecisionResponse(BaseModel):
    round_index: int
    action: str
    reason: str = ""
    missing_topics: list[str] = []
    next_worker_topics: list[str] = []
    failed_gates: list[str] = []
    quality_snapshot: dict[str, Any] = {}


class WorkerRunResponse(BaseModel):
    worker_id: str
    context_id: str
    topic: str
    focus: str = ""
    queries: list[str] = []
    round_index: int = 0
    result_count: int = 0
    evidence_count: int = 0
    summary: str = ""
    provider_breakdown: dict[str, int] = {}
    status: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    errors: list[str] = []


class IntermediateStepResponse(BaseModel):
    id: str
    order: int
    type: str
    title: Optional[str] = None
    status: Optional[str] = None
    worker_id: Optional[str] = None
    context_id: Optional[str] = None
    round_index: Optional[int] = None
    result_count: Optional[int] = None
    evidence_count: Optional[int] = None
    timestamp: Optional[str] = None
    reason: Optional[str] = None
    missing_topics: list[str] = []


class EvidenceResponse(BaseModel):
    sources: list[EvidenceSource] = []
    claims: list[EvidenceClaim] = []
    quality_summary: dict[str, Any] = {}
    quality_details: dict[str, Any] = {}
    research_brief: dict[str, Any] = {}
    plan_graph: dict[str, Any] = {}
    plan_events: list[dict[str, Any]] = []
    plan_summary: dict[str, Any] = {}
    research_todos: list[dict[str, Any]] = []
    todo_summary: dict[str, Any] = {}
    retrieval_policy: dict[str, Any] = {}
    evidence_store: dict[str, Any] = {}
    access_policy: dict[str, Any] = {}
    quality_gates: list[dict[str, Any]] = []
    evidence_items: list[EvidenceItemResponse] = []
    citation_annotations: list[CitationAnnotationResponse] = []
    timeline: list[TimelineEventResponse] = []
    supervisor_decisions: list[SupervisorDecisionResponse] = []
    worker_runs: list[WorkerRunResponse] = []
    intermediate_steps: list[IntermediateStepResponse] = []
    continue_requests: list[dict[str, Any]] = []
    fetched_pages: list[FetchedPageItem] = []
    passages: list[EvidencePassageItem] = []
    research_pipeline: dict[str, Any] = {}
    stage_runtime: dict[str, Any] = {}
    source_quality: dict[str, Any] = {}
    browser_reader_plan: dict[str, Any] = {}
    worker_orchestration: dict[str, Any] = {}
    branch_diagnostics: dict[str, Any] = {}
    brief_review: dict[str, Any] = {}
    fallback: dict[str, Any] = {}


@app.get("/api/sessions", response_model=SessionsListResponse)
async def list_sessions(
    request: Request,
    limit: int = 50,
    status: Optional[str] = None,
):
    """
    List all research sessions.

    Args:
        limit: Maximum sessions to return
        status: Filter by status (pending, running, completed, cancelled)
    """
    if not checkpointer:
        raise HTTPException(status_code=400, detail="No checkpointer configured")

    try:
        from common.session_manager import get_session_manager

        manager = get_session_manager(checkpointer)
        internal_key = (getattr(settings, "internal_api_key", "") or "").strip()
        user_filter = None
        if internal_key:
            user_filter = (
                getattr(request.state, "principal_id", "") or ""
            ).strip() or "internal"
        sessions = manager.list_sessions(
            limit=limit, status_filter=status, user_id_filter=user_filter
        )

        return {
            "count": len(sessions),
            "sessions": [s.to_dict() for s in sessions],
        }

    except Exception as e:
        logger.error(f"List sessions error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sessions/{thread_id}")
async def get_session(thread_id: str, request: Request):
    """
    Get session info by thread ID.
    """
    if not checkpointer:
        raise HTTPException(status_code=400, detail="No checkpointer configured")

    try:
        from common.session_manager import get_session_manager

        manager = get_session_manager(checkpointer)

        internal_key = (getattr(settings, "internal_api_key", "") or "").strip()
        if internal_key:
            principal_id = (getattr(request.state, "principal_id", "") or "").strip()
            session_state = manager.get_session_state(thread_id)
            if session_state and isinstance(session_state.state, dict):
                owner = session_state.state.get("user_id")
                if (
                    isinstance(owner, str)
                    and owner.strip()
                    and owner.strip() != principal_id
                ):
                    raise HTTPException(status_code=403, detail="Forbidden")

        session = manager.get_session(thread_id)

        if not session:
            raise HTTPException(
                status_code=404, detail=f"Session not found: {thread_id}"
            )

        return session.to_dict()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get session error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sessions/{thread_id}/state")
async def get_session_state(thread_id: str, request: Request):
    """
    Get full session state snapshot.
    """
    if not checkpointer:
        raise HTTPException(status_code=400, detail="No checkpointer configured")

    try:
        from common.session_manager import get_session_manager

        manager = get_session_manager(checkpointer)
        state = manager.get_session_state(thread_id)

        if not state:
            raise HTTPException(
                status_code=404, detail=f"Session not found: {thread_id}"
            )

        internal_key = (getattr(settings, "internal_api_key", "") or "").strip()
        if internal_key:
            principal_id = (getattr(request.state, "principal_id", "") or "").strip()
            owner = (
                state.state.get("user_id") if isinstance(state.state, dict) else None
            )
            if (
                isinstance(owner, str)
                and owner.strip()
                and owner.strip() != principal_id
            ):
                raise HTTPException(status_code=403, detail="Forbidden")

        return state.to_dict()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get session state error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sessions/{thread_id}/evidence", response_model=EvidenceResponse)
async def get_session_evidence(thread_id: str, request: Request):
    """
    Get evidence artifacts (sources + claims + quality summary) for a session.
    """
    if not checkpointer:
        raise HTTPException(status_code=400, detail="No checkpointer configured")

    try:
        from common.session_manager import get_session_manager

        manager = get_session_manager(checkpointer)
        session_state = manager.get_session_state(thread_id)
        if not session_state:
            raise HTTPException(
                status_code=404, detail=f"Session not found: {thread_id}"
            )

        internal_key = (getattr(settings, "internal_api_key", "") or "").strip()
        if internal_key:
            principal_id = (getattr(request.state, "principal_id", "") or "").strip()
            owner = (
                session_state.state.get("user_id")
                if isinstance(session_state.state, dict)
                else None
            )
            if (
                isinstance(owner, str)
                and owner.strip()
                and owner.strip() != principal_id
            ):
                raise HTTPException(status_code=403, detail="Forbidden")

        artifacts = session_state.deepsearch_artifacts or {}
        if not isinstance(artifacts, dict):
            artifacts = {}

        sources = artifacts.get("sources", [])
        claims = artifacts.get("claims", [])
        quality_summary = artifacts.get("quality_summary", {})
        quality_details = artifacts.get("quality_details", {})
        research_brief = artifacts.get("research_brief", {})
        plan_graph = artifacts.get("plan_graph", {})
        plan_graph_events = (
            plan_graph.get("events") if isinstance(plan_graph, dict) else None
        )
        plan_events = (
            plan_graph_events
            if isinstance(plan_graph_events, list)
            else artifacts.get("plan_events", [])
        )
        plan_artifact_summary = artifacts.get("plan_summary", {})
        plan_graph_summary = (
            plan_graph.get("summary") if isinstance(plan_graph, dict) else None
        )
        plan_summary = (
            plan_graph_summary
            if isinstance(plan_graph_summary, dict)
            else plan_artifact_summary
            if isinstance(plan_artifact_summary, dict)
            else {}
        )
        research_todos = artifacts.get("research_todos", [])
        todo_summary = artifacts.get("todo_summary", {})
        quality_gates = artifacts.get("quality_gates", [])
        evidence_items = artifacts.get("evidence_items", [])
        citation_annotations = artifacts.get("citation_annotations", [])
        timeline = artifacts.get("timeline", [])
        supervisor_decisions = artifacts.get("supervisor_decisions", [])
        worker_runs = artifacts.get("worker_runs", [])
        intermediate_steps = artifacts.get("intermediate_steps", [])
        continue_requests = artifacts.get("continue_requests", [])
        fetched_pages = artifacts.get("fetched_pages", [])
        passages = artifacts.get("passages", [])
        research_pipeline = artifacts.get("research_pipeline", {})
        stage_runtime = artifacts.get("stage_runtime", {})
        source_quality = artifacts.get("source_quality", {})
        browser_reader_plan = artifacts.get("browser_reader_plan", {})
        worker_orchestration = artifacts.get("worker_orchestration", {})
        branch_diagnostics = artifacts.get("branch_diagnostics", {})
        brief_review = artifacts.get("brief_review", {})
        fallback = artifacts.get("fallback", {})
        evidence_store = build_evidence_store_snapshot(
            thread_id=thread_id,
            artifacts=artifacts,
            state=session_state.state if isinstance(session_state.state, dict) else {},
        )
        evidence_patch = evidence_store.to_response_patch()
        retrieval_policy = (
            artifacts.get("retrieval_policy")
            or evidence_patch.get("retrieval_policy")
            or {}
        )
        access_policy = (
            artifacts.get("access_policy") or evidence_patch.get("access_policy") or {}
        )

        return {
            "sources": (
                sources
                if isinstance(sources, list)
                else evidence_patch.get("sources", [])
            ),
            "claims": (
                claims if isinstance(claims, list) else evidence_patch.get("claims", [])
            ),
            "quality_summary": (
                quality_summary if isinstance(quality_summary, dict) else {}
            ),
            "quality_details": (
                quality_details if isinstance(quality_details, dict) else {}
            ),
            "research_brief": (
                research_brief if isinstance(research_brief, dict) else {}
            ),
            "plan_graph": (
                plan_graph
                if isinstance(plan_graph, dict)
                else evidence_patch.get("plan_graph", {})
            ),
            "plan_events": (
                plan_events
                if isinstance(plan_events, list)
                else evidence_patch.get("plan_events", [])
            ),
            "plan_summary": (
                plan_summary
                if isinstance(plan_summary, dict)
                else evidence_patch.get("plan_summary", {})
            ),
            "research_todos": (
                research_todos
                if isinstance(research_todos, list)
                else evidence_patch.get("research_todos", [])
            ),
            "todo_summary": (
                todo_summary
                if isinstance(todo_summary, dict)
                else evidence_patch.get("todo_summary", {})
            ),
            "retrieval_policy": (
                retrieval_policy if isinstance(retrieval_policy, dict) else {}
            ),
            "evidence_store": evidence_store.to_dict(),
            "access_policy": access_policy if isinstance(access_policy, dict) else {},
            "quality_gates": (
                quality_gates
                if isinstance(quality_gates, list)
                else evidence_patch.get("quality_gates", [])
            ),
            "evidence_items": (
                evidence_items
                if isinstance(evidence_items, list)
                else evidence_patch.get("evidence_items", [])
            ),
            "citation_annotations": (
                citation_annotations
                if isinstance(citation_annotations, list)
                else evidence_patch.get("citation_annotations", [])
            ),
            "timeline": timeline if isinstance(timeline, list) else [],
            "supervisor_decisions": (
                supervisor_decisions if isinstance(supervisor_decisions, list) else []
            ),
            "worker_runs": worker_runs if isinstance(worker_runs, list) else [],
            "intermediate_steps": (
                intermediate_steps if isinstance(intermediate_steps, list) else []
            ),
            "continue_requests": (
                continue_requests if isinstance(continue_requests, list) else []
            ),
            "fetched_pages": (
                fetched_pages
                if isinstance(fetched_pages, list)
                else evidence_patch.get("fetched_pages", [])
            ),
            "passages": (
                passages
                if isinstance(passages, list)
                else evidence_patch.get("passages", [])
            ),
            "research_pipeline": (
                research_pipeline if isinstance(research_pipeline, dict) else {}
            ),
            "stage_runtime": stage_runtime if isinstance(stage_runtime, dict) else {},
            "source_quality": (
                source_quality if isinstance(source_quality, dict) else {}
            ),
            "browser_reader_plan": (
                browser_reader_plan if isinstance(browser_reader_plan, dict) else {}
            ),
            "worker_orchestration": (
                worker_orchestration if isinstance(worker_orchestration, dict) else {}
            ),
            "branch_diagnostics": (
                branch_diagnostics if isinstance(branch_diagnostics, dict) else {}
            ),
            "brief_review": brief_review if isinstance(brief_review, dict) else {},
            "fallback": fallback if isinstance(fallback, dict) else {},
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get session evidence error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class SessionResumeRequest(BaseModel):
    """Request to resume a session."""

    additional_input: Optional[str] = None
    update_state: Optional[dict[str, Any]] = None


class ContinueResearchRequest(BaseModel):
    target_type: str = Field(..., description="section | claim | source | gap")
    target_id: Optional[str] = None
    target_index: Optional[int] = None
    target_text: Optional[str] = None
    instruction: Optional[str] = None
    strategy: str = "supervisor_workers"


class ContinueResearchResponse(BaseModel):
    success: bool
    thread_id: str
    status: str
    continue_request: dict[str, Any]
    resume_input: str
    update_state: dict[str, Any]
    stream_payload: dict[str, Any]
    resume_state: dict[str, Any]


@app.post(
    "/api/sessions/{thread_id}/continue-research",
    response_model=ContinueResearchResponse,
)
async def continue_research_session(
    thread_id: str,
    request: Request,
    payload: ContinueResearchRequest,
):
    if not checkpointer:
        raise HTTPException(status_code=400, detail="No checkpointer configured")

    try:
        from agent.workflows.interactive_continue import build_continue_research_plan
        from common.session_manager import get_session_manager

        _require_thread_owner(request, thread_id)

        manager = get_session_manager(checkpointer)
        session_state = manager.get_session_state(thread_id)
        if not session_state:
            raise HTTPException(
                status_code=404, detail=f"Session not found: {thread_id}"
            )

        artifacts = dict(session_state.deepsearch_artifacts or {})
        plan = build_continue_research_plan(
            artifacts=artifacts,
            target_type=payload.target_type,
            target_id=payload.target_id or "",
            target_index=payload.target_index,
            target_text=payload.target_text or "",
            instruction=payload.instruction or "",
            strategy=payload.strategy or "supervisor_workers",
        )
        plan_payload = plan.to_dict()
        continue_requests = artifacts.get("continue_requests", [])
        if not isinstance(continue_requests, list):
            continue_requests = []
        continue_requests.append(plan_payload)
        artifacts["continue_requests"] = continue_requests
        update_state = dict(plan.update_state)
        if isinstance(update_state.get("plan_graph"), dict):
            artifacts["plan_graph"] = update_state["plan_graph"]
            artifacts["plan_events"] = update_state.get("plan_events", [])
            try:
                from agent.workflows.plan_graph import summarize_plan_graph

                artifacts["plan_summary"] = summarize_plan_graph(update_state["plan_graph"])
            except Exception:
                artifacts["plan_summary"] = {}
        if isinstance(update_state.get("research_todos"), list):
            artifacts["research_todos"] = update_state["research_todos"]
        if isinstance(update_state.get("todo_summary"), dict):
            artifacts["todo_summary"] = update_state["todo_summary"]
        update_state["deepsearch_artifacts"] = artifacts
        if isinstance(update_state.get("plan_graph"), dict):
            try:
                emitter = await get_emitter(thread_id)
                payload_data = {
                    "reason": "interactive continue research target",
                    "plan_graph": update_state["plan_graph"],
                    "plan_summary": artifacts.get("plan_summary", {}),
                }
                await emitter.emit(ToolEvent.REPLAN_APPLIED, payload_data)
                await emitter.emit(ToolEvent.PLAN_GRAPH_UPDATE, payload_data)
            except Exception:
                pass
        restored_state = manager.build_resume_state(
            thread_id=thread_id,
            additional_input=plan.resume_input,
            update_state=update_state,
        )
        if restored_state is None:
            raise HTTPException(
                status_code=404, detail=f"Session not found: {thread_id}"
            )

        stream_payload = {
            "messages": [{"role": "user", "content": plan.resume_input}],
            "stream": True,
            "search_mode": {
                "useWebSearch": True,
                "useAgent": True,
                "useDeepSearch": True,
            },
            "thread_id": thread_id,
            "deepsearch_strategy": payload.strategy or "supervisor_workers",
        }

        return {
            "success": True,
            "thread_id": thread_id,
            "status": "ready_to_continue",
            "continue_request": plan_payload,
            "resume_input": plan.resume_input,
            "update_state": update_state,
            "stream_payload": stream_payload,
            "resume_state": {
                "route": restored_state.get("route"),
                "research_plan_count": len(restored_state.get("research_plan") or []),
                "has_deepsearch_artifacts": bool(
                    restored_state.get("deepsearch_artifacts")
                ),
                "resumed_from_checkpoint": bool(
                    restored_state.get("resumed_from_checkpoint")
                ),
            },
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Continue research error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sessions/{thread_id}/resume")
async def resume_session(
    thread_id: str,
    request: Request,
    payload: SessionResumeRequest | None = None,
):
    """
    Resume a paused or cancelled research session.
    """
    if not checkpointer:
        raise HTTPException(status_code=400, detail="No checkpointer configured")

    try:
        from common.session_manager import get_session_manager

        _require_thread_owner(request, thread_id)

        manager = get_session_manager(checkpointer)

        # Check if session can be resumed
        can_resume, reason = manager.can_resume(thread_id)
        if not can_resume:
            raise HTTPException(status_code=400, detail=reason)

        # Get current state
        state = manager.get_session_state(thread_id)
        if not state:
            raise HTTPException(
                status_code=404, detail=f"Session not found: {thread_id}"
            )

        restored_state = manager.build_resume_state(
            thread_id=thread_id,
            additional_input=payload.additional_input if payload else None,
            update_state=payload.update_state if payload else None,
        )
        if restored_state is None:
            raise HTTPException(
                status_code=404, detail=f"Session not found: {thread_id}"
            )

        deepsearch_artifacts = restored_state.get("deepsearch_artifacts", {}) or {}
        quality_summary = (
            deepsearch_artifacts.get("quality_summary", {})
            if isinstance(deepsearch_artifacts, dict)
            else {}
        )
        queries = (
            deepsearch_artifacts.get("queries", [])
            if isinstance(deepsearch_artifacts, dict)
            else []
        )
        query_coverage = (
            deepsearch_artifacts.get("query_coverage", {})
            if isinstance(deepsearch_artifacts, dict)
            else {}
        )
        freshness_summary = (
            deepsearch_artifacts.get("freshness_summary", {})
            if isinstance(deepsearch_artifacts, dict)
            else {}
        )
        if not isinstance(query_coverage, dict):
            query_coverage = {}
        if not isinstance(freshness_summary, dict):
            freshness_summary = {}
        query_coverage_score = query_coverage.get("score")
        if query_coverage_score is not None:
            try:
                query_coverage_score = float(query_coverage_score)
            except (TypeError, ValueError):
                query_coverage_score = None
        if query_coverage_score is None and isinstance(quality_summary, dict):
            nested_coverage = quality_summary.get("query_coverage")
            if isinstance(nested_coverage, dict):
                query_coverage_score = nested_coverage.get("score")
            if query_coverage_score is None:
                query_coverage_score = quality_summary.get("query_coverage_score")
            if query_coverage_score is not None:
                try:
                    query_coverage_score = float(query_coverage_score)
                except (TypeError, ValueError):
                    query_coverage_score = None
        freshness_warning = ""
        if isinstance(quality_summary, dict):
            freshness_warning = str(quality_summary.get("freshness_warning") or "")

        # Resume the graph execution
        # Note: Actual resumption depends on the graph implementation
        # This returns info for the client to continue via SSE
        return {
            "success": True,
            "thread_id": thread_id,
            "status": "ready_to_resume",
            "message": f"Session {thread_id} is ready to resume. Use the streaming endpoint with this thread_id.",
            "current_state": {
                "route": state.state.get("route"),
                "revision_count": state.state.get("revision_count", 0),
                "has_report": bool(state.state.get("final_report")),
                "has_deepsearch_artifacts": bool(deepsearch_artifacts),
                "deepsearch_queries": len(queries) if isinstance(queries, list) else 0,
            },
            "deepsearch_resume": {
                "artifacts_restored": bool(deepsearch_artifacts),
                "mode": (
                    deepsearch_artifacts.get("mode")
                    if isinstance(deepsearch_artifacts, dict)
                    else None
                ),
                "quality_summary": (
                    quality_summary if isinstance(quality_summary, dict) else {}
                ),
                "query_coverage_score": query_coverage_score,
                "freshness_warning": freshness_warning,
                "freshness_summary": freshness_summary,
            },
            "resume_state": {
                "route": restored_state.get("route"),
                "revision_count": restored_state.get("revision_count", 0),
                "research_plan_count": len(
                    restored_state.get("research_plan", []) or []
                ),
                "resumed_from_checkpoint": bool(
                    restored_state.get("resumed_from_checkpoint")
                ),
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Resume session error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/sessions/{thread_id}")
async def delete_session(thread_id: str, request: Request):
    """
    Delete a research session.
    """
    if not checkpointer:
        raise HTTPException(status_code=400, detail="No checkpointer configured")

    try:
        from common.session_manager import get_session_manager

        _require_thread_owner(request, thread_id)

        manager = get_session_manager(checkpointer)
        success = manager.delete_session(thread_id)

        if not success:
            raise HTTPException(
                status_code=400, detail=f"Failed to delete session: {thread_id}"
            )

        return {
            "success": True,
            "message": f"Session {thread_id} deleted",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete session error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Collaboration API ====================


class ShareRequest(BaseModel):
    """Request to create a share link."""

    permissions: str = "view"
    expires_hours: Optional[int] = 72


class CommentRequest(BaseModel):
    """Request to add a comment."""

    content: str
    author: str = "anonymous"
    message_id: Optional[str] = None


class SessionComment(BaseModel):
    id: str
    thread_id: str
    message_id: Optional[str] = None
    author: str
    content: str
    created_at: str
    updated_at: str


class CommentsResponse(BaseModel):
    comments: list[SessionComment]
    count: int


class SessionVersion(BaseModel):
    id: str
    thread_id: str
    version_number: int
    label: str
    created_at: str
    snapshot_size: int


class VersionsResponse(BaseModel):
    versions: list[SessionVersion]
    count: int


@app.post("/api/sessions/{thread_id}/share")
async def create_share(thread_id: str, request: Request, req: ShareRequest):
    """Create a share link for a session."""
    try:
        _require_thread_owner(request, thread_id)
        from common.collaboration import create_share_link

        link = create_share_link(
            thread_id=thread_id,
            permissions=req.permissions,
            expires_hours=req.expires_hours,
        )
        return {
            "success": True,
            "share": link,
            "url": f"/share/{link['id']}",
        }
    except Exception as e:
        logger.error(f"Create share error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/share/{share_id}")
async def get_share(share_id: str):
    """Get shared session content."""
    try:
        from common.collaboration import get_share_link

        link = get_share_link(share_id)
        if not link:
            raise HTTPException(
                status_code=404, detail="Share link not found or expired"
            )

        # Get session state if checkpointer available
        session_data = None
        if checkpointer:
            from common.session_manager import get_session_manager

            manager = get_session_manager(checkpointer)
            session_state = manager.get_session_state(link["thread_id"])
            if session_state and isinstance(session_state.state, dict):
                state = session_state.state
                raw_messages = state.get("messages", [])
                messages = []
                if isinstance(raw_messages, list) and raw_messages:
                    # Keep payload bounded for share views.
                    for m in raw_messages[-50:]:
                        try:
                            role = None
                            content = None

                            if isinstance(m, dict):
                                role = m.get("role") or m.get("type") or m.get("name")
                                content = m.get("content")
                            else:
                                role = getattr(m, "role", None) or getattr(
                                    m, "type", None
                                )
                                content = getattr(m, "content", None)

                            if content is None:
                                content = str(m)

                            role_norm = str(role or "unknown").strip().lower()
                            if role_norm in {"human", "user"}:
                                role_norm = "user"
                            elif role_norm in {"ai", "assistant"}:
                                role_norm = "assistant"
                            elif role_norm in {"system"}:
                                role_norm = "system"

                            messages.append(
                                {
                                    "role": role_norm,
                                    "content": str(content),
                                }
                            )
                        except Exception:
                            continue

                title = (
                    state.get("title")
                    or state.get("topic")
                    or state.get("input")
                    or link["thread_id"]
                )
                if not isinstance(title, str) or not title.strip():
                    title = link["thread_id"]

                session_data = {
                    "id": link["thread_id"],
                    "title": title.strip(),
                    "messages": messages,
                }

        return {
            "success": True,
            "share": link,
            "session": session_data,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get share error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/share/{share_id}")
async def delete_share(share_id: str):
    """Delete a share link."""
    try:
        from common.collaboration import delete_share_link

        success = delete_share_link(share_id)
        if not success:
            raise HTTPException(status_code=404, detail="Share link not found")
        return {"success": True, "id": share_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete share error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sessions/{thread_id}/comments")
async def add_comment(thread_id: str, request: Request, req: CommentRequest):
    """Add a comment to a session."""
    try:
        _require_thread_owner(request, thread_id)
        from common.collaboration import add_comment

        comment = add_comment(
            thread_id=thread_id,
            content=req.content,
            author=req.author,
            message_id=req.message_id,
        )
        return {"success": True, "comment": comment}
    except Exception as e:
        logger.error(f"Add comment error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sessions/{thread_id}/comments", response_model=CommentsResponse)
async def get_comments(
    thread_id: str, request: Request, message_id: Optional[str] = None
):
    """Get comments for a session."""
    try:
        _require_thread_owner(request, thread_id)
        from common.collaboration import get_comments

        comments = get_comments(thread_id, message_id)
        return {"comments": comments, "count": len(comments)}
    except Exception as e:
        logger.error(f"Get comments error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sessions/{thread_id}/versions", response_model=VersionsResponse)
async def get_versions(thread_id: str, request: Request):
    """Get version history for a session."""
    try:
        _require_thread_owner(request, thread_id)
        from common.collaboration import list_versions

        versions = list_versions(thread_id)
        return {"versions": versions, "count": len(versions)}
    except Exception as e:
        logger.error(f"Get versions error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sessions/{thread_id}/versions")
async def create_version(thread_id: str, request: Request, label: Optional[str] = None):
    """Create a version snapshot of a session."""
    try:
        if not checkpointer:
            raise HTTPException(status_code=400, detail="No checkpointer configured")

        _require_thread_owner(request, thread_id)

        from common.collaboration import save_version
        from common.session_manager import get_session_manager

        manager = get_session_manager(checkpointer)
        session = manager.get_session(thread_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        # Create snapshot from session state
        state_snapshot = {
            "thread_id": session.thread_id,
            "title": session.title,
            "messages": session.messages,
            "metadata": getattr(session, "metadata", {}),
        }

        version = save_version(thread_id, state_snapshot, label)
        return {"success": True, "version": version}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Create version error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sessions/{thread_id}/restore/{version_id}")
async def restore_version(thread_id: str, version_id: str, request: Request):
    """Restore a session from a version snapshot."""
    try:
        _require_thread_owner(request, thread_id)
        from common.collaboration import get_version_snapshot

        snapshot = get_version_snapshot(version_id)
        if not snapshot:
            raise HTTPException(status_code=404, detail="Version snapshot not found")

        # Note: Full restoration would require re-initializing the session state
        # For now, just return the snapshot for client-side handling
        return {
            "success": True,
            "snapshot": snapshot,
            "message": "Version snapshot retrieved. Client should handle restoration.",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Restore version error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ==================== HITL Interrupt API ====================


class InterruptAction(str, Enum):
    """Actions for interrupt handling."""

    APPROVE = "approve"  # Approve and continue
    MODIFY = "modify"  # Modify state and continue
    REJECT = "reject"  # Reject and stop
    SKIP = "skip"  # Skip this checkpoint


class InterruptResumeRequest(BaseModel):
    """Request to resume from an interrupt point."""

    action: str = "approve"
    modifications: Optional[dict[str, Any]] = None
    feedback: Optional[str] = None


@app.get("/api/interrupt/{thread_id}/status")
async def get_interrupt_status(thread_id: str, request: Request):
    """
    Get the current interrupt status for a session.

    Returns information about whether the session is paused at an interrupt point.
    """
    if not checkpointer:
        raise HTTPException(status_code=400, detail="No checkpointer configured")

    try:
        _require_thread_owner(request, thread_id)
        config = {"configurable": {"thread_id": thread_id}}
        checkpoint_tuple = checkpointer.get_tuple(config)

        if not checkpoint_tuple:
            raise HTTPException(
                status_code=404, detail=f"Session not found: {thread_id}"
            )

        pending_writes = getattr(checkpoint_tuple, "pending_writes", []) or []
        interrupt_items: list[Any] = []
        for entry in pending_writes:
            if not isinstance(entry, (list, tuple)) or len(entry) != 3:
                continue
            _, key, value = entry
            if key != "__interrupt__":
                continue
            if isinstance(value, (list, tuple)):
                interrupt_items.extend(list(value))
            elif value is not None:
                interrupt_items.append(value)

        prompts = _serialize_interrupts(interrupt_items)
        is_interrupted = bool(prompts)

        checkpoint_name: Optional[str] = None
        available_actions: list[str] = []
        first = prompts[0] if prompts else None
        if isinstance(first, dict):
            cp = first.get("checkpoint")
            if isinstance(cp, str) and cp.strip():
                checkpoint_name = cp.strip()

            # Tool approval interrupt (HumanInTheLoopMiddleware) — expose allowed decisions.
            if "action_requests" in first and "review_configs" in first:
                checkpoint_name = checkpoint_name or "tool_approval"
                allowed: set[str] = set()
                for cfg in first.get("review_configs", []) or []:
                    if not isinstance(cfg, dict):
                        continue
                    for d in cfg.get("allowed_decisions", []) or []:
                        if isinstance(d, str) and d.strip():
                            allowed.add(d.strip())
                if allowed:
                    available_actions = sorted(allowed)

        if is_interrupted and not available_actions:
            # Generic interrupts support "approve" (resume as-is) and optional edits
            # (client can send a structured payload like {"content": "..."}).
            available_actions = ["approve", "edit"]

        return {
            "thread_id": thread_id,
            "is_interrupted": is_interrupted,
            "interrupt_node": "",
            "checkpoint_name": checkpoint_name,
            "checkpoint_info": {},
            "available_actions": available_actions if is_interrupted else [],
            "prompts": prompts,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get interrupt status error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/interrupt/{thread_id}/resume")
async def resume_from_interrupt(
    thread_id: str, request: Request, payload: InterruptResumeRequest
):
    """
    Compatibility path for interrupt resume.

    The canonical implementation is /api/interrupt/resume; keep this route as
    a thin adapter so there is only one resume code path.
    """
    action = str(payload.action or "approve").strip().lower()
    resume_payload: dict[str, Any] = {"action": action}
    if payload.modifications:
        resume_payload["modifications"] = payload.modifications
        if action == "modify":
            resume_payload.update(payload.modifications)
    if payload.feedback:
        resume_payload["feedback"] = payload.feedback
    return await resume_interrupt(
        request,
        GraphInterruptResumeRequest(thread_id=thread_id, payload=resume_payload),
    )


@app.post("/api/research/sse")
async def research_sse(request: Request, payload: ResearchRequest):
    """
    Standard SSE research endpoint.

    This endpoint translates the internal stream protocol into standard SSE
    frames (`event:` / `data:`).
    """
    query = (payload.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query is required")

    internal_key = (getattr(settings, "internal_api_key", "") or "").strip()
    principal_id = (getattr(request.state, "principal_id", "") or "").strip()
    user_id = (
        principal_id
        if internal_key and principal_id
        else (payload.user_id or settings.memory_user_id)
    )
    mode_info = _normalize_search_mode(payload.search_mode)
    model = (payload.model or settings.primary_model).strip()
    thread_id = f"thread_{uuid.uuid4().hex}"
    run_id = f"run_{uuid.uuid4().hex}"
    set_thread_owner(thread_id, principal_id or "anonymous")
    run_manager.start(
        run_id=run_id,
        thread_id=thread_id,
        model=model,
        route=mode_info.get("mode", ""),
        user_id=user_id,
        metadata={"input_preview": query[:200]},
    )
    safe_deepsearch_config = _safe_research_deepsearch_config(
        payload.deepsearch_config or {}
    )
    try:
        reject_legacy_source_routing((payload.deepsearch_config or {}).get("source_routing"))
        reject_legacy_source_routing((payload.research_brief or {}).get("source_routing"))
        normalized_retrieval_policy = build_retrieval_policy(
            payload.retrieval_policy or safe_deepsearch_config.get("retrieval_policy"),
            user_id=user_id,
            config={
                "configurable": {
                    **safe_deepsearch_config,
                    "user_id": user_id,
                }
            },
        )
    except LegacySourceRoutingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    safe_deepsearch_config["retrieval_policy"] = normalized_retrieval_policy
    if payload.skill_ids:
        safe_deepsearch_config["skill_ids"] = [
            str(skill_id).strip()
            for skill_id in payload.skill_ids
            if str(skill_id).strip()
        ]
    should_prepare_research_brief = bool(mode_info.get("use_deep"))
    preview_retrieval_policy: dict[str, Any] = normalized_retrieval_policy
    normalized_research_brief: dict[str, Any] = {}
    if should_prepare_research_brief:
        preview_state: dict[str, Any] = {"input": query, "user_id": user_id}
        if isinstance(payload.research_brief, dict) and payload.research_brief:
            preview_state["research_brief"] = payload.research_brief
        preview_config: dict[str, Any] = {
            "configurable": {
                "thread_id": thread_id,
                "model": model,
                "search_mode": mode_info,
                "user_id": user_id,
                **safe_deepsearch_config,
            }
        }
        try:
            preview_brief = build_research_brief(preview_state, preview_config)
            preview_brief.retrieval_policy = preview_retrieval_policy
            normalized_research_brief = preview_brief.to_dict()
            normalized_research_brief["retrieval_policy"] = preview_retrieval_policy
        except Exception as e:
            logger.debug(f"Failed to prepare preview research brief: {e}")
            normalized_research_brief = (
                payload.research_brief
                if isinstance(payload.research_brief, dict)
                else {}
            )

    async def _sse_generator():
        gauge = None
        try:
            gauge = sse_active_connections.labels("research_sse")
            gauge.inc()
        except Exception:
            gauge = None

        try:
            seq = 0
            # Hint clients (EventSource) how long to wait before attempting reconnects.
            try:
                if await request.is_disconnected():
                    return
            except Exception:
                pass
            yield format_sse_retry(2000)
            seq += 1
            if should_prepare_research_brief:
                brief_payload = {
                    "thread_id": thread_id,
                    "research_brief": normalized_research_brief,
                    "retrieval_policy": preview_retrieval_policy,
                }
                stream_payload = {
                    "type": "brief_created",
                    "data": brief_payload,
                    "research_event": build_research_run_event(
                        "brief_created",
                        brief_payload,
                        seq=seq,
                    ),
                }
                persisted = _persist_run_stream_event(
                    run_id=run_id,
                    thread_id=thread_id,
                    seq=seq,
                    event_type="brief_created",
                    payload=stream_payload,
                )
                if persisted:
                    seq = int(persisted.get("seq") or seq)
                    stream_payload = dict(persisted.get("payload") or stream_payload)
                yield format_sse_event(
                    event="brief_created",
                    data=stream_payload,
                    event_id=seq,
                )

            # Deterministic failure mode when no API key is configured.
            # We keep this fast and side-effect free (no graph compilation/run).
            if not (settings.openai_api_key or "").strip():
                seq += 1
                error_payload = {
                    "type": "error",
                    "data": {
                        "message": "OPENAI_API_KEY is not configured",
                        "thread_id": thread_id,
                    },
                    "research_event": build_research_run_event(
                        "error",
                        {
                            "message": "OPENAI_API_KEY is not configured",
                            "thread_id": thread_id,
                        },
                        seq=seq,
                    ),
                }
                persisted = _persist_run_stream_event(
                    run_id=run_id,
                    thread_id=thread_id,
                    seq=seq,
                    event_type="error",
                    payload=error_payload,
                )
                if persisted:
                    seq = int(persisted.get("seq") or seq)
                    error_payload = dict(persisted.get("payload") or error_payload)
                yield format_sse_event(
                    event="error",
                    data=error_payload,
                    event_id=seq,
                )
                seq += 1
                done_payload = {
                    "type": "done",
                    "data": {"thread_id": thread_id},
                    "research_event": build_research_run_event(
                        "done",
                        {"thread_id": thread_id},
                        seq=seq,
                    ),
                }
                persisted = _persist_run_stream_event(
                    run_id=run_id,
                    thread_id=thread_id,
                    seq=seq,
                    event_type="done",
                    payload=done_payload,
                )
                if persisted:
                    seq = int(persisted.get("seq") or seq)
                    done_payload = dict(persisted.get("payload") or done_payload)
                yield format_sse_event(
                    event="done",
                    data=done_payload,
                    event_id=seq,
                )
                run_manager.finish(
                    thread_id,
                    status=RunStatus.failed,
                    error="OPENAI_API_KEY is not configured",
                )
                return

            source = iter_with_sse_keepalive(
                _stream_agent_events_call(
                    query,
                    thread_id=thread_id,
                    run_id=run_id,
                    model=model,
                    search_mode=mode_info,
                    images=_normalize_images_payload(payload.images),
                    user_id=user_id,
                    request=request,
                    deepsearch_config=safe_deepsearch_config,
                    research_brief=normalized_research_brief,
                ),
                interval_s=15.0,
            )

            async for maybe_line in iter_abort_on_disconnect(
                source,
                is_disconnected=request.is_disconnected,
                check_interval_s=0.25,
            ):
                # Keepalive comments are already SSE frames.
                if maybe_line.startswith(":"):
                    yield maybe_line
                    continue

                seq += 1
                payload_data = data_stream_line_to_payload(maybe_line, seq=seq)
                if payload_data:
                    event_type = str(payload_data.get("type") or "event")
                    persisted = _persist_run_stream_event(
                        run_id=run_id,
                        thread_id=thread_id,
                        seq=seq,
                        event_type=event_type,
                        payload=payload_data,
                    )
                    if persisted:
                        seq = int(persisted.get("seq") or seq)
                        payload_data = dict(persisted.get("payload") or payload_data)
                    yield format_sse_event(
                        event=event_type,
                        data=payload_data,
                        event_id=seq,
                    )
        finally:
            try:
                if gauge is not None:
                    gauge.dec()
            except Exception:
                pass

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Thread-ID": thread_id,
        },
    )


if __name__ == "__main__":
    import uvicorn

    # `settings` reads from `.env` (see `common/config.py`). This makes the
    # backend port configurable via `PORT=...` in `.env`, not only via shell env.
    port = int(getattr(settings, "port", 8001) or 8001)

    # NOTE: `uvicorn` hot reload (watchfiles) watches the whole CWD tree.
    # In this repo we have `web/node_modules`, which easily exceeds OS watcher limits
    # and crashes reload with "OS file watch limit reached".
    #
    # Keep reload opt-in to make `python main.py` reliable out-of-the-box.
    reload_enabled = bool(settings.debug) and bool(
        getattr(settings, "soulsearcher_reload", False)
    )
    if settings.debug and not reload_enabled:
        logger.info("Hot reload disabled (set SOULSEARCHER_RELOAD=true to enable).")

    reload_dirs = None
    reload_excludes = None
    if reload_enabled:
        try:
            from common.uvicorn_reload import (
                get_uvicorn_reload_dirs,
                get_uvicorn_reload_excludes,
            )

            reload_dirs = get_uvicorn_reload_dirs()
            reload_excludes = get_uvicorn_reload_excludes()
        except Exception:
            reload_dirs = None
            reload_excludes = None

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=reload_enabled,
        reload_dirs=reload_dirs,
        reload_excludes=reload_excludes,
        log_level="info",
    )
