"""Deferred tool discovery for large MCP tool sets.

Default-off compatibility layer: only tools tagged as MCP are deferred, and
only when config enables it. Non-MCP tools remain bound exactly as before.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Annotated, Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langchain_core.utils.function_calling import convert_to_openai_function
from langgraph.types import Command

MAX_RESULTS = 5


def is_mcp_tool(tool_obj: Any) -> bool:
    if bool(getattr(tool_obj, "is_mcp_tool", False)):
        return True
    metadata = getattr(tool_obj, "metadata", None)
    if isinstance(metadata, dict) and metadata.get("mcp"):
        return True
    return bool(getattr(tool_obj, "server_id", None) or getattr(tool_obj, "original_name", None))


def _tool_name(tool_obj: Any) -> str:
    name = getattr(tool_obj, "name", None)
    if isinstance(name, str) and name:
        return name
    if isinstance(tool_obj, type):
        return tool_obj.__name__
    return str(getattr(tool_obj, "__name__", "") or "")


def _tool_schema(tool_obj: Any) -> dict[str, Any]:
    try:
        return convert_to_openai_function(tool_obj)
    except Exception:
        return {
            "name": _tool_name(tool_obj),
            "description": str(getattr(tool_obj, "description", "") or ""),
            "parameters": {"type": "object", "properties": {}},
        }


@dataclass(frozen=True)
class DeferredToolCatalog:
    tools: tuple[Any, ...]

    @property
    def names(self) -> frozenset[str]:
        return frozenset(_tool_name(t) for t in self.tools if _tool_name(t))

    @property
    def hash(self) -> str:
        canon = [
            {"name": _tool_name(t), "schema": _tool_schema(t)}
            for t in sorted(self.tools, key=_tool_name)
        ]
        return hashlib.sha256(
            json.dumps(canon, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        ).hexdigest()[:16]

    def search(self, query: str) -> list[Any]:
        query = str(query or "").strip()
        if not query:
            return []
        if query.startswith("select:"):
            wanted = {part.strip() for part in query[7:].split(",") if part.strip()}
            return [t for t in self.tools if _tool_name(t) in wanted][:MAX_RESULTS]
        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error:
            pattern = re.compile(re.escape(query), re.IGNORECASE)
        scored: list[tuple[int, Any]] = []
        for t in self.tools:
            searchable = f"{_tool_name(t)} {getattr(t, 'description', '') or ''}"
            if pattern.search(searchable):
                scored.append((2 if pattern.search(_tool_name(t)) else 1, t))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [t for _, t in scored][:MAX_RESULTS]


@dataclass(frozen=True)
class DeferredToolSetup:
    final_tools: list[Any]
    deferred_tools: list[Any]
    deferred_names: frozenset[str]
    catalog_hash: str | None


def _promoted_names(config: dict[str, Any] | None, catalog_hash: str | None) -> set[str]:
    cfg = (config or {}).get("configurable") if isinstance(config, dict) else {}
    if not isinstance(cfg, dict):
        return set()
    promoted = cfg.get("promoted_tools") or {}
    if not isinstance(promoted, dict):
        return set()
    if catalog_hash and promoted.get("catalog_hash") != catalog_hash:
        return set()
    names = promoted.get("names") or []
    return {str(name) for name in names}


def build_tool_search_tool(catalog: DeferredToolCatalog):
    catalog_hash = catalog.hash

    @tool("tool_search")
    def tool_search(query: str, tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
        """Fetch full schemas for deferred MCP tools and make them callable."""
        promoted = promote_deferred_tools(catalog, query)
        return Command(
            update={
                "promoted_tools": promoted["promoted_tools"],
                "messages": [
                    ToolMessage(
                        content=promoted["content"],
                        tool_call_id=tool_call_id,
                        name="tool_search",
                    )
                ],
            }
        )

    try:
        metadata = dict(getattr(tool_search, "metadata", None) or {})
        metadata["deferred_catalog"] = catalog
        tool_search.metadata = metadata
    except Exception:
        pass
    return tool_search


def promote_deferred_tools(catalog: DeferredToolCatalog, query: str) -> dict[str, Any]:
    """Return schemas and promotion metadata for deferred tool-search results."""
    matched = catalog.search(query)
    names = [_tool_name(t) for t in matched]
    content = (
        json.dumps([_tool_schema(t) for t in matched], indent=2, ensure_ascii=False, default=str)
        if matched
        else f"No deferred tools found matching: {query}"
    )
    return {
        "content": content,
        "promoted_tools": {"catalog_hash": catalog.hash, "names": names},
    }


def assemble_deferred_tools(
    tools: list[Any],
    *,
    enabled: bool,
    config: dict[str, Any] | None = None,
) -> DeferredToolSetup:
    if not enabled:
        return DeferredToolSetup(list(tools), [], frozenset(), None)
    deferred = [t for t in tools if is_mcp_tool(t)]
    if not deferred:
        return DeferredToolSetup(list(tools), [], frozenset(), None)
    catalog = DeferredToolCatalog(tuple(deferred))
    promoted = _promoted_names(config, catalog.hash)
    final_tools = [
        t for t in tools
        if not is_mcp_tool(t) or _tool_name(t) in promoted
    ]
    final_tools.append(build_tool_search_tool(catalog))
    return DeferredToolSetup(final_tools, deferred, catalog.names, catalog.hash)


def deferred_tools_prompt_section(deferred_names: frozenset[str]) -> str:
    if not deferred_names:
        return ""
    names = "\n".join(sorted(deferred_names))
    return (
        "<available_deferred_mcp_tools>\n"
        "These MCP tools exist but are not callable until you fetch their schema with tool_search.\n"
        f"{names}\n"
        "</available_deferred_mcp_tools>"
    )
