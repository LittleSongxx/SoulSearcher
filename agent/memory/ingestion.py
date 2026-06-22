from __future__ import annotations

import re
from typing import Any

from agent.memory.models import MemoryRecord, MemoryScope, MemoryType

SENSITIVE_RE = re.compile(
    r"(api[_ -]?key|secret|password|token|authorization|bearer\s+[A-Za-z0-9._-]+|"
    r"sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16})",
    re.IGNORECASE,
)


def is_sensitive(text: str) -> bool:
    return bool(SENSITIVE_RE.search(str(text or "")))


def _quality_passed(artifacts: dict[str, Any]) -> bool:
    summary = artifacts.get("quality_summary")
    if isinstance(summary, dict):
        verdict = str(
            summary.get("overall_verdict")
            or summary.get("delivery_status")
            or ""
        ).lower()
        if verdict in {"incomplete", "failed", "fail"}:
            return False
        if summary.get("publish_ready") is False:
            return False
    gates = artifacts.get("quality_gates")
    if isinstance(gates, list):
        severe = [
            gate
            for gate in gates
            if isinstance(gate, dict)
            and gate.get("passed") is False
            and str(gate.get("verdict") or "").lower() == "incomplete"
        ]
        if severe:
            return False
    return True


def _evidence_ids(artifacts: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for item in artifacts.get("evidence_items", []) or []:
        if isinstance(item, dict) and item.get("id"):
            ids.append(str(item["id"]))
    return ids


def _source_urls(artifacts: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for key in ("sources", "evidence_items", "passages"):
        for item in artifacts.get(key, []) or []:
            if not isinstance(item, dict):
                continue
            url = str(
                item.get("url")
                or item.get("canonical_url")
                or item.get("source")
                or ""
            ).strip()
            if url and url not in urls:
                urls.append(url)
    return urls


def _quality_score(artifacts: dict[str, Any]) -> float:
    summary = artifacts.get("quality_summary") if isinstance(artifacts, dict) else {}
    if isinstance(summary, dict):
        for key in ("overall_score", "level2_score", "level1_score"):
            try:
                value = float(summary.get(key))
                return max(0.0, min(1.0, value))
            except (TypeError, ValueError):
                continue
    return 0.0


def _content_candidates(
    *,
    research_brief: str,
    final_report: str,
    artifacts: dict[str, Any],
    notes: list[str],
) -> list[tuple[str, str, str, float, float]]:
    candidates: list[tuple[str, str, str, float, float]] = []

    for claim in artifacts.get("claims", []) or []:
        if not isinstance(claim, dict):
            continue
        text = str(claim.get("claim") or claim.get("claim_summary") or "").strip()
        status = str(claim.get("status") or "").lower()
        if text and status in {"supported", "partial", ""}:
            candidates.append((
                MemoryType.research_finding.value,
                text,
                text,
                0.85 if status == "supported" else 0.75,
                0.65,
            ))

    for item in artifacts.get("evidence_items", []) or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("content") or item.get("snippet") or item.get("title") or "").strip()
        if len(text) >= 60:
            candidates.append((MemoryType.source.value, text[:900], text[:240], 0.8, 0.55))

    for note in notes[:8]:
        text = " ".join(str(note or "").split())
        if len(text) >= 100:
            candidates.append((MemoryType.research_finding.value, text[:900], text[:240], 0.78, 0.55))

    if research_brief and final_report:
        report_summary = " ".join(str(final_report).split())[:1000]
        candidates.append((
            MemoryType.episode.value,
            f"Research brief: {research_brief}\nOutcome: {report_summary}",
            research_brief[:240],
            0.8,
            0.5,
        ))

    procedural_text = _procedural_candidate(artifacts)
    if procedural_text:
        candidates.append((MemoryType.procedure.value, procedural_text, procedural_text[:240], 0.82, 0.7))

    return candidates


def _procedural_candidate(artifacts: dict[str, Any]) -> str:
    gates = artifacts.get("quality_gates", [])
    if not isinstance(gates, list):
        return ""
    failed = [
        str(gate.get("name") or "")
        for gate in gates
        if isinstance(gate, dict) and gate.get("passed") is False
    ]
    if failed:
        return (
            "When quality gates fail for "
            + ", ".join(name for name in failed if name)[:160]
            + ", run focused follow-up research and verify citation evidence before final report."
        )
    summary = artifacts.get("quality_summary")
    if isinstance(summary, dict) and summary.get("publish_ready"):
        return "For similar DeepResearch runs, preserve source-backed evidence passages before drafting the final report."
    return ""


def build_memory_records_from_run(
    *,
    user_id: str,
    thread_id: str,
    run_id: str,
    research_brief: str,
    final_report: str,
    artifacts: dict[str, Any],
    notes: list[str],
    min_confidence: float,
    require_evidence: bool,
    sensitive_write_policy: str = "reject",
) -> tuple[list[MemoryRecord], list[dict[str, Any]]]:
    artifacts = artifacts if isinstance(artifacts, dict) else {}
    rejected: list[dict[str, Any]] = []
    sensitive_policy = str(sensitive_write_policy or "reject").strip().lower()
    if not _quality_passed(artifacts):
        rejected.append({"reason": "quality_gate_failed", "thread_id": thread_id})
        return [], rejected

    evidence_ids = _evidence_ids(artifacts)
    source_urls = _source_urls(artifacts)
    if require_evidence and not (evidence_ids or source_urls):
        rejected.append({"reason": "missing_evidence", "thread_id": thread_id})
        return [], rejected

    records: list[MemoryRecord] = []
    for record_type, content, summary, confidence, importance in _content_candidates(
        research_brief=research_brief,
        final_report=final_report,
        artifacts=artifacts,
        notes=notes,
    ):
        if confidence < min_confidence:
            rejected.append({"reason": "low_confidence", "content": content[:160]})
            continue
        if is_sensitive(content):
            if sensitive_policy == "redact":
                content = SENSITIVE_RE.sub("[REDACTED]", content)
                summary = SENSITIVE_RE.sub("[REDACTED]", summary)
            elif sensitive_policy == "allow":
                rejected.append({"reason": "sensitive_content_allowed", "content": content[:80]})
            else:
                rejected.append({"reason": "sensitive_content", "content": content[:80]})
                continue
        if require_evidence and record_type != MemoryType.procedure.value and not (evidence_ids or source_urls):
            rejected.append({"reason": "missing_evidence_for_record", "content": content[:160]})
            continue
        scope = (
            MemoryScope.user.value
            if record_type in {MemoryType.profile.value, MemoryType.preference.value}
            else MemoryScope.research.value
        )
        records.append(
            MemoryRecord(
                user_id=user_id,
                scope=scope,
                type=record_type,
                content=content,
                summary=summary,
                confidence=confidence,
                importance=importance,
                quality_score=_quality_score(artifacts),
                source_thread_id=thread_id,
                source_run_id=run_id,
                source_evidence_ids=evidence_ids[:12],
                source_urls=source_urls[:12],
                metadata={"research_brief": research_brief[:500]},
            )
        )
    return records, rejected
