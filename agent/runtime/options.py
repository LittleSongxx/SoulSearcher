from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent.retrieval.policy import reject_legacy_source_routing

_RUNTIME_OPTION_KEYS = {
    "deepsearch_max_epochs",
    "deepsearch_max_seconds",
    "deepsearch_max_tokens",
    "deepsearch_max_research_units",
    "deepsearch_max_tool_calls_per_unit",
    "deepsearch_max_skills",
    "deepsearch_supervisor_rounds",
    "deepsearch_supervisor_max_workers",
    "deepsearch_supervisor_queries_per_worker",
    "deepsearch_supervisor_parallel_workers",
    "deepsearch_supervisor_think_enabled",
    "deepsearch_supervisor_max_depth",
    "deepsearch_supervisor_depth_confidence_threshold",
    "deepsearch_max_seconds_per_worker",
    "deepsearch_claim_verifier_use_passages",
    "deepsearch_claim_verifier_min_overlap_tokens",
    "deepsearch_claim_verifier_max_evidence_per_claim",
    "deepsearch_claim_verifier_max_claims",
    "deepsearch_guardrail_denied_tools",
    "deepsearch_guardrail_allowed_domains",
    "deepsearch_guardrail_denied_domains",
    "deepsearch_summary_trigger_tokens",
    "deepsearch_summary_trigger_messages",
    "deepsearch_summary_keep_recent",
    "deep_research_strict_citations",
    "tool_policy_strict",
    "retrieval_policy_strict",
    "legacy_citation_mode",
    "supervisor_model",
    "planner_model",
    "planning_model",
    "query_model",
    "query_gen_model",
    "search_summary_model",
    "summary_model",
    "synthesis_model",
    "worker_model",
    "researcher_model",
    "research_model",
    "compression_model",
    "writer_model",
    "final_report_model",
    "writing_model",
    "verifier_model",
    "evaluator_model",
    "evaluation_model",
    "reasoning_model",
    "retrieval_policy",
    "retrieval_allowed_origins",
    "retrieval_channels",
    "retrieval_methods",
    "retrieval_profiles",
    "allowed_domains",
    "denied_domains",
    "mcp_preset_ids",
    "mcp_results",
    "mcp_auth_required",
    "mcp_requires_auth",
    "mcp_tools_to_include",
    "mcp_tool_whitelist",
    "mcp_max_tools",
    "skill_ids",
    "deepsearch_skill_ids",
    "source_connectors",
    "user_injected_sources",
    "memory_source_candidates",
    "memory_retrieval",
}


_DICT_KEYS = {"retrieval_policy", "mcp_results", "research_brief_review", "memory_retrieval"}
_OBJECT_LIST_KEYS = {"source_connectors", "user_injected_sources", "mcp_results", "memory_source_candidates"}


class ResearchRuntimeOptions(BaseModel):
    """Validated request-scoped DeepResearch runtime options."""

    model_config = ConfigDict(extra="allow")

    retrieval_policy: dict[str, Any] = Field(default_factory=dict)
    skill_ids: list[str] = Field(default_factory=list)
    deepsearch_skill_ids: list[str] = Field(default_factory=list)
    source_connectors: list[dict[str, Any] | str | int | float | bool] = Field(default_factory=list)
    user_injected_sources: list[dict[str, Any] | str | int | float | bool] = Field(default_factory=list)
    memory_source_candidates: list[dict[str, Any] | str | int | float | bool] = Field(default_factory=list)
    mcp_results: dict[str, Any] | list[dict[str, Any] | str | int | float | bool] = Field(default_factory=dict)
    memory_retrieval: dict[str, Any] = Field(default_factory=dict)

    @field_validator("retrieval_policy", "memory_retrieval", mode="before")
    @classmethod
    def _dict_or_empty(cls, value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @field_validator("skill_ids", "deepsearch_skill_ids", mode="before")
    @classmethod
    def _list_of_strings(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        if isinstance(value, list):
            return [str(part).strip() for part in value if str(part).strip()]
        return []

    def to_config(self) -> dict[str, Any]:
        payload = self.model_dump(exclude_none=True)
        return {key: value for key, value in payload.items() if value not in ({}, [], "")}


def sanitize_research_runtime_options(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    reject_legacy_source_routing(value.get("source_routing"))
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key or "").strip()
        if key_text not in _RUNTIME_OPTION_KEYS:
            continue
        if isinstance(item, (str, int, float, bool)) or item is None:
            cleaned[key_text] = item
        elif key_text in _OBJECT_LIST_KEYS and isinstance(item, list):
            cleaned[key_text] = [
                part for part in item if isinstance(part, (dict, str, int, float, bool))
            ]
        elif isinstance(item, list):
            cleaned[key_text] = [str(part).strip() for part in item if str(part).strip()]
        elif key_text in _DICT_KEYS and isinstance(item, dict):
            if key_text == "retrieval_policy":
                reject_legacy_source_routing(item.get("source_routing"))
            cleaned[key_text] = item
    return ResearchRuntimeOptions.model_validate(cleaned).to_config()
