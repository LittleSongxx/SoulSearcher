from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.runtime.options import sanitize_research_runtime_options

_DEFAULT_DEEP_SEARCH_MODE = {
    "mode": "deep",
    "useWebSearch": True,
    "useAgent": True,
    "useDeepSearch": True,
}


@dataclass(frozen=True, slots=True)
class A2AResearchInputs:
    deepsearch_config: dict[str, Any] = field(default_factory=dict)
    research_brief: dict[str, Any] = field(default_factory=dict)
    search_mode: dict[str, Any] = field(default_factory=dict)
    images: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""


def build_a2a_research_inputs(
    *,
    metadata: dict[str, Any],
    options: dict[str, Any],
    request_context: dict[str, Any],
    settings: Any,
) -> A2AResearchInputs:
    deepsearch_config = _dict_value(metadata.get("deepsearch_config"))
    deepsearch_config.update(_dict_value(options.get("deepsearch_config")))
    retrieval_policy = (
        _dict_value(metadata.get("retrieval_policy"))
        or _dict_value(options.get("retrieval_policy"))
        or _dict_value(request_context.get("retrieval_policy"))
    )
    if retrieval_policy:
        deepsearch_config["retrieval_policy"] = retrieval_policy

    skill_ids = _list_value(metadata.get("skill_ids") or options.get("skill_ids"))
    if skill_ids:
        deepsearch_config["skill_ids"] = skill_ids

    research_brief = (
        _dict_value(metadata.get("research_brief"))
        or _dict_value(options.get("research_brief"))
        or _dict_value(request_context.get("research_brief"))
    )
    search_mode = (
        _dict_value(metadata.get("search_mode"))
        or _dict_value(options.get("search_mode"))
        or dict(_DEFAULT_DEEP_SEARCH_MODE)
    )
    images = _list_of_dicts(options.get("images") or metadata.get("images"))
    if not images:
        images = _list_of_dicts(metadata.get("files") or options.get("files"))
    model = str(
        options.get("model")
        or metadata.get("model")
        or getattr(settings, "primary_model", "")
        or ""
    ).strip()
    return A2AResearchInputs(
        deepsearch_config=sanitize_research_runtime_options(deepsearch_config),
        research_brief=research_brief,
        search_mode=search_mode,
        images=images,
        model=model,
    )


def _dict_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _list_value(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]
