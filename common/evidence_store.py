from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class EvidenceStoreSnapshot:
    thread_id: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    passages: list[dict[str, Any]] = field(default_factory=list)
    claims: list[dict[str, Any]] = field(default_factory=list)
    evidence_items: list[dict[str, Any]] = field(default_factory=list)
    citation_annotations: list[dict[str, Any]] = field(default_factory=list)
    quality_results: list[dict[str, Any]] = field(default_factory=list)
    research_todos: list[dict[str, Any]] = field(default_factory=list)
    todo_summary: dict[str, Any] = field(default_factory=dict)
    source_routing: dict[str, Any] = field(default_factory=dict)
    access_policy: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_response_patch(self) -> dict[str, Any]:
        return {
            "sources": self.sources,
            "claims": self.claims,
            "quality_details": _dict_from(self.metadata.get("quality_details")),
            "quality_gates": self.quality_results,
            "research_todos": self.research_todos,
            "todo_summary": self.todo_summary,
            "evidence_items": self.evidence_items,
            "citation_annotations": self.citation_annotations,
            "fetched_pages": _list_from(self.metadata.get("fetched_pages")),
            "passages": self.passages,
            "evidence_store": self.to_dict(),
            "source_routing": self.source_routing,
            "access_policy": self.access_policy,
        }


def build_evidence_store_snapshot(
    *,
    thread_id: str,
    artifacts: dict[str, Any],
    state: dict[str, Any] | None = None,
) -> EvidenceStoreSnapshot:
    artifacts = artifacts if isinstance(artifacts, dict) else {}
    state = state if isinstance(state, dict) else {}
    owner = str(state.get("user_id") or artifacts.get("user_id") or "").strip()
    group_id = str(state.get("group_id") or artifacts.get("group_id") or "").strip()
    access_policy = _dict_from(artifacts.get("access_policy"))
    if not access_policy:
        access_policy = {
            "owner_id": owner,
            "group_id": group_id,
            "visibility": str(artifacts.get("visibility") or state.get("visibility") or "private"),
            "task_bound_to_owner": bool(owner),
        }
    return EvidenceStoreSnapshot(
        thread_id=thread_id,
        sources=_list_from(artifacts.get("sources")),
        passages=_list_from(artifacts.get("passages")),
        claims=_list_from(artifacts.get("claims")),
        evidence_items=_list_from(artifacts.get("evidence_items")),
        citation_annotations=_list_from(artifacts.get("citation_annotations")),
        quality_results=_list_from(artifacts.get("quality_gates")),
        research_todos=_list_from(artifacts.get("research_todos")),
        todo_summary=_dict_from(artifacts.get("todo_summary")),
        source_routing=_dict_from(artifacts.get("source_routing")),
        access_policy=access_policy,
        metadata={
            "quality_summary": _dict_from(artifacts.get("quality_summary")),
            "quality_details": _dict_from(artifacts.get("quality_details")),
            "research_brief": _dict_from(artifacts.get("research_brief")),
            "fetched_pages": _list_from(artifacts.get("fetched_pages")),
            "provider_capabilities": _dict_from(artifacts.get("provider_capabilities")),
            "research_pipeline": _dict_from(artifacts.get("research_pipeline")),
        },
    )


def _list_from(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _dict_from(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}
