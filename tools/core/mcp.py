import json
import logging
from typing import Any, Optional

from langchain.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from common.config import settings
from tools.core.mcp_policy import build_mcp_tool_policy, filter_mcp_tools

logger = logging.getLogger(__name__)

_CLIENT: Any = None
_TOOLS: list[BaseTool] = []
_CONFIG: dict[str, Any] = {}


def _parse_servers(servers: Any) -> dict[str, Any]:
    if isinstance(servers, str):
        try:
            return json.loads(servers)
        except json.JSONDecodeError:
            logger.error("MCP_SERVERS is not valid JSON; MCP tools disabled")
            return {}
    return servers or {}


async def init_mcp_tools(
    servers_override: Optional[dict[str, Any]] = None,
    enabled: Optional[bool] = None,
    policy_config: Optional[dict[str, Any]] = None,
) -> list[BaseTool]:
    """
    Initialize MCP tools. Optionally override servers config.
    """
    global _CLIENT, _TOOLS, _CONFIG

    servers_cfg = (
        servers_override if servers_override is not None else settings.mcp_servers
    )
    servers: dict[str, Any] = _parse_servers(servers_cfg)

    policy = build_mcp_tool_policy(policy_config or {})
    use_mcp = enabled if enabled is not None else settings.enable_mcp

    if policy.disabled or not use_mcp or not servers:
        logger.info("MCP disabled or no servers configured.")
        _TOOLS = []
        return []

    _CONFIG = servers
    _CLIENT = MultiServerMCPClient(servers)
    _TOOLS = filter_mcp_tools(await _CLIENT.get_tools(), policy)
    logger.info(f"Loaded {len(_TOOLS)} MCP tools from {len(servers)} servers")
    try:
        names = [t.name for t in _TOOLS if hasattr(t, "name")]
        logger.debug(f"MCP tools: {names[:10]}")
    except Exception:
        pass
    return _TOOLS


async def reload_mcp_tools(
    servers_config: dict[str, Any], enabled: Optional[bool] = None
) -> list[BaseTool]:
    """Close existing and reload with new config."""
    await close_mcp_tools()
    return await init_mcp_tools(servers_override=servers_config, enabled=enabled)


async def close_mcp_tools() -> None:
    global _CLIENT
    if _CLIENT is None:
        return
    if hasattr(_CLIENT, "close"):
        await _CLIENT.close()
    _CLIENT = None
