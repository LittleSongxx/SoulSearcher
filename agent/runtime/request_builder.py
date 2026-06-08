from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.core.state import build_initial_state
from agent.runtime.workspace import get_research_workspace


@dataclass
class ResearchRuntimeRequest:
    input_text: str
    thread_id: str
    model: str
    mode_info: dict[str, Any]
    user_id: str | None
    images: list[dict[str, Any]]
    research_brief: dict[str, Any] | None
    context_messages: list[Any]
    deepsearch_config: dict[str, Any]
    base_configurable: dict[str, Any]
    recursion_limit: int = 50


@dataclass
class ResearchRuntimeBundle:
    initial_state: dict[str, Any]
    config: dict[str, Any]
    deepsearch_config: dict[str, Any]
    workspace: dict[str, Any]


def build_research_runtime(request: ResearchRuntimeRequest) -> ResearchRuntimeBundle:
    """Build the single runtime state/config bundle for a research request."""
    safe_deepsearch_config = dict(request.deepsearch_config or {})
    active_skill_ids = (
        safe_deepsearch_config.get("skill_ids")
        or safe_deepsearch_config.get("deepsearch_skill_ids")
        or []
    )

    workspace = get_research_workspace(request.thread_id)
    workspace.write_json(
        "request.json",
        {
            "thread_id": request.thread_id,
            "query": request.input_text,
            "model": request.model,
            "search_mode": request.mode_info,
            "user_id": request.user_id,
            "deepsearch_config": safe_deepsearch_config,
        },
    )

    initial_state = build_initial_state(
        input_text=request.input_text,
        user_id=request.user_id or "",
        images=request.images,
        research_brief=(
            request.research_brief
            if isinstance(request.research_brief, dict)
            else None
        ),
        skill_ids=active_skill_ids,
        source_routing=(
            safe_deepsearch_config.get("source_routing")
            if isinstance(safe_deepsearch_config.get("source_routing"), dict)
            else {}
        ),
        messages=request.context_messages,
    )
    if (
        initial_state.get("source_routing")
        and not isinstance(safe_deepsearch_config.get("source_routing"), dict)
    ):
        safe_deepsearch_config["source_routing"] = initial_state["source_routing"]

    workspace_artifact = workspace.artifact()
    initial_state["deepsearch_artifacts"]["workspace"] = workspace_artifact
    safe_deepsearch_config["workspace_path"] = str(workspace.root)

    config = {
        "configurable": dict(request.base_configurable),
        "recursion_limit": request.recursion_limit,
    }
    config["configurable"].update(safe_deepsearch_config)

    return ResearchRuntimeBundle(
        initial_state=initial_state,
        config=config,
        deepsearch_config=safe_deepsearch_config,
        workspace=workspace_artifact,
    )
