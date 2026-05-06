"""
Lightweight branch evaluator for LATS-style tree search backtracking.

Scores completed research tree branches using heuristic signals
(no extra LLM call required):
- Number of findings
- Summary length / richness
- Source diversity
- Relevance score from decomposition

Branches scoring below ``tree_backtrack_score_threshold`` are eligible
for retry via an alternative subtopic decomposition.
"""

import logging
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import urlparse

from common.config import settings

if TYPE_CHECKING:
    from agent.workflows.research_tree import ResearchTreeNode

logger = logging.getLogger(__name__)


def _clip_score(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _normalized_unique_domains(sources: list[str]) -> float:
    domains = set()
    for source in sources or []:
        host = urlparse(str(source or "").strip()).netloc.lower()
        if host:
            domains.add(host)
    return min(len(domains) / 4.0, 1.0)


def evaluate_branch(node: "ResearchTreeNode") -> dict[str, Any]:
    findings = node.findings if isinstance(node.findings, list) else []
    findings_count = len(findings)
    summary_len = len(node.summary) if node.summary else 0
    sources = [str(s).strip() for s in (node.sources or []) if str(s).strip()]
    unique_sources = list(dict.fromkeys(sources))
    unique_queries = {
        str(q).strip().lower()
        for q in (node.queries or [])
        if isinstance(q, str) and q.strip()
    }
    relevance = _clip_score(node.relevance_score)

    evidence_rich = 0
    numeric_evidence = 0
    result_scores: list[float] = []
    for finding in findings:
        result = finding.get("result", {}) if isinstance(finding, dict) else {}
        if not isinstance(result, dict):
            continue
        snippet = (
            result.get("raw_excerpt")
            or result.get("summary")
            or result.get("snippet")
            or result.get("content")
            or ""
        )
        text = str(snippet or "").strip()
        if text:
            evidence_rich += 1
        if any(ch.isdigit() for ch in text):
            numeric_evidence += 1
        try:
            result_scores.append(float(result.get("score", 0.0) or 0.0))
        except (TypeError, ValueError):
            continue

    findings_score = min(findings_count / 6.0, 1.0)
    summary_score = min(summary_len / 700.0, 1.0)
    source_score = min(len(unique_sources) / 4.0, 1.0)
    domain_score = _normalized_unique_domains(unique_sources)
    query_score = min(
        len(unique_queries) / max(2.0, float(min(4, max(1, len(node.queries or []))))),
        1.0,
    )
    evidence_density = (evidence_rich / findings_count) if findings_count else 0.0
    numeric_density = (numeric_evidence / findings_count) if findings_count else 0.0
    avg_result_score = (
        sum(_clip_score(score) for score in result_scores) / len(result_scores)
        if result_scores
        else 0.0
    )
    duplicate_penalty = (
        1.0 - (len(unique_sources) / max(1.0, float(findings_count)))
        if findings_count
        else 1.0
    )

    evidence_score = _clip_score(
        (findings_score * 0.35)
        + (evidence_density * 0.35)
        + (numeric_density * 0.15)
        + (avg_result_score * 0.15)
    )
    source_diversity_score = _clip_score((source_score * 0.55) + (domain_score * 0.45))
    coverage_score = _clip_score((query_score * 0.55) + (summary_score * 0.45))
    score = _clip_score(
        (evidence_score * 0.28)
        + (source_diversity_score * 0.24)
        + (coverage_score * 0.18)
        + (summary_score * 0.15)
        + (relevance * 0.15)
    )
    score = _clip_score(score * max(0.55, 1.0 - (duplicate_penalty * 0.35)))

    focus_areas: list[str] = []
    if findings_count < 3:
        focus_areas.append("补充更多一手或高置信度检索结果")
    if source_score < 0.5:
        focus_areas.append("补齐官方来源、原始数据或权威报告")
    if domain_score < 0.5:
        focus_areas.append("增加独立来源和跨站点证据，避免单一来源")
    if evidence_density < 0.6 or numeric_density < 0.2:
        focus_areas.append("优先搜索带有数据、统计或明确事实支撑的证据")
    if coverage_score < 0.55:
        focus_areas.append("补齐缺失维度，如风险、限制、实施细节、最新进展")
    if duplicate_penalty > 0.4:
        focus_areas.append("换一个检索角度，减少重复结果")

    signals = {
        "findings_score": round(findings_score, 4),
        "summary_score": round(summary_score, 4),
        "source_score": round(source_score, 4),
        "domain_score": round(domain_score, 4),
        "query_score": round(query_score, 4),
        "evidence_score": round(evidence_score, 4),
        "source_diversity_score": round(source_diversity_score, 4),
        "coverage_score": round(coverage_score, 4),
        "duplicate_penalty": round(duplicate_penalty, 4),
        "relevance": round(relevance, 4),
    }

    return {
        "score": round(score, 4),
        "focus_areas": focus_areas[:4],
        "signals": signals,
        "summary": {
            "findings_count": findings_count,
            "unique_sources": len(unique_sources),
            "unique_queries": len(unique_queries),
            "summary_length": summary_len,
        },
    }


def score_branch(node: "ResearchTreeNode") -> float:
    """
    Score a completed branch on a 0-1 scale using heuristic signals.

    Components (equally weighted):
    - findings_score:  normalized count of findings (cap at 10)
    - summary_score:   normalized summary length (cap at 500 chars)
    - sources_score:   normalized unique source count (cap at 5)
    - relevance_score: the original decomposition relevance (already 0-1)
    """
    evaluation = evaluate_branch(node)
    score = float(evaluation["score"])
    signals = evaluation.get("signals", {})

    logger.debug(
        f"[tree_evaluator] Branch {node.id} score={score:.3f} "
        f"(evidence={signals.get('evidence_score', 0.0):.2f}, "
        f"coverage={signals.get('coverage_score', 0.0):.2f}, "
        f"sources={signals.get('source_diversity_score', 0.0):.2f}, "
        f"relevance={signals.get('relevance', 0.0):.2f})"
    )
    return round(score, 4)


def build_backtrack_queries(
    node: "ResearchTreeNode",
    evaluation: Optional[dict[str, Any]] = None,
    *,
    limit: int = 3,
) -> list[str]:
    evaluation = evaluation or evaluate_branch(node)
    signals = evaluation.get("signals", {}) if isinstance(evaluation, dict) else {}
    existing_queries = {
        str(q).strip().lower()
        for q in (node.queries or [])
        if isinstance(q, str) and q.strip()
    }

    candidates: list[str] = []
    topic = str(node.topic or "").strip()
    if not topic:
        return []

    if signals.get("source_score", 0.0) < 0.5:
        candidates.extend(
            [
                f"{topic} official report data",
                f"{topic} primary source guidance",
            ]
        )
    if signals.get("domain_score", 0.0) < 0.5:
        candidates.append(f"{topic} independent analysis case study")
    if signals.get("evidence_score", 0.0) < 0.55:
        candidates.append(f"{topic} statistics evidence benchmark")
    if signals.get("coverage_score", 0.0) < 0.55:
        candidates.append(f"{topic} risks limitations implementation latest updates")
    if signals.get("duplicate_penalty", 0.0) > 0.4:
        candidates.append(f"{topic} alternative perspective")

    focus_areas = (
        evaluation.get("focus_areas", []) if isinstance(evaluation, dict) else []
    )
    for focus in focus_areas:
        if isinstance(focus, str) and focus.strip():
            candidates.append(f"{topic} {focus.strip()}")

    deduped: list[str] = []
    seen = set(existing_queries)
    for candidate in candidates:
        normalized = " ".join(str(candidate or "").split()).strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
        if len(deduped) >= max(1, limit):
            break
    return deduped


def should_backtrack(node: "ResearchTreeNode") -> bool:
    """
    Determine if a branch should be retried via backtracking.

    Returns True if:
    - tree_backtrack_enabled is True
    - The branch score is below tree_backtrack_score_threshold
    - The node hasn't already been retried (retry_count check via node status)
    """
    if not settings.tree_backtrack_enabled:
        return False

    evaluation = evaluate_branch(node)
    score = float(evaluation["score"])
    threshold = float(settings.tree_backtrack_score_threshold)

    if score < threshold:
        logger.info(
            f"[tree_evaluator] Branch {node.id} ({node.topic[:40]}) "
            f"score {score:.3f} < threshold {threshold} → backtrack candidate"
            f" | focus={evaluation.get('focus_areas', [])[:2]}"
        )
        return True
    return False
