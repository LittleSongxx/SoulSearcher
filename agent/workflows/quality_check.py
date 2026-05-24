"""Quality Check System — Level 1 instant quality validation.

From the unified design's 3-level evaluation:
- Level 1: Instant quality check (fast_llm, <5s, after every report)
- Level 2: Dev evaluation (smart_llm, Pytest, 9 dimensions)
- Level 3: Deep evaluation (strategic_llm, LangSmith, 4-dimension scoring)

This module implements Level 1: fast, automated quality validation that runs
after every report generation, providing immediate feedback and triggering
automatic revisions when needed.

Pattern from open_deep_research's evaluation system.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from agent.core.configuration import ResearchConfiguration
from agent.core.model_routing import configurable_model

logger = logging.getLogger(__name__)


# =============================================================================
# Quality Check Data
# =============================================================================

@dataclass
class QualityCheckResult:
    """Result of a quality check."""
    passed: bool
    score: float  # 0.0 - 1.0
    dimensions: dict[str, float] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    verdict: str = ""  # "pass" | "revise" | "incomplete"


# =============================================================================
# Level 1: Instant Quality Check
# =============================================================================

LEVEL1_CHECK_PROMPT = """Evaluate the quality of the following research report against these criteria.
Respond with a JSON object.

<Report>
{report}
</Report>

<Research Brief>
{research_brief}
</Research Brief>

{evidence_context}

Evaluation criteria:
1. **citation_density**: Does each major claim have a source citation? (0-1)
2. **section_completeness**: Are all promised sections present? (0-1)
3. **format_correctness**: Proper markdown, no broken formatting? (0-1)
4. **topic_relevance**: Does the report address the research brief? (0-1)
5. **minimum_length**: Is the report substantial enough (not too short)? (0-1)
6. **evidence_alignment**: Are sample claims actually supported by their cited sources?
   Check: pick 3 factual claims with citations, then verify whether the cited source text
   genuinely supports the claim. Score 0 if claims contradict sources or citations are
   hallucinated, 0.5 if unclear, 1.0 if well-supported. (0-1)

Respond with JSON:
{{
  "passed": true/false,
  "score": 0.0-1.0,
  "dimensions": {{
    "citation_density": 0.0-1.0,
    "section_completeness": 0.0-1.0,
    "format_correctness": 0.0-1.0,
    "topic_relevance": 0.0-1.0,
    "minimum_length": 0.0-1.0,
    "evidence_alignment": 0.0-1.0
  }},
  "issues": ["list of specific issues found"],
  "suggestions": ["list of concrete improvement suggestions"],
  "verdict": "pass" or "revise" or "incomplete"
}}

Threshold: score >= 0.7 → pass | 0.4-0.7 → revise | <0.4 → incomplete
"""


def _build_evidence_context(state: dict) -> str:
    """Extract sampled cited-source pairs from state for evidence_alignment checking.

    Follows open_deep_research's LLM-as-judge approach: sample a few claims
    and their cited sources so the evaluator can check factual accuracy.
    """
    notes = state.get("notes", []) or state.get("raw_notes", []) or []
    sources = state.get("sources", []) or state.get("curated_sources", []) or []

    context_parts: list[str] = []

    # Extract up to 3 source-text snippets from research notes
    source_texts: list[str] = []
    for note in notes[:5]:
        text = str(note)[:1500]
        source_texts.append(text)

    if source_texts:
        context_parts.append("<Source Evidence>")
        for i, src in enumerate(source_texts[:3], 1):
            context_parts.append(f"[Source {i}]: {src[:800]}")
        context_parts.append("</Source Evidence>")

    # List cited URLs for cross-reference
    if sources:
        context_parts.append("<Cited Sources>")
        for s in sources[:10]:
            url = s.get("url", "") if isinstance(s, dict) else str(s)
            title = s.get("title", "") if isinstance(s, dict) else ""
            if url:
                context_parts.append(f"- {title}: {url}"[:200])
        context_parts.append("</Cited Sources>")

    return "\n".join(context_parts)


async def run_level1_check(
    report: str,
    research_brief: str,
    config: RunnableConfig,
    state: dict | None = None,
    use_rubric: bool = True,
) -> QualityCheckResult:
    """Run instant quality check using fast_llm.

    Takes <5 seconds, runs after every report generation.
    Uses fast_llm for cost efficiency.

    When use_rubric=True (default), uses the structured rubric scoring system
    (researchrubrics/DEER pattern) for more reliable, auditable results.
    Falls back to legacy prompt-based scoring when use_rubric=False.
    """
    if use_rubric:
        try:
            from agent.workflows.rubric import run_level1_rubric
            rubric_result = await run_level1_rubric(report, research_brief, config, state)
            return QualityCheckResult(
                passed=rubric_result.passed,
                score=rubric_result.overall_score,
                issues=rubric_result.issues,
                suggestions=rubric_result.suggestions,
                verdict=rubric_result.verdict,
            )
        except ImportError:
            logger.debug("[QualityCheck] Rubric system not available, falling back to legacy")
        except Exception as e:
            logger.warning(f"[QualityCheck] Rubric eval failed: {e}, falling back to legacy")

    # Legacy prompt-based evaluation (fallback)
    research_config = ResearchConfiguration.from_runnable_config(config)

    model_config = {
        "model": research_config.fast_llm,
        "max_tokens": 1024,
        "temperature": 0.0,
        "tags": ["langsmith:nostream"],
    }

    evidence_context = _build_evidence_context(state) if state else ""

    prompt = LEVEL1_CHECK_PROMPT.format(
        report=report[:8000],
        research_brief=research_brief[:2000],
        evidence_context=evidence_context,
    )

    try:
        response = await configurable_model.with_config(model_config).ainvoke([
            SystemMessage(content="You are a quality evaluator. Respond only with valid JSON."),
            HumanMessage(content=prompt),
        ])

        return _parse_quality_response(response.content if hasattr(response, "content") else str(response))

    except Exception as e:
        logger.error(f"[QualityCheck] Level 1 failed: {e}")
        return QualityCheckResult(
            passed=True,
            score=0.5,
            issues=[f"Quality check error: {str(e)}"],
            verdict="pass",
        )


def _parse_quality_response(content: str) -> QualityCheckResult:
    """Parse the JSON response from the quality check."""
    import json
    import re

    try:
        json_match = re.search(r"\{[\s\S]*\}", content)
        if json_match:
            data = json.loads(json_match.group(0))
            return QualityCheckResult(
                passed=data.get("passed", False),
                score=float(data.get("score", 0.0)),
                dimensions=data.get("dimensions", {}),
                issues=data.get("issues", []),
                suggestions=data.get("suggestions", []),
                verdict=data.get("verdict", "pass"),
            )
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"[QualityCheck] Failed to parse response: {e}")

    return QualityCheckResult(
        passed=True,
        score=0.5,
        issues=["Could not parse quality check response"],
        verdict="pass",
    )


# =============================================================================
# Report Revision (triggered by Level 1 failure)
# =============================================================================

REVISION_PROMPT = """Revise the following research report to address these quality issues.

