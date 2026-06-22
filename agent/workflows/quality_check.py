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

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from agent.core.configuration import ResearchConfiguration
from agent.core.model_routing import build_model_config, configurable_model
from agent.workflows.evidence_ledger import evaluate_citation_gate

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
    metadata: dict[str, Any] = field(default_factory=dict)
    gates: list[dict[str, Any]] = field(default_factory=list)


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


def _normalize_report_text(report: str) -> str:
    content = str(report or "")
    if "<" in content and ">" in content:
        stripped = re.sub(r"<[^>]+>", " ", content)
        stripped = re.sub(r"\s+", " ", stripped).strip()
        if stripped:
            return stripped
    return content


def _coerce_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = 0.0
    return max(0.0, min(1.0, score))


def _unique_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _rubric_to_dict(result: Any) -> dict[str, Any]:
    if result is None:
        return {}
    if hasattr(result, "model_dump"):
        return result.model_dump()
    dimensions = []
    for dimension in getattr(result, "dimensions", []) or []:
        items = []
        for item in getattr(dimension, "items", []) or []:
            items.append(
                {
                    "id": getattr(item, "id", ""),
                    "score": getattr(item, "score", 0.0),
                    "weight": getattr(item, "weight", 1.0),
                    "evidence": getattr(item, "evidence", ""),
                }
            )
        dimensions.append(
            {
                "name": getattr(dimension, "name", ""),
                "score": getattr(dimension, "score", 0.0),
                "weight": getattr(dimension, "weight", 1.0),
                "items": items,
            }
        )
    return {
        "overall_score": getattr(result, "overall_score", 0.0),
        "passed": getattr(result, "passed", False),
        "verdict": getattr(result, "verdict", "incomplete"),
        "issues": list(getattr(result, "issues", []) or []),
        "suggestions": list(getattr(result, "suggestions", []) or []),
        "summary": getattr(result, "summary", ""),
        "metadata": dict(getattr(result, "metadata", {}) or {}),
        "dimensions": dimensions,
    }


