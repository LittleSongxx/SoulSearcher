from __future__ import annotations

import logging
import os
from collections.abc import Iterable

logger = logging.getLogger(__name__)


_PROXY_ENV_KEYS: tuple[str, ...] = (
    "ALL_PROXY",
    "all_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
)


def _dedupe_keep_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def normalize_socks_proxy_env() -> list[str]:
    """
    Normalize common proxy env vars so httpx/OpenAI clients don't crash.

    Many environments (Clash/V2Ray/etc.) export proxies as `socks://host:port`.
    httpx expects an explicit SOCKS version scheme like `socks5://` (or `socks4://`).

    This function:
    - Rewrites `socks://...` → `socks5://...` for standard proxy env keys.
    - Leaves other schemes untouched.

    Returns:
        List of environment variable names that were modified.
    """
    changed: list[str] = []

    for key in _PROXY_ENV_KEYS:
        raw = (os.environ.get(key) or "").strip()
        if not raw:
            continue

        lowered = raw.lower()
        if lowered.startswith("socks://"):
            os.environ[key] = "socks5://" + raw[len("socks://") :]
            changed.append(key)

    changed = _dedupe_keep_order(changed)
    if changed:
        logger.info(f"[proxy_env] Normalized proxy scheme for: {', '.join(changed)}")

    return changed

