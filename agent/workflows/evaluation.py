"""Evaluation helpers for vertical industry research quality gates."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)


# =============================================================================
# Data Models
# =============================================================================

@dataclass
class EvalDimension:
    """A single evaluation dimension with score and justification."""
    name: str
    score: float  # 0.0 - 1.0
    weight: float = 1.0
    justification: str = ""
    passed: bool = True


@dataclass
class EvalResult:
    """Complete evaluation result."""
    overall_score: float = 0.0
    overall_passed: bool = False
    dimensions: list[EvalDimension] = field(default_factory=list)
    summary: str = ""
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_score": self.overall_score,
            "overall_passed": self.overall_passed,
            "dimensions": [
                {
                    "name": d.name,
                    "score": d.score,
                    "weight": d.weight,
                    "justification": d.justification,
                    "passed": d.passed,
                }
                for d in self.dimensions
            ],
            "summary": self.summary,
            "errors": self.errors,
            "metadata": self.metadata,
        }


# =============================================================================
# Level 2: 9-Dimension Dev Evaluation
# =============================================================================

LEVEL2_EVAL_PROMPT = """Evaluate the quality of this research report against 9 criteria.
Respond with a JSON object.

<Report>
{report}
</Report>

<Research Brief>
{research_brief}
</Research Brief>

<Topic>
{topic}
</Topic>

Evaluate against these 9 criteria:

1. **topic_relevance_overall** (weight=1.5): How thoroughly does the report address the topic?
2. **section_relevance_critical** (weight=2.0): Are ALL sections directly relevant?
3. **structure_and_flow** (weight=1.0): Do sections flow logically?
4. **introduction_quality** (weight=1.0): Does the introduction provide context and scope?
5. **conclusion_quality** (weight=1.0): Does the conclusion summarize key findings?
6. **structural_elements** (weight=0.5): Proper use of tables, lists, etc.?
7. **section_headers** (weight=0.5): Correct markdown formatting?
8. **citations** (weight=1.0): Proper source citation throughout?
9. **overall_quality** (weight=1.5): Well-researched, accurate, professionally written?

For each criterion, provide:
- score: 0.0-1.0
- justification: brief explanation
- passed: true if score >= 0.6

Overall passed = ALL critical criteria (weights > 1.0) must pass.

Respond with JSON:
{{
  "dimensions": [
    {{"name": "...", "score": 0.0-1.0, "justification": "...", "passed": true/false}},
    ...
  ],
  "overall_passed": true/false,
  "summary": "overall evaluation summary"
}}
"""


async def run_level2_evaluation(
    report: str,
    research_brief: str,
    topic: str,
    model_name: str,
) -> EvalResult:
    """Run Level 2 dev evaluation using smart_llm.

    9 dimensions with weighted scoring and binary pass/fail.
    Pytest-oriented evaluation with 9 criteria.

    Args:
        report: The generated report to evaluate.
        research_brief: The original research brief.
        topic: The research topic.
        model_name: LLM model to use for evaluation.

    Returns:
        EvalResult with dimension scores and overall pass/fail.
    """
    weights = {
        "topic_relevance_overall": 1.5,
        "section_relevance_critical": 2.0,
        "structure_and_flow": 1.0,
        "introduction_quality": 1.0,
        "conclusion_quality": 1.0,
        "structural_elements": 0.5,
        "section_headers": 0.5,
        "citations": 1.0,
        "overall_quality": 1.5,
    }

    try:
        from agent.core.model_routing import build_model_config, configurable_model

        model_config = build_model_config(
            model=model_name,
            max_tokens=2048,
            temperature=0.0,
            tags=["langsmith:nostream"],
        )

        prompt = LEVEL2_EVAL_PROMPT.format(
            report=report[:12000],
            research_brief=research_brief[:2000],
            topic=topic[:500],
        )

        response = await configurable_model.with_config(model_config).ainvoke([
            SystemMessage(content="You are an expert research evaluator. Respond only with valid JSON."),
            HumanMessage(content=prompt),
        ])

        content = response.content if hasattr(response, "content") else str(response)
        data = _parse_json_response(content)

        dimensions = []
        for d in data.get("dimensions", []):
            name = d.get("name", "unknown")
            dimensions.append(EvalDimension(
                name=name,
                score=float(d.get("score", 0.0)),
                weight=weights.get(name, 1.0),
                justification=str(d.get("justification", "")),
                passed=bool(d.get("passed", False)),
            ))

        # Calculate weighted score
        total_weight = sum(d.weight for d in dimensions)
        weighted_score = sum(d.score * d.weight for d in dimensions) / total_weight if total_weight > 0 else 0

        return EvalResult(
            overall_score=round(weighted_score, 3),
            overall_passed=bool(data.get("overall_passed", False)),
            dimensions=dimensions,
            summary=str(data.get("summary", "")),
        )

    except Exception as e:
        logger.error(f"[Eval] Level 2 evaluation failed: {e}")
        return EvalResult(
            overall_score=0.0,
            overall_passed=False,
            errors=[str(e)],
        )


# =============================================================================
# Level 3: Deep 4-Dimension Evaluation (strategic_llm)
# =============================================================================

LEVEL3_EVAL_PROMPT = """Conduct a deep, multi-dimensional evaluation of this research report.
This is a comprehensive quality assessment for production monitoring.

