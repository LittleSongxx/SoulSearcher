"""Evaluation System — Levels 2 & 3 from the unified design.

From the unified design's 3-level evaluation:
- Level 1: Instant quality check (fast_llm) — in quality_check.py
- Level 2: Dev evaluation (smart_llm, Pytest framework, 9 dimensions, binary pass/fail)
- Level 3: Deep evaluation (strategic_llm, LangSmith, 4-dimension weighted scoring)

This module implements Levels 2 and 3.

Pattern from open_deep_research: evaluation system with rich console output.
Enhanced with deer-flow's structured dimension scoring.
"""

from __future__ import annotations

import json
import logging
import re
import time
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
# Level 2: 9-Dimension Dev Evaluation (from open_deep_research)
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
    Pattern from open_deep_research: pytest evaluation with 9 criteria.

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
    Pattern from open_deep_research: LangSmith dataset evaluation.

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
            score_5 = float(d.get("score", 3)) / 5.0  # Normalize to 0-1
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

    Pattern from open_deep_research: rich console output.
    """
    status = "PASSED" if result.overall_passed else "FAILED"
    color = "green" if result.overall_passed else "red"

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