def _build_gate(
    name: str,
    level: int,
    passed: bool,
    score: float,
    verdict: str,
    threshold: float,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "level": level,
        "passed": bool(passed),
        "score": round(_coerce_score(score), 4),
        "verdict": verdict,
        "threshold": round(_coerce_score(threshold), 4),
        "details": details or {},
    }


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
    Falls back to prompt-based scoring when use_rubric=False.
    """
    research_config = ResearchConfiguration.from_runnable_config(config)
    report_text = _normalize_report_text(report)

    if use_rubric:
        try:
            from agent.workflows.rubric import (
                extract_evidence_items,
                extract_source_texts,
                run_full_claim_alignment,
                run_level1_rubric,
                run_level2_rubric,
            )

            rubric_result = await run_level1_rubric(
                report_text, research_brief, config, state
            )
            l2_result = await run_level2_rubric(
                report=report_text,
                research_brief=research_brief,
                topic=research_brief[:500] if research_brief else "",
                config=config,
            )

            source_texts = extract_source_texts(state)
            evidence_items = extract_evidence_items(state)
            has_citations = bool(re.search(r"\[(\d+(?:,\s*\d+)*)\]", report_text))
            artifacts = state.get("deepsearch_artifacts", {}) if isinstance(state, dict) else {}
            artifacts = artifacts if isinstance(artifacts, dict) else {}
            citation_gate = evaluate_citation_gate(
                report_text,
                sources=(
                    artifacts.get("sources")
                    if isinstance(artifacts.get("sources"), list)
                    else (state.get("sources") if isinstance(state, dict) else [])
                ),
                evidence_items=evidence_items,
                passages=(
                    artifacts.get("passages")
                    if isinstance(artifacts.get("passages"), list)
                    else []
                ),
                require_evidence=research_config.evaluation_require_citation_evidence,
            )
            citation_bindings = citation_gate.get("citation_bindings", []) or []
            alignment_result: dict[str, Any]
            alignment_rate = 1.0
            alignment_passed = True
            alignment_verdict = "pass"
            alignment_issues: list[str] = []
            alignment_suggestions: list[str] = []

            if has_citations and citation_bindings:
                alignment_result = await run_full_claim_alignment(
                    report_text,
                    source_texts,
                    config,
                    citation_bindings=citation_bindings,
                )
                alignment_rate = _coerce_score(
                    alignment_result.get("alignment_rate", 0.0)
                )
                alignment_passed = alignment_rate >= research_config.evaluation_claim_alignment_min_rate
                if alignment_passed:
                    alignment_verdict = "pass"
                elif alignment_rate >= research_config.evaluation_revise_threshold:
                    alignment_verdict = "revise"
                else:
                    alignment_verdict = "incomplete"
                failed_claims = [
                    claim
                    for claim in alignment_result.get("claims", []) or []
                    if isinstance(claim, dict)
                    and _coerce_score(claim.get("score", 0.0)) < 0.5
                ]
                alignment_issues.extend(
                    [
                        f"Unsupported claim [{claim.get('citation_marker', '?')}]: {str(claim.get('claim_summary', ''))[:180]}"
                        for claim in failed_claims[:5]
                    ]
                )
                if failed_claims:
                    alignment_suggestions.append(
                        "Revise unsupported claims or replace them with evidence-backed citations."
                    )
            elif has_citations and research_config.evaluation_require_citation_evidence and not source_texts.strip():
                alignment_result = {
                    "alignment_rate": 0.0,
                    "total_claims": 0,
                    "aligned": 0,
                    "claims": [],
                    "error": "missing_source_evidence",
                }
                alignment_rate = 0.0
                alignment_passed = False
                alignment_verdict = "incomplete"
                alignment_issues.append(
                    "Citation evidence could not be reconstructed from research artifacts."
                )
                alignment_suggestions.append(
                    "Persist source passages or evidence items before final evaluation."
                )
            elif has_citations and source_texts.strip():
                alignment_result = await run_full_claim_alignment(
                    report_text, source_texts, config
                )
                alignment_rate = _coerce_score(
                    alignment_result.get("alignment_rate", 0.0)
                )
                alignment_passed = alignment_rate >= research_config.evaluation_claim_alignment_min_rate
                if alignment_passed:
                    alignment_verdict = "pass"
                elif alignment_rate >= research_config.evaluation_revise_threshold:
                    alignment_verdict = "revise"
                else:
                    alignment_verdict = "incomplete"
                failed_claims = [
                    claim
                    for claim in alignment_result.get("claims", []) or []
                    if isinstance(claim, dict)
                    and _coerce_score(claim.get("score", 0.0)) < 0.5
                ]
                alignment_issues.extend(
                    [
                        f"Unsupported claim [{claim.get('citation_marker', '?')}]: {str(claim.get('claim_summary', ''))[:180]}"
                        for claim in failed_claims[:5]
                    ]
                )
                if failed_claims:
                    alignment_suggestions.append(
                        "Revise unsupported claims or replace them with evidence-backed citations."
                    )
            else:
                alignment_result = {
                    "alignment_rate": 1.0,
                    "total_claims": 0,
                    "aligned": 0,
                    "claims": [],
                }

            gates = [
                _build_gate(
                    "level1_rubric",
                    1,
                    rubric_result.passed,
                    rubric_result.overall_score,
                    rubric_result.verdict,
                    research_config.evaluation_pass_threshold,
                    {"dimensions": _rubric_to_dict(rubric_result).get("dimensions", [])},
                ),
                _build_gate(
                    "citation_gate",
                    2,
                    bool(citation_gate.get("passed")),
                    float(citation_gate.get("score", 0.0) or 0.0),
                    str(citation_gate.get("verdict") or "incomplete"),
                    1.0,
                    {
                        "markers": citation_gate.get("markers", []),
                        "missing_markers": citation_gate.get("missing_markers", []),
                        "source_count": citation_gate.get("source_count", 0),
                        "evidence_count": citation_gate.get("evidence_count", 0),
                        "passage_count": citation_gate.get("passage_count", 0),
                    },
                ),
                _build_gate(
                    "claim_alignment",
                    2,
                    alignment_passed,
                    alignment_rate,
                    alignment_verdict,
                    research_config.evaluation_claim_alignment_min_rate,
                    {
                        "total_claims": alignment_result.get("total_claims", 0),
                        "aligned": alignment_result.get("aligned", 0),
                        "error": alignment_result.get("error", ""),
                    },
                ),
                _build_gate(
                    "level2_rubric",
                    2,
                    l2_result.passed,
                    l2_result.overall_score,
                    l2_result.verdict,
                    research_config.evaluation_pass_threshold,
                    {"dimensions": _rubric_to_dict(l2_result).get("dimensions", [])},
                ),
            ]

            score_terms = [
                (rubric_result.overall_score, 0.45),
                (l2_result.overall_score, 0.35),
                (alignment_rate, 0.20),
            ]
            weighted_score = sum(score * weight for score, weight in score_terms) / sum(
                weight for _score, weight in score_terms
            )

            passed = rubric_result.passed and alignment_passed and bool(citation_gate.get("passed"))
            if research_config.evaluation_require_l2_pass:
                passed = passed and l2_result.passed

            if passed:
                verdict = "pass"
            elif any(gate.get("verdict") == "incomplete" for gate in gates):
                verdict = "incomplete"
            elif weighted_score >= research_config.evaluation_revise_threshold:
                verdict = "revise"
            else:
                verdict = "incomplete"

            issues = _unique_strings(
                list(rubric_result.issues)
                + list(l2_result.issues)
                + alignment_issues
                + list(citation_gate.get("issues", []) or [])
            )
            suggestions = _unique_strings(
                list(rubric_result.suggestions)
                + list(l2_result.suggestions)
                + alignment_suggestions
                + list(citation_gate.get("suggestions", []) or [])
            )

            return QualityCheckResult(
                passed=passed,
                score=round(weighted_score, 4),
                dimensions={
                    **{
                        dimension.name: round(dimension.score, 4)
                        for dimension in rubric_result.dimensions
                    },
                    "claim_alignment": round(alignment_rate, 4),
                    "level2_overall": round(l2_result.overall_score, 4),
                },
                issues=issues,
                suggestions=suggestions,
                verdict=verdict,
                metadata={
                    "level1_rubric": _rubric_to_dict(rubric_result),
                    "level2_rubric": _rubric_to_dict(l2_result),
                    "citation_gate": citation_gate,
                    "claim_alignment": alignment_result,
                    "citation_bindings": citation_bindings,
                    "evidence_items": evidence_items,
                    "source_texts": source_texts,
                },
                gates=gates,
            )
        except ImportError:
            logger.debug("[QualityCheck] Rubric system not available, using prompt fallback")
        except Exception as e:
            logger.warning(f"[QualityCheck] Rubric eval failed: {e}, using prompt fallback")

    # Prompt-based evaluation fallback
    model_config = build_model_config(
        model=research_config.fast_llm,
        max_tokens=1024,
        temperature=0.0,
        tags=["langsmith:nostream"],
    )

    evidence_context = _build_evidence_context(state) if state else ""

    prompt = LEVEL1_CHECK_PROMPT.format(
        report=report_text[:8000],
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
            passed=False,
            score=0.0,
            issues=[f"Quality check error: {str(e)}"],
            suggestions=["Retry the quality evaluation run."],
            verdict="incomplete",
            metadata={"error": str(e)},
        )


def _parse_quality_response(content: str) -> QualityCheckResult:
    """Parse the JSON response from the quality check."""
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
        passed=False,
        score=0.0,
        issues=["Could not parse quality check response"],
        suggestions=["Retry the quality evaluation run."],
        verdict="incomplete",
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

<Output Format>
{report_format}
</Output Format>

Please produce a revised version of the report that:
1. Addresses ALL the issues listed above
2. Incorporates the improvement suggestions
3. Maintains the original report's structure and key findings
4. Includes proper citations

Return the complete revised report in the requested format.
"""


