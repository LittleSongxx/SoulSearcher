from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field


class AgentHealthResponse(BaseModel):
    tool_registry_total_tools: int
    search_strategy: str
    search_engines: list[str]
    search_providers_available: list[str]
    rate_limiter: dict[str, Any] = Field(default_factory=dict)
    background_leases: dict[str, Any] = Field(default_factory=dict)
    llm_reliability: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class HealthRouterDeps:
    settings: Any
    app_version: str
    app_started_at: float
    checkpointer_type: str
    content_type_latest: str
    generate_latest: Callable[[], bytes]
    get_memory_service: Callable[[], Any]
    get_search_orchestrator: Callable[[], Any]
    rate_limiter_status: Callable[[], dict[str, Any]]
    background_lease_status: Callable[[], dict[str, Any]]
    llm_reliability_status: Callable[[], dict[str, Any]]


def build_health_router(deps: HealthRouterDeps) -> APIRouter:
    router = APIRouter()

    @router.get("/")
    async def root():
        return {
            "status": "healthy",
            "service": "SoulSearcher Research Agent",
            "version": "0.1.0",
        }

    @router.get("/health")
    async def health():
        return {
            "status": "healthy",
            "database": "configured" if deps.settings.database_url else "not configured",
            "checkpointer_type": deps.checkpointer_type,
            "persistence_mode": "persistent" if deps.checkpointer_type != "memory" else "ephemeral",
            "version": deps.app_version,
            "uptime_seconds": time.monotonic() - deps.app_started_at,
            "timestamp": datetime.now().isoformat(),
        }

    @router.get("/ready")
    async def ready():
        checks: dict[str, Any] = {
            "database": {"configured": bool(deps.settings.database_url), "ok": True},
            "rate_limiter": deps.rate_limiter_status(),
            "background_leases": deps.background_lease_status(),
            "llm_reliability": deps.llm_reliability_status(),
            "memory": {"enabled": bool(getattr(deps.settings, "memory_enabled", True)), "ok": True},
            "search": {"ok": True, "providers_available": []},
        }

        if deps.settings.database_url:
            try:
                import psycopg

                with (
                    psycopg.connect(deps.settings.database_url, connect_timeout=2) as conn,
                    conn.cursor() as cur,
                ):
                    cur.execute("SELECT 1")
            except Exception as exc:
                checks["database"] = {
                    "configured": True,
                    "ok": False,
                    "error": _sanitize_error_message(str(exc)),
                }

        if getattr(deps.settings, "memory_enabled", True):
            try:
                status = deps.get_memory_service().status()
                checks["memory"].update(status)
                checks["memory"]["ok"] = bool(status.get("available", True))
            except Exception as exc:
                checks["memory"] = {
                    "enabled": True,
                    "ok": False,
                    "error": _sanitize_error_message(str(exc)),
                }

        try:
            orchestrator = deps.get_search_orchestrator()
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

    @router.get("/api/health/agent", response_model=AgentHealthResponse)
    async def agent_health():
        from tools.core.registry import get_global_registry

        registry = get_global_registry()
        orchestrator = deps.get_search_orchestrator()
        try:
            available = [p.name for p in orchestrator.get_available_providers()]
        except Exception:
            available = []

        return {
            "tool_registry_total_tools": len(registry.list_names()),
            "search_strategy": str(getattr(deps.settings, "search_strategy", "fallback") or "fallback"),
            "search_engines": list(getattr(deps.settings, "search_engines_list", [])),
            "search_providers_available": sorted([str(n) for n in available if str(n).strip()]),
            "rate_limiter": deps.rate_limiter_status(),
            "background_leases": deps.background_lease_status(),
            "llm_reliability": deps.llm_reliability_status(),
        }

    @router.get("/metrics")
    async def metrics():
        data = deps.generate_latest()
        return StreamingResponse(iter([data]), media_type=deps.content_type_latest)

    return router


def _sanitize_error_message(message: str) -> str:
    text = str(message or "").strip()
    if not text:
        return "unknown error"
    redactions = ("api_key", "apikey", "authorization", "bearer ", "token", "password", "secret")
    lowered = text.lower()
    if any(item in lowered for item in redactions):
        return "redacted error"
    return text[:500]
