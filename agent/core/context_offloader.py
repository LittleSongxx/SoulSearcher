"""
Context Offloading Engine.

Offloads large tool results / scraped content to the filesystem,
keeping only compact references in AgentState to reduce context window size.

Inspired by Manus's "compact vs full representation" strategy:
- Full content → written to /tmp/weaver_offload/{thread_id}/{hash}.json
- AgentState   → stores only {url, title, snippet, offloaded_path}
- Writer/Compressor → reads full content on demand via load_offloaded_content()
"""

import hashlib
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from common.config import settings

logger = logging.getLogger(__name__)


def _offload_dir(thread_id: str = "") -> Path:
    """Return the offload directory for a given thread."""
    base = settings.context_offloading_dir or "/tmp/weaver_offload"
    if thread_id:
        return Path(base) / thread_id
    return Path(base)


def _content_hash(content: str) -> str:
    """Deterministic short hash for deduplication."""
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:12]


def offload_content(
    item: Dict[str, Any],
    thread_id: str = "",
) -> Dict[str, Any]:
    """
    Offload a single scraped_content item if it exceeds the threshold.

    Returns a compact version with ``offloaded_path`` when offloaded,
    or the original item unchanged when below threshold.
    """
    if not settings.context_offloading:
        return item

    # Already offloaded
    if item.get("offloaded_path"):
        return item

    content = item.get("content", "") or item.get("markdown", "") or ""
    threshold = int(settings.context_offloading_threshold)

    if len(content) < threshold:
        return item

    # Write full content to filesystem
    dest_dir = _offload_dir(thread_id)
    dest_dir.mkdir(parents=True, exist_ok=True)

    h = _content_hash(content)
    dest_path = dest_dir / f"{h}.json"

    try:
        dest_path.write_text(
            json.dumps(item, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"[context_offloader] Failed to write offload file: {e}")
        return item

    # Build compact reference
    snippet = content[:200].replace("\n", " ").strip()
    compact = {
        "url": item.get("url", ""),
        "title": item.get("title", ""),
        "snippet": snippet,
        "offloaded_path": str(dest_path),
        "offloaded": True,
    }
    # Preserve metadata keys that are small
    for key in ("query", "timestamp", "source", "relevance_score"):
        if key in item:
            compact[key] = item[key]

    logger.debug(f"[context_offloader] Offloaded {len(content)} chars → {dest_path}")
    return compact


def offload_content_list(
    items: List[Dict[str, Any]],
    thread_id: str = "",
) -> List[Dict[str, Any]]:
    """Offload a list of scraped_content items."""
    if not settings.context_offloading:
        return items
    return [offload_content(item, thread_id) for item in items]


def load_offloaded_content(path_or_item: Any) -> Dict[str, Any]:
    """
    Load the full content for an offloaded item.

    Accepts either a file path string or a compact dict with ``offloaded_path``.
    Returns the full item dict, or a minimal fallback on error.
    """
    if isinstance(path_or_item, dict):
        path = path_or_item.get("offloaded_path", "")
        if not path:
            return path_or_item  # not offloaded
    else:
        path = str(path_or_item)

    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data
    except Exception as e:
        logger.warning(f"[context_offloader] Failed to load offloaded content: {e}")
        # Return the compact item as-is so the caller can still use snippet
        if isinstance(path_or_item, dict):
            return path_or_item
        return {"error": str(e), "offloaded_path": path}


def load_all_offloaded(
    items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Load full content for all offloaded items in a list."""
    result = []
    for item in items:
        if item.get("offloaded"):
            result.append(load_offloaded_content(item))
        else:
            result.append(item)
    return result


def cleanup_offload_dir(thread_id: str = "") -> None:
    """Remove the offload directory for a thread (call on session end)."""
    dest_dir = _offload_dir(thread_id)
    if dest_dir.exists():
        try:
            shutil.rmtree(dest_dir)
            logger.info(f"[context_offloader] Cleaned up offload dir: {dest_dir}")
        except Exception as e:
            logger.warning(f"[context_offloader] Failed to cleanup: {e}")
