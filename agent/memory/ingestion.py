from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agent.memory.models import MemoryRecord, MemoryScope, MemoryType

SENSITIVE_RE = re.compile(
    r"(api[_ -]?key|secret|password|token|authorization|bearer\s+[A-Za-z0-9._-]+|"
    r"sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16})",
    re.IGNORECASE,
)


@dataclass(slots=True)
class MemoryCandidate:
    record_type: str
    content: str
    summary: str
    confidence: float
    importance: float
    evidence_ids: list[str] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)


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


def _evidence_index(artifacts: dict[str, Any]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for item in artifacts.get("evidence_items", []) or []:
        if not isinstance(item, dict):
            continue
        ref = {
            "id": str(item.get("id") or "").strip(),
            "url": str(item.get("url") or item.get("canonical_url") or item.get("source") or "").strip(),
            "content": str(item.get("content") or item.get("snippet") or item.get("title") or "").strip(),
        }
        if ref["id"] or ref["url"]:
            refs.append(ref)
    return refs


def _candidate_refs(
    *,
    evidence_refs: list[dict[str, str]],
    source_id: str = "",
    canonical_url: str = "",
    content: str = "",
) -> tuple[list[str], list[str]]:
    ids: list[str] = []
    urls: list[str] = []
    source_id = str(source_id or "").strip()
    canonical_url = str(canonical_url or "").strip()
    content_key = " ".join(str(content or "").split())[:160].casefold()
    for ref in evidence_refs:
        ref_id = ref.get("id", "")
        ref_url = ref.get("url", "")
        ref_content = " ".join(ref.get("content", "").split())[:160].casefold()
        matched = False
        if source_id and source_id == ref_id:
            matched = True
        if canonical_url and canonical_url == ref_url:
            matched = True
        if content_key and ref_content and (content_key in ref_content or ref_content in content_key):
            matched = True
        if matched:
            if ref_id and ref_id not in ids:
                ids.append(ref_id)
            if ref_url and ref_url not in urls:
                urls.append(ref_url)
    if canonical_url and canonical_url not in urls:
        urls.append(canonical_url)
    return ids, urls


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
) -> list[MemoryCandidate]:
    candidates: list[MemoryCandidate] = []
    evidence_refs = _evidence_index(artifacts)

    for claim in artifacts.get("claims", []) or []:
        if not isinstance(claim, dict):
            continue
        text = str(claim.get("claim") or claim.get("claim_summary") or "").strip()
        status = str(claim.get("status") or "").lower()
        if text and status in {"supported", "partial", ""}:
            evidence_ids, source_urls = _candidate_refs(
                evidence_refs=evidence_refs,
                source_id=str(claim.get("source_id") or ""),
                canonical_url=str(claim.get("canonical_url") or ""),
                content=text,
            )
            candidates.append(MemoryCandidate(
                MemoryType.research_finding.value,
                text,
                text,
                0.85 if status == "supported" else 0.75,
                0.65,
                evidence_ids=evidence_ids,
                source_urls=source_urls,
            ))

    for item in artifacts.get("evidence_items", []) or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("content") or item.get("snippet") or item.get("title") or "").strip()
        if len(text) >= 60:
            evidence_id = str(item.get("id") or "").strip()
            source_url = str(item.get("url") or item.get("canonical_url") or item.get("source") or "").strip()
            candidates.append(MemoryCandidate(
                MemoryType.source.value,
                text[:900],
                text[:240],
                0.8,
                0.55,
                evidence_ids=[evidence_id] if evidence_id else [],
                source_urls=[source_url] if source_url else [],
            ))

    for note in notes[:8]:
        text = " ".join(str(note or "").split())
        if len(text) >= 100:
            evidence_ids, source_urls = _candidate_refs(
                evidence_refs=evidence_refs,
                content=text,
            )
            candidates.append(MemoryCandidate(
                MemoryType.research_finding.value,
                text[:900],
                text[:240],
                0.78,
                0.55,
                evidence_ids=evidence_ids,
                source_urls=source_urls,
            ))

    if research_brief and final_report:
        report_summary = " ".join(str(final_report).split())[:1000]
        candidates.append(MemoryCandidate(
            MemoryType.episode.value,
            f"Research brief: {research_brief}\nOutcome: {report_summary}",
            research_brief[:240],
            0.8,
            0.5,
            evidence_ids=_evidence_ids(artifacts)[:12],
            source_urls=_source_urls(artifacts)[:12],
        ))

    procedural_text = _procedural_candidate(artifacts)
    if procedural_text:
        candidates.append(MemoryCandidate(
            MemoryType.procedure.value,
            procedural_text,
            procedural_text[:240],
            0.82,
            0.7,
        ))

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
    for candidate in _content_candidates(
        research_brief=research_brief,
        final_report=final_report,
        artifacts=artifacts,
        notes=notes,
    ):
        record_type = candidate.record_type
        content = candidate.content
        summary = candidate.summary
        confidence = candidate.confidence
        importance = candidate.importance
        record_evidence_ids = candidate.evidence_ids or evidence_ids
        record_source_urls = candidate.source_urls or source_urls
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
        if require_evidence and record_type != MemoryType.procedure.value and not (record_evidence_ids or record_source_urls):
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
                source_evidence_ids=record_evidence_ids[:12],
                source_urls=record_source_urls[:12],
                metadata={
                    "research_brief": research_brief[:500],
                    "record_level_provenance": True,
                },
            )
        )
    return records, rejected