<Report>
{report}
</Report>

<Research Brief>
{research_brief}
</Research Brief>

Evaluate against 4 high-level dimensions (1-5 scale):

1. **coverage** (weight=0.30): How comprehensively does the report cover all aspects of the topic?
   - 1: Surface-level, major gaps
   - 3: Adequate coverage of main points
   - 5: Exhaustive coverage with nuanced insights

2. **accuracy** (weight=0.25): How factually accurate is the content?
   - 1: Multiple factual errors
   - 3: Generally accurate with minor issues
   - 5: All claims accurate and well-supported

3. **freshness** (weight=0.20): How current is the information?
   - 1: Outdated, missing key recent developments
   - 3: Reasonably current
   - 5: Cutting-edge, includes latest developments

4. **coherence** (weight=0.25): How well-structured and readable is the report?
   - 1: Disorganized, hard to follow
   - 3: Adequately organized
   - 5: Excellently structured, clear narrative flow

For each dimension, provide:
- score: 1-5
- evidence: specific examples from the report
- recommendation: how to improve

Overall score = weighted average of dimension scores.

Respond with JSON:
{{
  "dimensions": [
    {{"name": "coverage", "score": 1-5, "evidence": "...", "recommendation": "..."}},
    {{"name": "accuracy", "score": 1-5, "evidence": "...", "recommendation": "..."}},
    {{"name": "freshness", "score": 1-5, "evidence": "...", "recommendation": "..."}},
    {{"name": "coherence", "score": 1-5, "evidence": "...", "recommendation": "..."}}
  ],
  "overall_analysis": "comprehensive analysis paragraph",
  "degradation_detected": true/false,
  "degradation_details": "if detected, describe what regressed"
}}
"""


async def run_level3_evaluation(
    report: str,
    research_brief: str,
    model_name: str,
    previous_scores: Optional[dict[str, float]] = None,
) -> EvalResult:
    """Run Level 3 deep evaluation using strategic_llm.

    4 dimensions with 1-5 weighted scoring.
    Includes degradation detection for trend analysis.
    Optional dataset evaluation for quality monitoring.

    Args:
        report: The generated report.
        research_brief: The original research brief.
        model_name: Strategic LLM for deep evaluation.
        previous_scores: Previous scores for degradation detection.

    Returns:
        EvalResult with weighted dimension scores.
    """
    weights = {
        "coverage": 0.30,
        "accuracy": 0.25,
        "freshness": 0.20,
        "coherence": 0.25,
    }

    try:
        from agent.core.model_routing import build_model_config, configurable_model

        model_config = build_model_config(
            model=model_name,
            max_tokens=2048,
            temperature=0.0,
            tags=["langsmith:nostream"],
        )

        prompt = LEVEL3_EVAL_PROMPT.format(
            report=report[:15000],
            research_brief=research_brief[:2000],
        )

        response = await configurable_model.with_config(model_config).ainvoke([
            SystemMessage(content="You are an expert research evaluator performing deep quality assessment."),
            HumanMessage(content=prompt),
        ])

        content = response.content if hasattr(response, "content") else str(response)
        data = _parse_json_response(content)

        dimensions = []
        for d in data.get("dimensions", []):
            name = d.get("name", "unknown")
            dimensions.append(EvalDimension(
                name=name,
                score=float(d.get("score", 3)),
                weight=weights.get(name, 0.25),
                justification=(
                    f"Evidence: {d.get('evidence', '')}\n"
                    f"Recommendation: {d.get('recommendation', '')}"
                ),
                passed=float(d.get("score", 3)) >= 3,
            ))

        total_weight = sum(d.weight for d in dimensions)
        weighted_score = sum(d.score / 5.0 * d.weight for d in dimensions) / total_weight if total_weight > 0 else 0

        # Check for degradation
        degradation_detected = data.get("degradation_detected", False)
        if previous_scores and not degradation_detected:
            for dim_name, prev_score in previous_scores.items():
                current = next((d.score for d in dimensions if d.name == dim_name), None)
                if current is not None and current < prev_score - 1.0:
                    degradation_detected = True
                    break

        return EvalResult(
            overall_score=round(weighted_score, 3),
            overall_passed=weighted_score >= 0.6,
            dimensions=dimensions,
            summary=str(data.get("overall_analysis", "")),
            metadata={
                "degradation_detected": degradation_detected,
                "degradation_details": data.get("degradation_details", ""),
                "level": 3,
            },
        )

    except Exception as e:
        logger.error(f"[Eval] Level 3 evaluation failed: {e}")
        return EvalResult(
            overall_score=0.0,
            overall_passed=False,
            errors=[str(e)],
        )


# =============================================================================
# Evaluation Runner
# =============================================================================

def format_eval_result(result: EvalResult, level: int = 2) -> str:
    """Format evaluation results for display.

    Format deterministic evaluation output for logs and CLI display.
    """
    status = "PASSED" if result.overall_passed else "FAILED"

    lines = [
        f"\n{'='*60}",
        f"EVALUATION RESULTS (Level {level})",
        f"{'='*60}",
        f"Overall Score: {result.overall_score:.2f} — [{status}]",
        "",
    ]

    if result.dimensions:
        lines.append("Dimensions:")
        for dim in result.dimensions:
            status_mark = "✓" if dim.passed else "✗"
            lines.append(
                f"  {status_mark} {dim.name}: {dim.score:.2f} "
                f"(weight={dim.weight})"
            )
            if dim.justification:
                lines.append(f"      {dim.justification[:150]}")

    if result.summary:
        lines.append(f"\nSummary: {result.summary}")

    if result.errors:
        lines.append(f"\nErrors: {', '.join(result.errors)}")

    if result.metadata.get("degradation_detected"):
        lines.append(
            f"\n⚠️ DEGRADATION DETECTED: {result.metadata.get('degradation_details', '')}"
        )

    lines.append(f"{'='*60}\n")
    return "\n".join(lines)


def _parse_json_response(content: str) -> dict[str, Any]:
    """Parse JSON from LLM response, handling markdown wrapping."""
    try:
        json_match = re.search(r"\{[\s\S]*\}", content)
        if json_match:
            return json.loads(json_match.group(0))
    except (json.JSONDecodeError, ValueError):
        pass
    return {}


# =============================================================================
# Vertical Industry Research Evaluation
# =============================================================================

def _clamp_score(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 3)


def _metadata(item: dict[str, Any]) -> dict[str, Any]:
    meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return meta


def _dimension(
    name: str,
    score: float,
    weight: float,
    justification: str,
    threshold: float = 0.65,
) -> EvalDimension:
    score = _clamp_score(score)
    return EvalDimension(
        name=name,
        score=score,
        weight=weight,
        justification=justification,
        passed=score >= threshold,
    )


def run_vertical_evaluation(
    *,
    report: str,
    research_tasks: list[dict[str, Any]] | None = None,
    evidence_items: list[dict[str, Any]] | None = None,
    datapoints: list[dict[str, Any]] | None = None,
    claim_checks: list[dict[str, Any]] | None = None,
    critic_feedback: list[dict[str, Any]] | None = None,
) -> EvalResult:
    """Evaluate the fixed-role industry research mainline deterministically.

    The rubric is vertical-specific: it emphasizes section coverage, authority,
    freshness, datapoint completeness, claim support, citation traceability,
    risk analysis, and actionability. Each low-scoring dimension is attributed
    to a responsible role so QualityGate can route follow-up work precisely.
    """
    tasks = [t for t in (research_tasks or []) if isinstance(t, dict)]
    evidence = [e for e in (evidence_items or []) if isinstance(e, dict)]
    points = [d for d in (datapoints or []) if isinstance(d, dict)]
    checks = [c for c in (claim_checks or []) if isinstance(c, dict)]
    feedback = [f for f in (critic_feedback or []) if isinstance(f, dict)]
    report_text = str(report or "")

    section_ids = [str(t.get("section_id") or "") for t in tasks if t.get("section_id")]
    covered_sections = [sid for sid in section_ids if sid and any(str(t.get("title") or sid) in report_text for t in tasks if t.get("section_id") == sid)]
    section_coverage = len(covered_sections) / len(section_ids) if section_ids else 1.0

    authority_values = [float(_metadata(e).get("authority_score") or e.get("authority_score") or 0.0) for e in evidence]
    authoritative = [v for v in authority_values if v >= 0.70]
    authority_ratio = len(authoritative) / len(evidence) if evidence else 0.0

    freshness_values = [float(_metadata(e).get("freshness_score") or e.get("freshness_score") or 0.0) for e in evidence]
    freshness_score = sum(freshness_values) / len(freshness_values) if freshness_values else 0.0

    data_sections = [str(t.get("section_id") or "") for t in tasks if t.get("requires_data")]
    point_sections = {str(d.get("section_id") or "") for d in points}
    data_complete = len([sid for sid in data_sections if sid in point_sections]) / len(data_sections) if data_sections else 1.0

    supported = [c for c in checks if str(c.get("status") or "").lower() in {"verified", "supported"}]
    claim_support = len(supported) / len(checks) if checks else 0.0

    citation_markers = set(int(x) for x in re.findall(r"\[(\d+)\]", report_text))
    traceable_citations = min(len(citation_markers), len(evidence))
    citation_traceability = traceable_citations / len(citation_markers) if citation_markers else (1.0 if not evidence else 0.0)

    risk_terms = ["风险", "政策", "竞争", "供给", "技术", "商业化", "触发", "监控"]
    risk_quality = min(1.0, sum(1 for term in risk_terms if term in report_text) / 5.0)

    action_terms = ["建议", "跟踪", "监控", "触发", "判断", "行动", "指标"]
    actionability = min(1.0, sum(1 for term in action_terms if term in report_text) / 4.0)

    dimensions = [
        _dimension("section_coverage", section_coverage, 1.3, f"covered {len(covered_sections)}/{len(section_ids) or 1} planned sections"),
        _dimension("authoritative_source_ratio", authority_ratio, 1.3, f"{len(authoritative)}/{len(evidence)} sources have authority_score >= 0.70"),
        _dimension("source_freshness", freshness_score, 1.0, "average freshness_score across ledger evidence"),
        _dimension("datapoint_completeness", data_complete, 1.2, f"covered {len(point_sections & set(data_sections))}/{len(data_sections) or 1} data-required sections"),
        _dimension("claim_support_rate", claim_support, 1.4, f"{len(supported)}/{len(checks)} checked claims are supported"),
        _dimension("citation_traceability", citation_traceability, 1.4, f"{traceable_citations}/{len(citation_markers) or 1} citation markers map to ledger evidence"),
        _dimension("risk_analysis_quality", risk_quality, 0.9, "risk section contains vertical risk dimensions and triggers"),
        _dimension("conclusion_actionability", actionability, 0.8, "conclusion contains monitorable actions or decision triggers"),
    ]

    responsible_agents: dict[str, list[str]] = {}
    agent_map = {
        "section_coverage": "ResearchArchitect",
        "authoritative_source_ratio": "SourceScout",
        "source_freshness": "SourceScout",
        "datapoint_completeness": "DataAnalyst",
        "claim_support_rate": "ClaimVerifier",
        "citation_traceability": "LeadWriter",
        "risk_analysis_quality": "CriticReviewer",
        "conclusion_actionability": "LeadWriter",
    }
    for dim in dimensions:
        if not dim.passed:
            responsible_agents.setdefault(agent_map.get(dim.name, "QualityGate"), []).append(dim.name)
    for item in feedback:
        agent = str(item.get("responsible_agent") or "CriticReviewer")
        issue = str(item.get("issue") or "critic_feedback")
        responsible_agents.setdefault(agent, []).append(issue)

    total_weight = sum(d.weight for d in dimensions)
    weighted = sum(d.score * d.weight for d in dimensions) / total_weight if total_weight else 0.0
    passed = all(d.passed for d in dimensions if d.weight >= 1.2) and not feedback

    return EvalResult(
        overall_score=round(weighted, 3),
        overall_passed=passed,
        dimensions=dimensions,
        summary="Vertical industry research quality gate passed." if passed else "Vertical industry research requires targeted follow-up.",
        metadata={
            "responsible_agents": responsible_agents,
            "rubric": "industry_market_policy_research",
            "feedback_count": len(feedback),
            "evidence_count": len(evidence),
            "datapoint_count": len(points),
            "claim_check_count": len(checks),
        },
    )
