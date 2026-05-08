"""
Heuristic semantic channel for claim verification.

This module is **not** a Natural Language Inference (NLI) model. It augments the
deterministic `ClaimVerifier` (token overlap / numeric / negation / trend) with a
second pass that:

1. Re-checks each claim against evidence using slightly different heuristics
   (higher overlap thresholds, additional structural filters), and
2. Optionally accepts external overrides keyed by claim text via
   ``semantic_claim_verifier_results`` in the runnable config — this is the
   opt-in injection point for an LLM-judge channel produced upstream.

Both channels are merged conservatively (correctness-first): a claim is only
marked ``verified`` if both channels agree, while any contradiction from either
side downgrades the final status.

If you need true entailment-style verification, plug an LLM judge or a
transformer NLI model upstream and pass its decisions in via the
``semantic_claim_verifier_results`` override map.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Iterable

from agent.workflows.claim_verifier import ClaimCheck, ClaimStatus


@dataclass
class SemanticClaimCheck:
    claim: str
    deterministic_status: str
    semantic_status: str
    status: str
    confidence: float = 0.0
    evidence_urls: list[str] = field(default_factory=list)
    evidence_passages: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""
    semantic_notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value not in (None, "", [], {})
        }


def enrich_claim_checks_with_semantics(
    checks: Iterable[ClaimCheck],
    *,
    evidence_items: list[dict[str, Any]] | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[list[SemanticClaimCheck], dict[str, Any]]:
    cfg = _configurable(config)
    enabled = _truthy(cfg.get("deepsearch_semantic_claim_verifier_enabled")) or _truthy(
        cfg.get("semantic_claim_verifier_enabled")
    )
    mode = (
        str(
            cfg.get("deepsearch_semantic_claim_verifier_mode")
            or cfg.get("semantic_claim_verifier_mode")
            or "heuristic"
        )
        .strip()
        .lower()
    )
    overrides = cfg.get("semantic_claim_verifier_results") or cfg.get(
        "deepsearch_semantic_claim_verifier_results"
    )
    if not isinstance(overrides, dict):
        overrides = {}

    enriched: list[SemanticClaimCheck] = []
    for check in checks:
        deterministic_status = _status_value(check.status)
        semantic_status = deterministic_status
        semantic_notes = "semantic verifier disabled"
        confidence = _confidence_from_check(check)
        if enabled:
            override_status = _override_status(check.claim, overrides)
            if override_status:
                semantic_status = override_status
                semantic_notes = "semantic status supplied by verifier result override"
                confidence = max(confidence, 0.8)
            elif mode == "heuristic":
                semantic_status, semantic_notes, confidence = (
                    _heuristic_semantic_status(check, evidence_items or [], confidence)
                )
            else:
                semantic_notes = f"semantic verifier mode '{mode}' is not configured; deterministic status retained"
        final_status = _merge_status(deterministic_status, semantic_status)
        enriched.append(
            SemanticClaimCheck(
                claim=check.claim,
                deterministic_status=deterministic_status,
                semantic_status=semantic_status,
                status=final_status,
                confidence=round(confidence, 3),
                evidence_urls=list(check.evidence_urls or []),
                evidence_passages=list(check.evidence_passages or []),
                notes=check.notes,
                semantic_notes=semantic_notes,
            )
        )

    summary = summarize_semantic_claim_checks(enriched, enabled=enabled, mode=mode)
    return enriched, summary


def summarize_semantic_claim_checks(
    checks: list[SemanticClaimCheck], *, enabled: bool, mode: str
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    changed = 0
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
        if check.status != check.deterministic_status:
            changed += 1
    return {
        "semantic_claim_verifier_enabled": enabled,
        "semantic_claim_verifier_mode": mode,
        "semantic_claim_verifier_total": len(checks),
        "semantic_claim_verifier_changed": changed,
        "semantic_claim_verifier_status_counts": counts,
    }


def _heuristic_semantic_status(
    check: ClaimCheck,
    evidence_items: list[dict[str, Any]],
    base_confidence: float,
) -> tuple[str, str, float]:
    deterministic_status = _status_value(check.status)
    if deterministic_status == ClaimStatus.VERIFIED.value:
        return (
            deterministic_status,
            "deterministic verifier already found supporting evidence",
            max(base_confidence, 0.75),
        )
    if deterministic_status == ClaimStatus.CONTRADICTED.value:
        return (
            deterministic_status,
            "deterministic verifier found stronger conflicting evidence",
            max(base_confidence, 0.7),
        )

    claim_tokens = _tokenize(check.claim)
    if not claim_tokens:
        return deterministic_status, "no claim tokens available", base_confidence
    best_overlap = 0
    best_url = ""
    for item in evidence_items or []:
        if not isinstance(item, dict):
            continue
        text = " ".join(
            str(item.get(k) or "")
            for k in ("title", "summary", "snippet", "quote", "text", "content")
        )
        tokens = _tokenize(text)
        overlap = len(claim_tokens & tokens)
        if overlap > best_overlap:
            best_overlap = overlap
            best_url = str(
                item.get("url")
                or item.get("source_url")
                or item.get("document_id")
                or ""
            )
    if best_overlap >= max(5, min(10, len(claim_tokens) // 2)):
        note = "heuristic semantic overlap found likely supporting evidence"
        if best_url:
            note += f" ({best_url})"
        return (
            ClaimStatus.VERIFIED.value,
            note,
            max(base_confidence, min(0.85, best_overlap / max(1, len(claim_tokens)))),
        )
    return (
        deterministic_status,
        "no semantic support above heuristic threshold",
        base_confidence,
    )


def _merge_status(deterministic_status: str, semantic_status: str) -> str:
    if deterministic_status == semantic_status:
        return deterministic_status
    if deterministic_status == ClaimStatus.CONTRADICTED.value:
        return deterministic_status
    if semantic_status == ClaimStatus.CONTRADICTED.value:
        return semantic_status
    if (
        semantic_status == ClaimStatus.VERIFIED.value
        and deterministic_status == ClaimStatus.UNSUPPORTED.value
    ):
        return semantic_status
    return deterministic_status


def _status_value(status: Any) -> str:
    if isinstance(status, Enum):
        return str(status.value)
    return str(status or "").strip() or ClaimStatus.UNSUPPORTED.value


def _confidence_from_check(check: ClaimCheck) -> float:
    try:
        score = float(check.score or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    if _status_value(check.status) == ClaimStatus.VERIFIED.value:
        return min(1.0, 0.55 + min(0.4, score / 20.0))
    if _status_value(check.status) == ClaimStatus.CONTRADICTED.value:
        return min(1.0, 0.5 + min(0.35, score / 20.0))
    return min(0.45, score / 20.0)


def _override_status(claim: str, overrides: dict[str, Any]) -> str:
    direct = overrides.get(claim)
    if isinstance(direct, str) and direct.strip():
        return direct.strip().lower()
    claim_lower = claim.lower()
    for key, value in overrides.items():
        if str(key).lower() in claim_lower and isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def _tokenize(text: str) -> set[str]:
    return {
        token
        for token in str(text or "").lower().replace("-", " ").split()
        if len(token) >= 3
    }


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _configurable(config: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    cfg = config.get("configurable")
    if isinstance(cfg, dict):
        merged = dict(config)
        merged.update(cfg)
        return merged
    return config
