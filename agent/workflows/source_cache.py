"""Thread-local cache for long source/tool output text."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from common.config import settings


def _safe_name(value: str, fallback: str = "source") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-")
    return (cleaned or fallback)[:80]


def source_cache_dir(config: dict[str, Any] | None = None) -> Path:
    cfg = (config or {}).get("configurable") if isinstance(config, dict) else {}
    cfg = cfg if isinstance(cfg, dict) else {}
    workspace_path = str(cfg.get("workspace_path") or "").strip()
    if workspace_path:
        root = Path(workspace_path)
    else:
        from agent.runtime.workspace import get_research_workspace

        thread_id = str(cfg.get("thread_id") or "default")
        root = get_research_workspace(thread_id).root
    cache_dir = root / "source-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def cache_source_text(
    *,
    text: str,
    config: dict[str, Any] | None = None,
    source_hint: str = "source",
    metadata: dict[str, Any] | None = None,
    min_chars: int | None = None,
) -> dict[str, Any] | None:
    content = str(text or "")
    threshold = int(
        min_chars
        if min_chars is not None
        else getattr(settings, "source_cache_max_chars", 120000)
    )
    if len(content) <= max(0, threshold):
        return None

    digest = hashlib.sha1(content.encode("utf-8")).hexdigest()
    filename = f"{_safe_name(source_hint)}-{digest[:12]}.txt"
    path = source_cache_dir(config) / filename
    if not path.exists():
        header = ""
        if metadata:
            header = "\n".join(f"{key}: {value}" for key, value in sorted(metadata.items()))
            header = f"---\n{header}\n---\n\n"
        path.write_text(header + content, encoding="utf-8")

    return {
        "cached_path": str(path),
        "content_hash": digest[:16],
        "line_count": content.count("\n") + 1,
        "char_count": len(content),
    }
