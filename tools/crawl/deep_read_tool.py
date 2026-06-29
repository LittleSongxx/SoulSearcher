"""Read selected sections from SoulSearcher source-cache files."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from common.config import settings


def _resolve_allowed_path(cached_path: str, config: dict[str, Any] | None = None) -> Path:
    from agent.workflows.source_cache import source_cache_dir

    candidate = Path(str(cached_path or "")).expanduser().resolve()
    cfg = (config or {}).get("configurable") if isinstance(config, dict) else {}
    has_explicit_context = isinstance(cfg, dict) and bool(cfg.get("workspace_path") or cfg.get("thread_id"))
    if not has_explicit_context:
        if "source-cache" not in candidate.parts:
            raise PermissionError("deep_read can only read files from a source-cache directory")
        if not candidate.is_file():
            raise FileNotFoundError(str(candidate))
        return candidate

    cache_dir = source_cache_dir(config).resolve()
    try:
        candidate.relative_to(cache_dir)
    except ValueError as exc:
        raise PermissionError("deep_read can only read files from the thread source-cache directory") from exc
    if not candidate.is_file():
        raise FileNotFoundError(str(candidate))
    return candidate


def _extract_section(content: str, query: str, max_chars: int) -> str:
    query = str(query or "").strip().lower()
    if not query:
        return content[:max_chars]

    lines = content.splitlines()
    heading = re.compile(r"^#{1,6}\s+(.+)$")
    for index, line in enumerate(lines):
        match = heading.match(line)
        if not match or query not in match.group(1).lower():
            continue
        level = len(line) - len(line.lstrip("#"))
        section = [line]
        for next_line in lines[index + 1:]:
            next_match = heading.match(next_line)
            if next_match and len(next_line) - len(next_line.lstrip("#")) <= level:
                break
            section.append(next_line)
        return "\n".join(section)[:max_chars]

    tokens = [part for part in re.split(r"\W+", query) if len(part) >= 2]
    paragraphs = [part.strip() for part in content.split("\n\n") if part.strip()]
    matches = [
        paragraph
        for paragraph in paragraphs
        if any(token in paragraph.lower() for token in tokens)
    ]
    return ("\n\n".join(matches) if matches else content)[:max_chars]


def deep_read_cached_source(
    cached_path: str,
    *,
    section_query: str = "",
    start_line: int | None = None,
    end_line: int | None = None,
    config: dict[str, Any] | None = None,
    max_chars: int | None = None,
) -> str:
    path = _resolve_allowed_path(cached_path, config)
    content = path.read_text(encoding="utf-8")
    max_chars = int(max_chars or getattr(settings, "deep_read_max_chars", 30000))

    if start_line is not None or end_line is not None:
        lines = content.splitlines()
        start = max(0, int(start_line or 1) - 1)
        end = min(len(lines), int(end_line or len(lines)))
        selected = "\n".join(lines[start:end])
        return f"## Lines {start + 1}-{end} of {len(lines)} from {os.fspath(path)}\n\n{selected[:max_chars]}"

    selected = _extract_section(content, section_query, max_chars)
    heading = (
        f"## Extracted section matching '{section_query}'"
        if section_query
        else "## Cached source preview"
    )
    return f"{heading}\n\n{selected}"


@tool("deep_read")
def deep_read(
    cached_path: str,
    section_query: str = "",
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    """Read a section from a file previously written to SoulSearcher's source-cache.

    Use this when a tool result says full content was cached and gives a
    cached_path. You can ask for a section query or a line range.
    """
    return deep_read_cached_source(
        cached_path,
        section_query=section_query,
        start_line=start_line,
        end_line=end_line,
    )
