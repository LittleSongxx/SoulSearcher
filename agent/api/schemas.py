from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class SearchMode(BaseModel):
    useWebSearch: bool = False
    useAgent: bool = False
    useDeepSearch: bool = False


def coerce_search_mode_input(value: Any) -> SearchMode | None:
    if value is None:
        return None

    if isinstance(value, SearchMode):
        return value

    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"", "direct"}:
            return SearchMode()
        if lowered == "deep":
            return SearchMode(useWebSearch=True, useAgent=True, useDeepSearch=True)
        raise ValueError("search_mode must be direct/deep flags")

    if isinstance(value, dict):
        use_web = bool(value.get("useWebSearch", value.get("use_web", False)))
        use_deep = bool(value.get("useDeepSearch", value.get("use_deep", False)))
        use_agent = bool(use_deep)

        if not (use_web or use_agent or use_deep) and isinstance(
            value.get("mode"), str
        ):
            mode_lower = value["mode"].strip().lower()
            if mode_lower == "deep":
                use_agent = True
                use_deep = True
            elif mode_lower not in {"", "direct"}:
                raise ValueError("search_mode.mode must be direct or deep")

        if use_deep:
            use_web = True
            use_agent = True

        return SearchMode(
            useWebSearch=use_web,
            useAgent=use_agent,
            useDeepSearch=use_deep,
        )

    return None


class ImagePayload(BaseModel):
    name: Optional[str] = None
    data: str
    mime: Optional[str] = None


class ResearchRequest(BaseModel):
    query: str
    model: Optional[str] = None
    search_mode: Optional[SearchMode] = None
    user_id: Optional[str] = None
    skill_ids: Optional[list[str]] = None
    images: Optional[list[ImagePayload]] = None
    retrieval_policy: dict[str, Any] = Field(default_factory=dict)
    deepsearch_config: dict[str, Any] = Field(default_factory=dict)
    research_brief: dict[str, Any] = Field(default_factory=dict)

    @field_validator("search_mode", mode="before")
    @classmethod
    def _coerce_search_mode(cls, value: Any) -> SearchMode | None:
        return coerce_search_mode_input(value)
