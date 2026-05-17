from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from langchain_core.tools import BaseTool

from common.config import settings
from tools.core.registry import set_registered_tools
from tools.mcp import close_mcp_tools, init_mcp_tools

logger = logging.getLogger(__name__)

_cached_tools: list[BaseTool] = []
_initialized = False
_mtime: float | None = None
_servers_fingerprint = ""
_lock = asyncio.Lock()


def _config_path() -> Path:
    path = Path(settings.mcp_config_path)
    return path if path.is_absolute() else Path.cwd() / path


def _current_mtime() -> float | None:
    path = _config_path()
    if path.exists():
        return os.path.getmtime(path)
    return None


def _fingerprint(servers: Any) -> str:
    try:
        return json.dumps(servers, sort_keys=True, ensure_ascii=True)
    except Exception:
        return str(servers)


def _is_stale(servers: Any) -> bool:
    if not _initialized:
        return True
    current_mtime = _current_mtime()
    if _mtime is not None and current_mtime is not None and current_mtime > _mtime:
        return True
    return _fingerprint(servers) != _servers_fingerprint


async def initialize_cached_mcp_tools(
    *,
    servers_override: Optional[dict[str, Any]] = None,
    enabled: Optional[bool] = None,
    force: bool = False,
) -> list[BaseTool]:
    """Initialize MCP once and refresh when config changes."""
    global _cached_tools, _initialized, _mtime, _servers_fingerprint
    servers = servers_override if servers_override is not None else settings.mcp_servers
    async with _lock:
        if not force and not _is_stale(servers):
            return list(_cached_tools)
        if _initialized:
            await close_mcp_tools()
        _cached_tools = await init_mcp_tools(servers_override=servers_override, enabled=enabled)
        set_registered_tools(_cached_tools)
        _initialized = True
        _mtime = _current_mtime()
        _servers_fingerprint = _fingerprint(servers)
        logger.info("MCP runtime cache loaded %d tool(s)", len(_cached_tools))
        return list(_cached_tools)


async def reset_mcp_runtime_cache() -> None:
    global _cached_tools, _initialized, _mtime, _servers_fingerprint
    async with _lock:
        await close_mcp_tools()
        _cached_tools = []
        _initialized = False
        _mtime = None
        _servers_fingerprint = ""
