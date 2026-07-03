import asyncio
import hashlib
import hmac
import json
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import (
    FastAPI,
    HTTPException,
    Request,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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


from pydantic import BaseModel
from starlette.responses import JSONResponse as StarletteJSONResponse

from agent import (
    ToolEvent,
    create_checkpointer,
    create_research_graph,
    get_emitter,
    remove_emitter,
)

# Router modules extracted from main.py for maintainability
from agent.api.a2a import mount_a2a_routes
from agent.api.admin import AdminRouterDeps, build_admin_router
from agent.api.channels import build_channels_router
from agent.api.collaboration import (
    CollaborationRouterDeps,
    build_collaboration_router,
)
from agent.api.export import ExportRouterDeps, build_export_router
from agent.api.health import HealthRouterDeps, build_health_router
from agent.api.interrupt import InterruptRouterDeps, build_interrupt_router
from agent.api.library import LibraryRouterDeps, build_library_router
from agent.api.memory import (
    MemoryRouterDeps,
    configure_memory_router,
)
from agent.api.memory import (
    list_memory_records as list_memory_records,
)
from agent.api.memory import (
    memory_status as memory_status,
)
from agent.api.research import ResearchRouterDeps, build_research_router
from agent.api.research_control import (
    ResearchControlRouterDeps,
    build_research_control_router,
)
from agent.api.runs import RunsRouterDeps, build_runs_router
from agent.api.schemas import coerce_search_mode_input as _coerce_search_mode_input
from agent.api.sessions import SessionsRouterDeps, build_sessions_router
from agent.api.skills import build_skills_router
from agent.api.tracing import router as tracing_router
from agent.core.capabilities import register_capability_metadata
from agent.core.llm_reliability import llm_reliability_manager
from agent.memory import MemoryUnavailableError, get_memory_service
from agent.retrieval.policy import build_retrieval_policy
from agent.runtime.background_runs import background_run_manager
from agent.runtime.context import SoulSearcherRuntimeContext
from agent.runtime.idempotency import (
    IdempotencyConflictError,
    canonical_request_hash,
    idempotency_store,
)
from agent.runtime.middleware.shared import get_token_summary
from agent.runtime.request_builder import (
    ResearchRuntimeRequest,
    build_research_runtime,
)
from agent.runtime.research_service import (
    ResearchExecutionService,
    format_stream_event,
)
from agent.runtime.research_service import (
    normalize_interrupt_resume_payload as _normalize_interrupt_resume_payload,
)
from agent.runtime.research_service import (
    normalize_search_mode as _normalize_search_mode,
)
from agent.runtime.research_service import (
    safe_research_deepsearch_config as _safe_research_deepsearch_config,
)
from agent.runtime.research_service import (
    serialize_interrupts as _serialize_interrupts,
)
from agent.runtime.runs import RunStatus, run_manager
from agent.workflows.evidence_extractor import extract_message_sources
from agent.workflows.research_brief import build_research_brief
from common.cancellation import TaskStatus, cancellation_manager
from common.config import settings
from common.logger import get_logger, setup_logging
from common.metrics import metrics_registry
from common.proxy_env import normalize_socks_proxy_env
from common.rate_limiter import build_rate_limiter
from common.stream_registry import StreamRegistry
from common.thread_ownership import get_thread_owner
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


def _build_app_shell() -> FastAPI:
    """Create the SoulSearcher FastAPI app shell used by decorators below."""
    application = FastAPI(
        title="SoulSearcher Research Agent API",
        description="Deep research AI agent with code execution capabilities",
        version="0.1.0",
        lifespan=lifespan,
    )
    register_capability_metadata(application, settings)
    return application


# Stable ASGI app instance used by ``main:app`` and tests.
app = _build_app_shell()

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

    Persisted run records are the authorization source of truth. The in-memory
    registry is only a compatibility fallback for short-lived streams that have
    not yet created a run record.
    """
    internal_key = (getattr(settings, "internal_api_key", "") or "").strip()
    if not internal_key:
        return

    principal_id = (getattr(request.state, "principal_id", "") or "").strip()
    if not principal_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    record = run_manager.get(thread_id)
    persisted_owner = ""
    if record is not None:
        persisted_owner = (record.user_id or "").strip()
        if not persisted_owner and isinstance(record.metadata, dict):
            persisted_owner = str(record.metadata.get("user_id") or "").strip()
    if persisted_owner:
        if persisted_owner != principal_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        return

    fallback_owner = (get_thread_owner(thread_id) or "").strip()
    if fallback_owner and fallback_owner != principal_id:
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
    digest = hashlib.sha1(f"{prefix}:{user_id}:{key}".encode()).hexdigest()[:24]
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


def attach_runtime_context(application: FastAPI) -> SoulSearcherRuntimeContext:
    """Attach app-scoped runtime objects after they have been initialized."""

    context = SoulSearcherRuntimeContext(
        settings=settings,
        research_graph=research_graph,
        checkpointer=checkpointer,
        checkpointer_type=_checkpointer_type,
        background_run_manager=background_run_manager,
        run_manager=run_manager,
        mcp_enabled=mcp_enabled,
        mcp_servers_config=mcp_servers_config,
    )
    application.state.runtime_context = context
    return context


attach_runtime_context(app)


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


# Active streaming tasks managed via StreamRegistry (see common/stream_registry.py)
active_streams = _stream_registry._tasks


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


app.include_router(
    build_health_router(
        HealthRouterDeps(
            settings=settings,
            app_version=app.version,
            app_started_at=APP_STARTED_AT,
            checkpointer_type=_checkpointer_type,
            content_type_latest=CONTENT_TYPE_LATEST,
            generate_latest=generate_latest,
            get_memory_service=get_memory_service,
            get_search_orchestrator=get_search_orchestrator,
            rate_limiter_status=_rate_limiter_status,
            background_lease_status=_background_lease_status,
            llm_reliability_status=_llm_reliability_status,
        )
    )
)
app.include_router(
    configure_memory_router(
        MemoryRouterDeps(settings=settings, get_memory_service=get_memory_service)
    )
)
app.include_router(
    build_admin_router(
        AdminRouterDeps(
            settings=settings,
            app_version=app.version,
            mcp_enabled=bool(mcp_enabled),
            checkpointer_type=_checkpointer_type,
            llm_reliability_manager=llm_reliability_manager,
            get_search_orchestrator=get_search_orchestrator,
        )
    )
)
app.include_router(build_library_router(LibraryRouterDeps(settings=settings, logger=logger)))
if getattr(settings, "channels_enabled", False):
    app.include_router(build_channels_router())
app.include_router(build_skills_router())
app.include_router(
    build_export_router(
        ExportRouterDeps(
            checkpointer=checkpointer,
            logger=logger,
            require_thread_owner=_require_thread_owner,
        )
    )
)
app.include_router(
    build_collaboration_router(
        CollaborationRouterDeps(
            checkpointer=checkpointer,
            logger=logger,
            require_thread_owner=_require_thread_owner,
        )
    )
)
app.include_router(
    build_sessions_router(
        SessionsRouterDeps(
            checkpointer=checkpointer,
            settings=settings,
            logger=logger,
            require_thread_owner=_require_thread_owner,
            get_emitter=get_emitter,
            tool_event=ToolEvent,
        )
    )
)
app.include_router(
    build_research_control_router(
        ResearchControlRouterDeps(
            settings=settings,
            checkpointer=checkpointer,
            logger=logger,
            cancellation_manager=cancellation_manager,
            active_streams=active_streams,
            task_status=TaskStatus,
            require_thread_owner=_require_thread_owner,
        )
    )
)


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


async def stream_agent_resume_events(
    resume_payload: Any,
    *,
    thread_id: str,
    run_id: str | None = None,
    model: str | None = None,
    search_mode: dict[str, Any] | None = None,
    user_id: str | None = None,
):
    """Stream LangGraph resume results in SoulSearcher's internal event envelope."""
    del run_id, user_id
    if not checkpointer:
        yield await format_stream_event(
            "error",
            {
                "code": "SOULSEARCHER_RESUME_CHECKPOINTER_MISSING",
                "message": "Interrupt resume requires a checkpointer.",
                "stage": "resume",
                "retryable": False,
            },
        )
        return
    if not thread_id:
        yield await format_stream_event(
            "error",
            {
                "code": "SOULSEARCHER_RESUME_THREAD_MISSING",
                "message": "thread_id is required for interrupt resume.",
                "stage": "resume",
                "retryable": False,
            },
        )
        return
    existing = checkpointer.get_tuple({"configurable": {"thread_id": thread_id}})
    if not existing:
        yield await format_stream_event(
            "error",
            {
                "code": "SOULSEARCHER_RESUME_CHECKPOINT_NOT_FOUND",
                "message": "No checkpoint found for this A2A task.",
                "stage": "resume",
                "retryable": False,
            },
        )
        return
    mode_info = _normalize_search_mode(search_mode)
    selected_model = (model or settings.primary_model).strip()
    config = {
        "configurable": {
            "thread_id": thread_id,
            "model": selected_model,
            "search_mode": mode_info,
            "allow_interrupts": True,
            "tool_approval": settings.tool_approval or False,
            "human_review": settings.human_review or False,
            "max_revisions": settings.max_revisions,
        },
        "recursion_limit": 50,
    }
    try:
        normalized = _normalize_interrupt_resume_payload(resume_payload)
        result = await research_graph.ainvoke(Command(resume=normalized), config=config)
        interrupts = _serialize_interrupts(result.get("__interrupt__"))
        if interrupts:
            yield await format_stream_event(
                "interrupt",
                {"thread_id": thread_id, "prompts": interrupts},
            )
            return
        final_report = str(result.get("final_report") or "")
        report_format = str(result.get("report_format") or "markdown")
        if final_report:
            yield await format_stream_event(
                "completion",
                {"content": final_report, "format": report_format},
            )
            yield await format_stream_event(
                "artifact",
                {
                    "id": "final-report",
                    "type": "html-report" if report_format == "html" else "report",
                    "title": "Research Report",
                    "content": final_report,
                    "format": report_format,
                },
            )
        yield await format_stream_event(
            "done",
            {"timestamp": datetime.now().isoformat(), "thread_id": thread_id},
        )
    except ValueError as exc:
        yield await format_stream_event(
            "error",
            {
                "code": "SOULSEARCHER_RESUME_PAYLOAD_INVALID",
                "message": str(exc),
                "stage": "resume",
                "retryable": False,
            },
        )
    except Exception as exc:
        logger.error("A2A resume failed for %s: %s", thread_id, exc, exc_info=True)
        yield await format_stream_event(
            "error",
            {
                "code": "SOULSEARCHER_RESUME_FAILED",
                "message": str(exc),
                "stage": "resume",
                "retryable": True,
            },
        )


research_execution_service = ResearchExecutionService(
    stream_factory=stream_agent_events,
    resume_factory=stream_agent_resume_events,
)
app.state.runtime_context.research_execution_service = research_execution_service


app.include_router(
    build_runs_router(
        RunsRouterDeps(
            settings=settings,
            checkpointer=checkpointer,
            metrics_registry=metrics_registry,
            run_manager=run_manager,
            background_run_manager=background_run_manager,
            stream_agent_events=research_execution_service.stream,
            normalize_search_mode=_normalize_search_mode,
            safe_deepsearch_config=_safe_research_deepsearch_config,
            require_thread_owner=_require_thread_owner,
        )
    )
)
app.include_router(
    build_interrupt_router(
        InterruptRouterDeps(
            settings=settings,
            checkpointer=checkpointer,
            research_graph=research_graph,
            logger=logger,
            normalize_search_mode=_normalize_search_mode,
            coerce_search_mode_input=_coerce_search_mode_input,
            normalize_interrupt_resume_payload=_normalize_interrupt_resume_payload,
            serialize_interrupts=_serialize_interrupts,
            require_thread_owner=_require_thread_owner,
        )
    )
)

app.include_router(
    build_research_router(
        ResearchRouterDeps(
            settings=settings,
            logger=logger,
            run_manager=run_manager,
            sse_active_connections=sse_active_connections,
            normalize_search_mode=_normalize_search_mode,
            safe_deepsearch_config=_safe_research_deepsearch_config,
            persist_run_stream_event=_persist_run_stream_event,
            stream_agent_events_call=research_execution_service.stream,
            build_research_brief=build_research_brief,
        )
    )
)


mount_a2a_routes(
    app,
    settings=settings,
    stream_factory=research_execution_service.stream,
    resume_factory=research_execution_service.resume,
)


def create_app() -> FastAPI:
    """Return the fully wired SoulSearcher FastAPI application.

    The current runtime still registers routes through module-level decorators,
    so the app factory intentionally returns the stable singleton after all
    routers and A2A endpoints have been mounted.
    """

    return app


def get_uvicorn_run_kwargs() -> dict[str, Any]:
    """Return uvicorn keyword arguments for the compatibility entry point."""
    port = int(getattr(settings, "port", 8001) or 8001)
    reload_enabled = bool(settings.debug) and bool(
        getattr(settings, "soulsearcher_reload", False)
    )
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
    return {
        "host": "0.0.0.0",
        "port": port,
        "reload": reload_enabled,
        "reload_dirs": reload_dirs,
        "reload_excludes": reload_excludes,
        "log_level": "info",
    }
