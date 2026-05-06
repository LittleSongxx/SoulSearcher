from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ResearchBriefReviewArtifact:
    status: str
    approval_required: bool
    approved: bool
    missing_fields: list[str] = field(default_factory=list)
    inferred_constraints: dict[str, Any] = field(default_factory=dict)
    success_criteria: list[str] = field(default_factory=list)
    source_policy: str = ""
    expected_fields: list[str] = field(default_factory=list)
    next_action: str = "execute"

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value not in (None, "", [], {})
        }


def build_research_brief_review_artifact(
    *,
    research_brief: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = _configurable(config)
    approval_required = _truthy(
        cfg.get("deepsearch_brief_review_required")
        or cfg.get("research_brief_review_required")
    )
    approved_payload = cfg.get("deepsearch_brief_review") or cfg.get("research_brief_review")
    approved = not approval_required
    review_notes = ""
    if isinstance(approved_payload, dict):
        approved = bool(approved_payload.get("approved", approved))
        review_notes = str(approved_payload.get("notes") or "").strip()

    missing_fields = _missing_fields(research_brief)
    status = "approved" if approved else "pending_approval"
    if missing_fields and not approval_required:
        status = "auto_completed_with_warnings"
    next_action = "execute" if approved else "await_user_review"
    constraints = research_brief.get("constraints") if isinstance(research_brief.get("constraints"), dict) else {}
    inferred_constraints = {
        key: value
        for key, value in {
            "freshness_requirement": research_brief.get("freshness_requirement"),
            "citation_policy": research_brief.get("citation_policy"),
            "source_policy": research_brief.get("source_policy"),
            "complexity": research_brief.get("complexity"),
            "scope": research_brief.get("scope"),
            **constraints,
        }.items()
        if value not in (None, "", [], {})
    }
    artifact = ResearchBriefReviewArtifact(
        status=status,
        approval_required=approval_required,
        approved=approved,
        missing_fields=missing_fields,
        inferred_constraints=inferred_constraints,
        success_criteria=[str(item) for item in research_brief.get("success_criteria") or []],
        source_policy=str(research_brief.get("source_policy") or ""),
        expected_fields=[str(item) for item in research_brief.get("expected_fields") or []],
        next_action=next_action,
    ).to_dict()
    if review_notes:
        artifact["review_notes"] = review_notes
    return artifact


def _missing_fields(brief: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not str(brief.get("clarified_goal") or brief.get("original_query") or "").strip():
        missing.append("goal")
    if not brief.get("expected_fields"):
        missing.append("expected_fields")
    if not brief.get("success_criteria"):
        missing.append("success_criteria")
    if not brief.get("source_policy"):
        missing.append("source_policy")
    return missing


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on", "required"}


def _configurable(config: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    cfg = config.get("configurable")
    if isinstance(cfg, dict):
        merged = dict(config)
        merged.update(cfg)
        return merged
    return config
