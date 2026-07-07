from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ArtifactModel(BaseModel):
    """Base model for internal research artifact DTOs."""

    model_config = ConfigDict(extra="allow")

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class EvidenceItemDTO(ArtifactModel):
    id: str = ""
    title: str = ""
    url: str = ""
    source: str = ""
    content: str = ""
    tool: str = ""
    query: str = ""
    score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class QualitySummaryDTO(ArtifactModel):
    publish_ready: bool | None = None
    delivery_status: str = ""
    overall_score: float | None = None
    level1_score: float | None = None
    level2_score: float | None = None
    quality_gate_count: int | None = None


class RetrievalPolicyDTO(ArtifactModel):
    allowed_origins: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    profiles: list[str] = Field(default_factory=list)
    domain_policy: dict[str, Any] = Field(default_factory=dict)
    corpus_policy: dict[str, Any] = Field(default_factory=dict)
    connector_policy: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)


class ResearchArtifactsDTO(ArtifactModel):
    schema_version: int = 1
    retrieval_policy: dict[str, Any] = Field(default_factory=dict)
    memory_retrieval: dict[str, Any] = Field(default_factory=dict)
    user_injected_sources: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    passages: list[dict[str, Any]] = Field(default_factory=list)
    evidence_items: list[dict[str, Any]] = Field(default_factory=list)
    claims: list[dict[str, Any]] = Field(default_factory=list)
    citation_annotations: list[dict[str, Any]] = Field(default_factory=list)
    citation_table: list[dict[str, Any]] = Field(default_factory=list)
    claim_citation_matrix: dict[str, Any] = Field(default_factory=dict)
    quality_summary: dict[str, Any] = Field(default_factory=dict)
    quality_gates: list[dict[str, Any]] = Field(default_factory=list)
    quality_details: dict[str, Any] = Field(default_factory=dict)
    plan_graph: dict[str, Any] = Field(default_factory=dict)
    plan_events: list[dict[str, Any]] = Field(default_factory=list)
    plan_summary: dict[str, Any] = Field(default_factory=dict)
    research_todos: list[dict[str, Any]] = Field(default_factory=list)
    todo_summary: dict[str, Any] = Field(default_factory=dict)
    research_brief: dict[str, Any] = Field(default_factory=dict)
    workspace: dict[str, Any] = Field(default_factory=dict)
    retrieval_diagnostics: list[dict[str, Any]] = Field(default_factory=list)


def normalize_evidence_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        output.append(EvidenceItemDTO.model_validate(item).to_dict())
    return output


def normalize_quality_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return QualitySummaryDTO.model_validate(value).to_dict()


def normalize_retrieval_policy_artifact(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return RetrievalPolicyDTO.model_validate(value).to_dict()


def normalize_deepsearch_artifacts(value: Any) -> dict[str, Any]:
    """Normalize persisted research artifacts while preserving unknown extensions."""

    if not isinstance(value, dict):
        return ResearchArtifactsDTO().to_dict()
    payload = dict(value)
    if "evidence_items" in payload:
        payload["evidence_items"] = normalize_evidence_items(payload.get("evidence_items"))
    if "quality_summary" in payload:
        payload["quality_summary"] = normalize_quality_summary(payload.get("quality_summary"))
    if "retrieval_policy" in payload:
        payload["retrieval_policy"] = normalize_retrieval_policy_artifact(payload.get("retrieval_policy"))
    return ResearchArtifactsDTO.model_validate(payload).to_dict()
