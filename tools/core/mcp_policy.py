from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from common.config import settings


@dataclass(frozen=True)
class MCPToolPolicy:
    strategy: str = "on_demand"
    tool_whitelist: list[str] = field(default_factory=list)
    max_tools: int = 0

    @property
    def disabled(self) -> bool:
        return self.strategy == "disabled"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_mcp_tool_policy(config: dict[str, Any] | None = None) -> MCPToolPolicy:
    cfg = _configurable(config or {})
    strategy = normalize_mcp_strategy(
        cfg.get("mcp_strategy")
        or cfg.get("deepsearch_mcp_strategy")
        or getattr(settings, "mcp_strategy", "on_demand")
    )
    whitelist = _string_list(
        cfg.get("mcp_tool_whitelist")
        or cfg.get("mcp_tools_to_include")
        or getattr(settings, "mcp_tool_whitelist", "")
    )
    max_tools = _int_value(cfg.get("mcp_max_tools"), int(getattr(settings, "mcp_max_tools", 0) or 0))
    return MCPToolPolicy(strategy=strategy, tool_whitelist=whitelist, max_tools=max(0, max_tools))


def filter_mcp_tools(tools: list[Any], policy: MCPToolPolicy) -> list[Any]:
    if policy.disabled:
        return []
    allowed = set(policy.tool_whitelist or [])
    output: list[Any] = []
    seen = set()
    for tool in tools or []:
        name = str(getattr(tool, "name", "") or "").strip()
        if not name:
            continue
        original = str(getattr(tool, "original_name", "") or "").strip()
        if allowed and name not in allowed and original not in allowed:
            continue
        if name in seen:
            continue
        seen.add(name)
        output.append(tool)
        if policy.max_tools > 0 and len(output) >= policy.max_tools:
            break
    return output


def normalize_mcp_strategy(value: Any) -> str:
    strategy = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "off": "disabled",
        "none": "disabled",
        "false": "disabled",
        "fast": "fast_once",
        "deep": "per_research_unit",
        "per_unit": "per_research_unit",
    }
    strategy = aliases.get(strategy, strategy)
    if strategy in {"disabled", "fast_once", "per_research_unit", "on_demand"}:
        return strategy
    return "on_demand"


def _configurable(config: dict[str, Any]) -> dict[str, Any]:
    cfg = config.get("configurable") if isinstance(config, dict) else {}
    return cfg if isinstance(cfg, dict) else {}


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def _int_value(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)
