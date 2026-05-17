"""DeerFlow-style deferred tool search — discover MCP tools at runtime.

Tools marked as 'deferred' are listed in <available-deferred-tools> but their
full schema is hidden until the agent calls tool_search to fetch them.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
from dataclasses import dataclass

from langchain_core.tools import BaseTool, tool
from langchain_core.utils.function_calling import convert_to_openai_function

logger = logging.getLogger(__name__)

MAX_RESULTS = 5


@dataclass
class DeferredToolEntry:
    name: str
    description: str
    tool: BaseTool


class DeferredToolRegistry:
    """Per-request registry of deferred (hidden) tools."""

    def __init__(self):
        self._entries: list[DeferredToolEntry] = []

    def register(self, tool: BaseTool) -> None:
        self._entries.append(DeferredToolEntry(
            name=tool.name,
            description=tool.description or "",
            tool=tool,
        ))

    def promote(self, names: set[str]) -> None:
        before = len(self._entries)
        self._entries = [e for e in self._entries if e.name not in names]
        if before != len(self._entries):
            logger.debug("Promoted %d tools from deferred: %s", before - len(self._entries), names)

    def search(self, query: str) -> list[BaseTool]:
        if query.startswith("select:"):
            names = {n.strip() for n in query[7:].split(",")}
            return [e.tool for e in self._entries if e.name in names][:MAX_RESULTS]

        if query.startswith("+"):
            parts = query[1:].split(None, 1)
            required = parts[0].lower()
            candidates = [e for e in self._entries if required in e.name.lower()]
            if len(parts) > 1:
                candidates.sort(key=lambda e: _regex_score(parts[1], e), reverse=True)
            return [e.tool for e in candidates][:MAX_RESULTS]

        try:
            regex = re.compile(query, re.IGNORECASE)
        except re.error:
            regex = re.compile(re.escape(query), re.IGNORECASE)

        scored = []
        for entry in self._entries:
            searchable = f"{entry.name} {entry.description}"
            if regex.search(searchable):
                score = 2 if regex.search(entry.name) else 1
                scored.append((score, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry.tool for _, entry in scored][:MAX_RESULTS]

    @property
    def entries(self) -> list[DeferredToolEntry]:
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)


def _regex_score(pattern: str, entry: DeferredToolEntry) -> int:
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error:
        regex = re.compile(re.escape(pattern), re.IGNORECASE)
    return len(regex.findall(f"{entry.name} {entry.description}"))


# Per-request registry via ContextVar
_registry_var: contextvars.ContextVar[DeferredToolRegistry | None] = contextvars.ContextVar(
    "deferred_tool_registry", default=None
)


def get_deferred_registry() -> DeferredToolRegistry | None:
    return _registry_var.get()


def set_deferred_registry(registry: DeferredToolRegistry) -> None:
    _registry_var.set(registry)


def reset_deferred_registry() -> None:
    _registry_var.set(None)


@tool
def tool_search(query: str) -> str:
    """Fetches full schema definitions for deferred tools so they can be called.

    Deferred tools appear by name in <available-deferred-tools> but cannot be
    called until their schema is fetched via this tool.

    Query forms:
      - "select:ToolA,ToolB" — exact name match
      - "notebook jupyter" — keyword search
      - "+slack send" — require "slack" in name, rank by remaining terms

    Args:
        query: Search query for deferred tools.
    """
    registry = get_deferred_registry()
    if not registry:
        return "No deferred tools available."

    matched = registry.search(query)
    if not matched:
        return f"No tools found matching: {query}"

    tool_defs = [convert_to_openai_function(t) for t in matched[:MAX_RESULTS]]
    registry.promote({t.name for t in matched[:MAX_RESULTS]})

    return json.dumps(tool_defs, indent=2, ensure_ascii=False)
