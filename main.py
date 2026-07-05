"""Compatibility entry point for the SoulSearcher FastAPI application.

The implementation lives in :mod:`agent.api.app` so the repository root stays
as a stable ``main:app`` target for Docker, scripts, and existing deployments.
"""

from __future__ import annotations

from agent.api.app import (
    HTTPException,
    app,
    create_app,
    list_memory_records,
    memory_status,
    settings,
)

__all__ = [
    "HTTPException",
    "app",
    "create_app",
    "list_memory_records",
    "memory_status",
    "settings",
]


if __name__ == "__main__":
    import uvicorn

    from agent.api.app import get_uvicorn_run_kwargs, logger

    kwargs = get_uvicorn_run_kwargs()
    if settings.debug and not kwargs.get("reload"):
        logger.info("Hot reload disabled (set SOULSEARCHER_RELOAD=true to enable).")
    uvicorn.run("main:app", **kwargs)