async def revise_report(
    report: str,
    research_brief: str,
    issues: list[str],
    suggestions: list[str],
    config: RunnableConfig,
    report_format: str = "markdown",
) -> str:
    """Revise a report based on quality issues.

    Uses smart_llm for high-quality revisions.
    """
    research_config = ResearchConfiguration.from_runnable_config(config)

    model_config = build_model_config(
        model=research_config.smart_llm,
        max_tokens=research_config.final_report_model_max_tokens,
        temperature=0.3,
        tags=["langsmith:nostream"],
    )

    prompt = REVISION_PROMPT.format(
        report=report,
        issues="\n".join(f"- {i}" for i in issues),
        suggestions="\n".join(f"- {s}" for s in suggestions) if suggestions else "None",
        research_brief=research_brief,
        report_format=report_format,
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
) -> tuple[str, QualityCheckResult]:
    """Generate report with Level 1 quality assurance loop.

    1. Run Level 1 check (rubric-based by default)
    2. If "pass": return report as-is
    3. If "revise": trigger revision, re-check (up to max_revisions)
    4. If "incomplete": flag but don't loop indefinitely
    """
    research_brief = state.get("research_brief", "")
    report_format = state.get("report_format", "markdown")

    current_report = report_content
    final_result = QualityCheckResult(
        passed=False,
        score=0.0,
        verdict="incomplete",
    )
    for revision in range(max_revisions + 1):
        check_result = await run_level1_check(
            current_report, research_brief, config, state=state, use_rubric=use_rubric,
        )
        final_result = check_result

        logger.info(
            f"[QualityCheck] Revision {revision}: "
            f"score={check_result.score:.2f}, verdict={check_result.verdict}"
        )

        if check_result.passed or check_result.verdict == "pass":
            logger.info("[QualityCheck] Report passed quality check")
            return current_report, check_result

        if check_result.verdict == "incomplete" and revision >= max_revisions:
            logger.warning("[QualityCheck] Report incomplete after max revisions")
            return current_report, check_result

        if revision >= max_revisions:
            logger.warning("[QualityCheck] Report did not pass after max revisions")
            return current_report, check_result

        # Revise and retry
        current_report = await revise_report(
            current_report,
            research_brief,
            check_result.issues,
            check_result.suggestions,
            config,
            report_format=report_format,
        )

    return current_report, final_result
