from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Iterable, List


_APPROVE_ACTIONS = {"approve", "approved", "accept", "accepted"}
_EDIT_ACTIONS = {"edit", "edited", "revise", "revised"}
_REJECT_ACTIONS = {"reject", "rejected", "cancel", "cancelled"}


def apply_sectioned_report_review(
    report_plan: Dict[str, Any],
    review_payload: Dict[str, Any] | None,
    *,
    approval_required: bool = False,
) -> Dict[str, Any]:
    base_sections = [section for section in report_plan.get("sections") or [] if isinstance(section, dict)]
    review = review_payload if isinstance(review_payload, dict) else {}
    action = str(review.get("action") or review.get("status") or "").strip().lower()
    if action in _REJECT_ACTIONS:
        return _review_result("rejected", base_sections, should_execute=False, review_payload=review)
    if action in _EDIT_ACTIONS:
        edited_sections = _merge_edited_sections(base_sections, review.get("sections") or [])
        return _review_result("edited", edited_sections, should_execute=True, review_payload=review)
    if action in _APPROVE_ACTIONS:
        return _review_result("approved", base_sections, should_execute=True, review_payload=review)
    if approval_required:
        return _review_result("pending_approval", base_sections, should_execute=False, review_payload=review)
    return _review_result("auto_approved", base_sections, should_execute=True, review_payload=review)


def build_section_search_query(topic: str, section: Dict[str, Any], *, follow_up_reason: str = "") -> str:
    title = str(section.get("title") or "").strip()
    focus = str(section.get("focus") or title or "evidence").strip()
    query = f"{topic} {focus} evidence sources"
    if follow_up_reason:
        query = f"{query} {follow_up_reason} follow up"
    return re.sub(r"\s+", " ", query).strip()


def grade_section_content(
    section: Dict[str, Any],
    content: str,
    evidence_items: Iterable[Dict[str, Any]],
    *,
    min_chars: int = 120,
    min_evidence: int = 1,
) -> Dict[str, Any]:
    text = str(content or "").strip()
    evidence_count = len([item for item in evidence_items or [] if isinstance(item, dict)])
    research_required = bool(section.get("research_required", True))
    reasons: List[str] = []
    if len(text) < max(1, int(min_chars or 1)):
        reasons.append("section_too_short")
    if research_required and evidence_count < max(0, int(min_evidence or 0)):
        reasons.append("insufficient_evidence")
    score = 1.0
    if reasons:
        score = max(0.0, 1.0 - 0.35 * len(reasons))
    return {
        "status": "pass" if not reasons else "fail",
        "score": round(score, 3),
        "reasons": reasons,
        "evidence_count": evidence_count,
        "content_chars": len(text),
        "follow_up_queries": [build_section_search_query("", section, follow_up_reason=reason) for reason in reasons],
    }


def compile_sectioned_report(section_results: List[Dict[str, Any]]) -> str:
    blocks: List[str] = []
    for result in section_results or []:
        if not isinstance(result, dict):
            continue
        title = str(result.get("title") or "Section").strip()
        content = str(result.get("content") or "").strip()
        if not content:
            continue
        blocks.append(f"## {title}\n\n{content}")
    return "\n\n".join(blocks).strip()


def _review_result(
    status: str,
    sections: List[Dict[str, Any]],
    *,
    should_execute: bool,
    review_payload: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "review_status": status,
        "should_execute": should_execute,
        "section_count": len(sections),
        "sections": sections,
        "review_notes": str(review_payload.get("notes") or "").strip(),
    }


def _merge_edited_sections(base_sections: List[Dict[str, Any]], edited_sections: Iterable[Any]) -> List[Dict[str, Any]]:
    base_by_id = {str(section.get("section_id") or ""): dict(section) for section in base_sections}
    output: List[Dict[str, Any]] = []
    for raw in edited_sections or []:
        if not isinstance(raw, dict):
            continue
        section_id = str(raw.get("section_id") or "").strip()
        merged = dict(base_by_id.get(section_id, {})) if section_id else {}
        merged.update({key: value for key, value in raw.items() if value not in (None, "", [], {})})
        if not merged.get("section_id"):
            merged["section_id"] = _stable_section_id(merged)
        if "research_required" not in merged:
            merged["research_required"] = True
        output.append(merged)
    if output:
        return output
    return [dict(section) for section in base_sections]


def _stable_section_id(section: Dict[str, Any]) -> str:
    raw = "|".join([str(section.get("title") or ""), str(section.get("focus") or "")])
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    return f"section_edit_{digest}"
