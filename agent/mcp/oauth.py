"""OAuth token support for MCP HTTP/SSE servers.

Supports client_credentials and refresh_token grant types with automatic
token refresh and Authorization header injection.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class _OAuthToken:
    access_token: str
    token_type: str
    expires_at: datetime


class OAuthTokenManager:
    """Acquire/cache/refresh OAuth tokens for MCP servers."""

    def __init__(self, oauth_configs: dict[str, dict[str, Any]]):
        self._configs = oauth_configs
        self._tokens: dict[str, _OAuthToken] = {}
        self._locks: dict[str, asyncio.Lock] = {
            name: asyncio.Lock() for name in oauth_configs
        }

    def has_servers(self) -> bool:
        return bool(self._configs)

    async def get_auth_header(self, server_name: str) -> str | None:
        cfg = self._configs.get(server_name)
        if not cfg:
            return None

        token = self._tokens.get(server_name)
        if token and not self._is_expiring(token, cfg):
            return f"{token.token_type} {token.access_token}"

        lock = self._locks[server_name]
        async with lock:
            token = self._tokens.get(server_name)
            if token and not self._is_expiring(token, cfg):
                return f"{token.token_type} {token.access_token}"

            fresh = await self._fetch_token(cfg)
            self._tokens[server_name] = fresh
            logger.info("Refreshed OAuth token for MCP server %s", server_name)
            return f"{fresh.token_type} {fresh.access_token}"

    @staticmethod
    def _is_expiring(token: _OAuthToken, cfg: dict[str, Any]) -> bool:
        skew = max(int(cfg.get("refresh_skew_seconds", 60)), 0)
        return token.expires_at <= datetime.now(timezone.utc) + timedelta(seconds=skew)

    async def _fetch_token(self, cfg: dict[str, Any]) -> _OAuthToken:
        data: dict[str, str] = {"grant_type": cfg["grant_type"]}
        for key, val in (cfg.get("extra_token_params") or {}).items():
            data[key] = val

        scope = cfg.get("scope")
        if scope:
            data["scope"] = scope
        audience = cfg.get("audience")
        if audience:
            data["audience"] = audience

        grant_type = cfg["grant_type"]
        if grant_type == "client_credentials":
            data["client_id"] = cfg["client_id"]
            data["client_secret"] = cfg["client_secret"]
        elif grant_type == "refresh_token":
            data["refresh_token"] = cfg["refresh_token"]
            if cfg.get("client_id"):
                data["client_id"] = cfg["client_id"]
            if cfg.get("client_secret"):
                data["client_secret"] = cfg["client_secret"]

        token_url = cfg["token_url"]
        token_field = cfg.get("token_field", "access_token")
        token_type_field = cfg.get("token_type_field", "token_type")
        default_token_type = cfg.get("default_token_type", "Bearer")
        expires_in_field = cfg.get("expires_in_field", "expires_in")

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(token_url, data=data)
            resp.raise_for_status()
            payload = resp.json()

        access_token = payload.get(token_field)
        if not access_token:
            raise ValueError(f"OAuth response missing '{token_field}'")

        token_type = str(payload.get(token_type_field, default_token_type))
        expires_in = int(payload.get(expires_in_field, 3600))
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(expires_in, 1))

        return _OAuthToken(access_token, token_type, expires_at)


def build_oauth_interceptor(extensions_config: dict[str, Any]) -> Any | None:
    """Build a tool interceptor that injects OAuth Authorization headers."""
    oauth_configs: dict[str, dict[str, Any]] = {}
    servers = extensions_config.get("mcpServers", {})
    for name, cfg in servers.items():
        oauth_cfg = cfg.get("oauth")
        if oauth_cfg and oauth_cfg.get("enabled"):
            oauth_configs[name] = oauth_cfg

    if not oauth_configs:
        return None

    manager = OAuthTokenManager(oauth_configs)

    async def oauth_interceptor(request: Any, handler: Any) -> Any:
        header = await manager.get_auth_header(request.server_name)
        if not header:
            return await handler(request)
        updated = dict(request.headers or {})
        updated["Authorization"] = header
        return await handler(request.override(headers=updated))

    return oauth_interceptor