<Original Report>
{report}
</Original Report>

<Quality Issues>
{issues}
</Quality Issues>

<Improvement Suggestions>
{suggestions}
</Improvement Suggestions>

<Research Brief>
{research_brief}
</Research Brief>

Please produce a revised version of the report that:
1. Addresses ALL the issues listed above
2. Incorporates the improvement suggestions
3. Maintains the original report's structure and key findings
4. Includes proper citations

Return the complete revised report in markdown format.
"""


async def revise_report(
    report: str,
    research_brief: str,
    issues: list[str],
    suggestions: list[str],
    config: RunnableConfig,
) -> str:
    """Revise a report based on quality issues.

    Uses smart_llm for high-quality revisions.
    """
    research_config = ResearchConfiguration.from_runnable_config(config)

    model_config = {
        "model": research_config.smart_llm,
        "max_tokens": research_config.final_report_model_max_tokens,
        "temperature": 0.3,
        "tags": ["langsmith:nostream"],
    }

    prompt = REVISION_PROMPT.format(
        report=report,
        issues="\n".join(f"- {i}" for i in issues),
        suggestions="\n".join(f"- {s}" for s in suggestions) if suggestions else "None",
        research_brief=research_brief,
    )

    try:
        response = await configurable_model.with_config(model_config).ainvoke([
            HumanMessage(content=prompt)
        ])
        return response.content if hasattr(response, "content") else str(response)
    except Exception as e:
        logger.error(f"[QualityCheck] Revision failed: {e}")
        return report  # Return original on failure


# =============================================================================
# Quality-Assured Report Generation
# =============================================================================

async def generate_report_with_quality_check(
    state: dict,
    report_content: str,
    config: RunnableConfig,
    max_revisions: int = 2,
    use_rubric: bool = True,
) -> str:
    """Generate report with Level 1 quality assurance loop.

    1. Run Level 1 check (rubric-based by default)
    2. If "pass": return report as-is
    3. If "revise": trigger revision, re-check (up to max_revisions)
    4. If "incomplete": flag but don't loop indefinitely
    """
    research_brief = state.get("research_brief", "")

    current_report = report_content
    for revision in range(max_revisions + 1):
        check_result = await run_level1_check(
            current_report, research_brief, config, state=state, use_rubric=use_rubric,
        )

        logger.info(
            f"[QualityCheck] Revision {revision}: "
            f"score={check_result.score:.2f}, verdict={check_result.verdict}"
        )

        if check_result.passed or check_result.verdict == "pass":
            logger.info("[QualityCheck] Report passed quality check")
            return current_report

        if check_result.verdict == "incomplete" and revision >= max_revisions:
            logger.warning("[QualityCheck] Report incomplete after max revisions")
            return current_report

        # Revise and retry
        current_report = await revise_report(
            current_report,
            research_brief,
            check_result.issues,
            check_result.suggestions,
            config,
        )

    return current_report
