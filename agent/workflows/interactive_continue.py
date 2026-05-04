from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


_ALLOWED_TARGET_TYPES = {"section", "claim", "source", "gap"}


@dataclass
class ContinueResearchPlan:
    request_id: str
    target_type: str
    target_id: str = ""
    target_index: Optional[int] = None
    target_text: str = ""
    instruction: str = ""
    generated_queries: List[str] = field(default_factory=list)
    resume_input: str = ""
    update_state: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value not in (None, "", [], {})}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stable_id(*parts: Any) -> str:
    raw = "|".join(_text(part) for part in parts)
    return "continue_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _one_based_index(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    try:
        index = int(value)
    except (TypeError, ValueError):
        return None
    return index if index > 0 else None


def _find_by_id_or_index(items: List[Dict[str, Any]], target_id: str = "", target_index: Optional[int] = None) -> Dict[str, Any]:
    target_id = _text(target_id)
    if target_id:
        for item in items or []:
            if not isinstance(item, dict):
                continue
            candidates = [item.get("id"), item.get("citation_id"), item.get("url"), item.get("claim")]
            if target_id in {_text(candidate) for candidate in candidates}:
                return item
    index = _one_based_index(target_index)
    if index is not None and 1 <= index <= len(items or []):
        item = items[index - 1]
        return item if isinstance(item, dict) else {}
    return {}


def _collect_quality_gaps(artifacts: Dict[str, Any]) -> List[str]:
    gaps: List[str] = []
    seen = set()
    for record in artifacts.get("quality_gates", []) or []:
        if not isinstance(record, dict):
            continue
        for gate in record.get("gates", []) or []:
            if not isinstance(gate, dict) or gate.get("status") != "fail":
                continue
            details = gate.get("details") if isinstance(gate.get("details"), dict) else {}
            for item in details.get("missing_dimensions") or []:
                text = _text(item)
                if text and text not in seen:
                    seen.add(text)
                    gaps.append(text)
    return gaps


def resolve_continue_target(
    artifacts: Dict[str, Any],
    *,
    target_type: str,
    target_id: str = "",
    target_index: Optional[int] = None,
    target_text: str = "",
) -> Dict[str, Any]:
    artifacts = artifacts if isinstance(artifacts, dict) else {}
    normalized_type = _text(target_type).lower().replace("-", "_")
    if normalized_type not in _ALLOWED_TARGET_TYPES:
        raise ValueError(f"unsupported continue research target_type: {target_type}")

    explicit_text = _text(target_text)
    if normalized_type == "claim":
        item = _find_by_id_or_index(artifacts.get("claims", []) or [], target_id, target_index)
        text = explicit_text or _text(item.get("claim")) or target_id
        return {"target_type": normalized_type, "target_id": target_id or _text(item.get("id")), "target_index": target_index, "target_text": text, "target": item}

    if normalized_type == "source":
        item = _find_by_id_or_index(artifacts.get("sources", []) or [], target_id, target_index)
        text = explicit_text or _text(item.get("title")) or _text(item.get("url")) or target_id
        return {"target_type": normalized_type, "target_id": target_id or _text(item.get("url")), "target_index": target_index, "target_text": text, "target": item}

    if normalized_type == "gap":
        gaps = _collect_quality_gaps(artifacts)
        index = _one_based_index(target_index)
        selected_gap = gaps[index - 1] if index is not None and 1 <= index <= len(gaps) else ""
        text = explicit_text or target_id or selected_gap or "quality/evidence gap"
        return {"target_type": normalized_type, "target_id": target_id or text, "target_index": target_index, "target_text": text, "target": {"gap": text}}

    text = explicit_text or target_id or "selected report section"
    return {"target_type": normalized_type, "target_id": target_id or text, "target_index": target_index, "target_text": text, "target": {"section": text}}


def _queries_for_target(target_type: str, target_text: str, instruction: str = "") -> List[str]:
    base = _text(target_text)
    detail = _text(instruction)
    suffix = f" {detail}" if detail else ""
    if target_type == "claim":
        return [
            f"{base} supporting evidence{suffix}".strip(),
            f"{base} counter evidence verification".strip(),
        ]
    if target_type == "source":
        return [
            f"{base} source verification original context{suffix}".strip(),
            f"{base} related official primary sources".strip(),
        ]
    if target_type == "gap":
        return [
            f"{base} missing evidence research{suffix}".strip(),
            f"{base} latest authoritative data".strip(),
        ]
    return [
        f"{base} deeper analysis{suffix}".strip(),
        f"{base} evidence examples risks".strip(),
    ]


def build_continue_research_plan(
    *,
    artifacts: Dict[str, Any],
    target_type: str,
    target_id: str = "",
    target_index: Optional[int] = None,
    target_text: str = "",
    instruction: str = "",
    strategy: str = "supervisor_workers",
) -> ContinueResearchPlan:
    resolved = resolve_continue_target(
        artifacts,
        target_type=target_type,
        target_id=target_id,
        target_index=target_index,
        target_text=target_text,
    )
    normalized_type = resolved["target_type"]
    resolved_text = resolved["target_text"]
    if not resolved_text:
        raise ValueError("continue research target could not be resolved")
    request_id = _stable_id(normalized_type, resolved.get("target_id"), resolved_text, instruction)
    queries = _queries_for_target(normalized_type, resolved_text, instruction)
    resume_input = (
        f"继续深入研究 {normalized_type}: {resolved_text}. "
        f"重点要求: {_text(instruction) or '补充证据、验证结论并更新报告。'}"
    )
    research_brief = dict(artifacts.get("research_brief") or {}) if isinstance(artifacts, dict) else {}
    research_brief.update(
        {
            "original_query": research_brief.get("original_query") or resolved_text,
            "clarified_goal": resume_input,
            "expected_fields": [normalized_type, "evidence", "verification"],
        }
    )
    continue_request = {
        "request_id": request_id,
        "target_type": normalized_type,
        "target_id": resolved.get("target_id", ""),
        "target_index": target_index,
        "target_text": resolved_text,
        "instruction": _text(instruction),
        "generated_queries": queries,
        "strategy": strategy,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    update_state = {
        "input": resume_input,
        "resume_input": resume_input,
        "research_brief": research_brief,
        "research_plan": queries,
        "missing_topics": [resolved_text] if normalized_type in {"claim", "gap"} else [],
        "interactive_continue": continue_request,
        "deepsearch_strategy_decision": {
            "strategy": strategy,
            "reason": "interactive continue research target",
            "parameters": {"target_type": normalized_type, "request_id": request_id},
            "confidence": 1.0,
        },
    }
    return ContinueResearchPlan(
        request_id=request_id,
        target_type=normalized_type,
        target_id=resolved.get("target_id", ""),
        target_index=target_index,
        target_text=resolved_text,
        instruction=_text(instruction),
        generated_queries=queries,
        resume_input=resume_input,
        update_state=update_state,
    )
