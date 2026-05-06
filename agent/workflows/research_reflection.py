from __future__ import annotations

from typing import Any

from agent.workflows.research_brief import ResearchBrief

_FOCUS_HINTS = {
    "citation_coverage": "补充可直接引用的一手来源和关键数据出处",
    "claim_verifier": "核验未支撑或矛盾声明并补充证据",
    "freshness": "补充最新时间线、监管更新和近期数据",
    "query_coverage": "补齐缺失研究维度",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _unique(values: list[str]) -> list[str]:
    output: list[str] = []
    seen = set()
    for value in values:
        text = _text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def gap_queries_from_quality_gates(
    *,
    brief: ResearchBrief,
    quality_gates: list[dict[str, Any]],
    claims: list[dict[str, Any]] | None = None,
    max_queries: int = 4,
) -> list[str]:
    topic = brief.clarified_goal or brief.original_query
    candidates: list[str] = []
    gate_hints: list[str] = []
    for gate in quality_gates or []:
        if not isinstance(gate, dict) or gate.get("status") != "fail":
            continue
        name = _text(gate.get("name"))
        action = _text(gate.get("action"))
        details = gate.get("details") if isinstance(gate.get("details"), dict) else {}
        for missing in details.get("missing_dimensions") or []:
            candidates.append(f"{topic} {missing} evidence official sources recent data")
        hint = _FOCUS_HINTS.get(name) or _FOCUS_HINTS.get(action)
        if hint:
            gate_hints.append(f"{topic} {hint}")

    for claim in claims or []:
        if not isinstance(claim, dict):
            continue
        if claim.get("status") not in {"unsupported", "contradicted"}:
            continue
        claim_text = _text(claim.get("claim"))
        if claim_text:
            candidates.append(f"{claim_text} source evidence verification")
    candidates.extend(gate_hints)

    for field in brief.expected_fields or []:
        if field:
            candidates.append(f"{topic} {field} official evidence")

    return _unique(candidates)[: max(1, int(max_queries or 1))]
