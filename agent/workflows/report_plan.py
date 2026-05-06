from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ReportSectionPlan:
    section_id: str
    title: str
    focus: str
    research_required: bool
    related_worker_ids: list[str] = field(default_factory=list)
    evidence_item_count: int = 0
    status: str = "planned"

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value not in (None, "", [], {})}


def build_sectioned_report_plan(
    *,
    research_brief: dict[str, Any],
    worker_runs: list[dict[str, Any]],
    evidence_items: list[dict[str, Any]],
) -> dict[str, Any]:
    topic = str(research_brief.get("clarified_goal") or research_brief.get("original_query") or "Research report").strip()
    expected_fields = _unique_text(research_brief.get("expected_fields") or [])
    worker_by_focus = _workers_by_focus(worker_runs)
    evidence_count = len([item for item in evidence_items or [] if isinstance(item, dict)])
    sections: list[ReportSectionPlan] = [
        ReportSectionPlan(
            section_id=_section_id("introduction", topic),
            title="Introduction",
            focus="overview",
            research_required=False,
            evidence_item_count=0,
        )
    ]

    for field in expected_fields:
        related_workers = worker_by_focus.get(field, [])
        sections.append(
            ReportSectionPlan(
                section_id=_section_id("field", field),
                title=_title_from_focus(field),
                focus=field,
                research_required=True,
                related_worker_ids=related_workers,
                evidence_item_count=evidence_count if related_workers else 0,
            )
        )

    if not expected_fields:
        for focus, workers in list(worker_by_focus.items())[:4]:
            sections.append(
                ReportSectionPlan(
                    section_id=_section_id("focus", focus),
                    title=_title_from_focus(focus),
                    focus=focus,
                    research_required=True,
                    related_worker_ids=workers,
                    evidence_item_count=evidence_count if workers else 0,
                )
            )

    sections.extend(
        [
            ReportSectionPlan(
                section_id=_section_id("evidence", topic),
                title="Evidence and Source Quality",
                focus="evidence",
                research_required=True,
                related_worker_ids=worker_by_focus.get("evidence", []),
                evidence_item_count=evidence_count,
            ),
            ReportSectionPlan(
                section_id=_section_id("risks", topic),
                title="Risks, Gaps, and Caveats",
                focus="risks",
                research_required=True,
                related_worker_ids=worker_by_focus.get("risks", []),
                evidence_item_count=evidence_count,
            ),
            ReportSectionPlan(
                section_id=_section_id("conclusion", topic),
                title="Conclusion",
                focus="synthesis",
                research_required=False,
                evidence_item_count=0,
            ),
        ]
    )

    serialized = [section.to_dict() for section in _dedupe_sections(sections)]
    return {
        "schema_version": 1,
        "topic": topic,
        "status": "planned",
        "requires_human_review": False,
        "section_count": len(serialized),
        "sections": serialized,
    }


def build_sectioned_report_artifact(
    report_plan: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    cfg = config.get("configurable") if isinstance(config, dict) else {}
    cfg = cfg if isinstance(cfg, dict) else {}
    enabled = _config_bool(
        cfg.get("deepsearch_sectioned_report", cfg.get("sectioned_report")),
        False,
    )
    review_required = _config_bool(
        cfg.get("deepsearch_sectioned_report_requires_approval", cfg.get("sectioned_report_requires_approval")),
        False,
    )
    sections = list(report_plan.get("sections") or []) if enabled else []
    return {
        "schema_version": 1,
        "enabled": enabled,
        "status": "planned" if enabled else "disabled",
        "execution_mode": "artifact_only",
        "review_required": bool(enabled and review_required),
        "section_count": len(sections),
        "sections": sections,
    }


def _section_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:10]
    return f"section_{prefix}_{digest}"


def _unique_text(values: Iterable[Any]) -> list[str]:
    output: list[str] = []
    seen = set()
    for value in values or []:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def _workers_by_focus(worker_runs: list[dict[str, Any]]) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for run in worker_runs or []:
        if not isinstance(run, dict):
            continue
        focus = str(run.get("focus") or "").strip()
        worker_id = str(run.get("worker_id") or "").strip()
        if not focus or not worker_id:
            continue
        mapping.setdefault(focus, []).append(worker_id)
    return mapping


def _title_from_focus(focus: str) -> str:
    text = str(focus or "").strip().replace("_", " ").replace("-", " ")
    if not text:
        return "Research Findings"
    return text[:1].upper() + text[1:]


def _dedupe_sections(sections: list[ReportSectionPlan]) -> list[ReportSectionPlan]:
    output: list[ReportSectionPlan] = []
    seen = set()
    for section in sections:
        key = (section.title.lower(), section.focus.lower())
        if key in seen:
            continue
        seen.add(key)
        output.append(section)
    return output


def _config_bool(value: Any, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
