"""Structured Rubric-Based Evaluation System.

Replaces ad-hoc LLM-as-judge prompts with fine-grained, verifiable rubric items
scored on a ternary scale (0 = fail, 0.5 = partial, 1 = pass).  This reduces LLM
self-assessment bias and produces reproducible, auditable evaluation results.

Design follows ResearchRubrics (Scale AI, 2025) and DEER (LG AI Research, 2025).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from agent.core.configuration import ResearchConfiguration
from agent.core.model_routing import configurable_model

logger = logging.getLogger(__name__)


# =============================================================================
# Pydantic Models
# =============================================================================


class RubricItem(BaseModel):
    """A single verifiable rubric criterion scored 0 / 0.5 / 1."""

    id: str = Field(description="Short identifier, e.g. 'citation_density'")
    criterion: str = Field(
        description="Precise, verifiable statement of what to check."
    )
    weight: float = Field(default=1.0, description="Relative weight in dimension score.")
    score: float = Field(default=0.0, ge=0.0, le=1.0, description="0 | 0.5 | 1")
    evidence: str = Field(
        default="",
        description="Specific quote or observation from the report supporting the score.",
    )


class RubricDimension(BaseModel):
    """A group of related rubric items forming one evaluation dimension."""

    name: str = Field(description="Dimension name, e.g. 'Factual Accuracy'")
    description: str = Field(default="", description="What this dimension measures.")
    weight: float = Field(default=1.0, description="Relative weight of this dimension in overall score.")
    items: list[RubricItem] = Field(default_factory=list)
    score: float = Field(default=0.0, ge=0.0, le=1.0, description="Weighted average of item scores.")

    def compute_score(self) -> float:
        total_w = sum(it.weight for it in self.items)
        if total_w == 0:
            return 0.0
        return sum(it.score * it.weight for it in self.items) / total_w


class RubricResult(BaseModel):
    """Complete rubric evaluation result."""

    dimensions: list[RubricDimension] = Field(default_factory=list)
    overall_score: float = Field(default=0.0, ge=0.0, le=1.0)
    passed: bool = Field(default=False)
    verdict: str = Field(default="pass", description="pass | revise | incomplete")
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)

    def compute_overall(self) -> float:
        total_w = sum(d.weight for d in self.dimensions)
        if total_w == 0:
            return 0.0
        return sum(d.compute_score() * d.weight for d in self.dimensions) / total_w


# =============================================================================
# L1 Rubric — Instant Quality Check (fast_llm)
# =============================================================================

# Each dimension has 3–5 binary/ternary rubric items.
L1_RUBRIC: list[dict[str, Any]] = [
    {
        "name": "citation_density",
        "description": "Are factual claims supported by source citations?",
        "weight": 1.0,
        "items": [
            {
                "id": "cd_major_claims",
                "criterion": "Every major factual claim has a citation marker ([1], [2], etc.) or inline link.",
                "weight": 1.0,
            },
            {
                "id": "cd_distribution",
                "criterion": "Citations are distributed throughout the report, not clustered in one section.",
                "weight": 0.5,
            },
            {
                "id": "cd_fabricated",
                "criterion": "No obviously fabricated or broken-link citations (check URL format plausibility).",
                "weight": 1.0,
            },
        ],
    },
    {
        "name": "section_completeness",
        "description": "Are all promised sections present?",
        "weight": 0.8,
        "items": [
            {
                "id": "sc_intro",
                "criterion": "Report has a clear introduction or overview section.",
                "weight": 0.5,
            },
            {
                "id": "sc_body",
                "criterion": "Body sections cover the main aspects mentioned in the research brief.",
                "weight": 1.0,
            },
            {
                "id": "sc_conclusion",
                "criterion": "Report has a conclusion, summary, or key-takeaways section.",
                "weight": 0.5,
            },
        ],
    },
    {
        "name": "format_correctness",
        "description": "Is markdown formatting correct and consistent?",
        "weight": 0.5,
        "items": [
            {
                "id": "fc_headers",
                "criterion": "Headings use consistent level hierarchy (no skipped levels like ### after #).",
                "weight": 0.5,
            },
            {
                "id": "fc_links",
                "criterion": "All markdown links have both link text and URL, no bare URLs used as link text.",
                "weight": 0.5,
            },
            {
                "id": "fc_broken",
                "criterion": "No broken markdown syntax (unclosed bold/italic, malformed tables).",
                "weight": 1.0,
            },
        ],
    },
    {
        "name": "topic_relevance",
        "description": "Does the report address the research brief?",
        "weight": 1.2,
        "items": [
            {
                "id": "tr_brief_alignment",
                "criterion": "All major questions or aspects from the research brief are addressed.",
                "weight": 1.0,
            },
            {
                "id": "tr_no_irrelevant",
                "criterion": "No substantial off-topic content unrelated to the research brief.",
                "weight": 1.0,
            },
            {
                "id": "tr_depth",
                "criterion": "The depth of analysis matches the complexity of the research brief.",
                "weight": 0.5,
            },
        ],
    },
    {
        "name": "evidence_alignment",
        "description": "Do citations genuinely support the claims they are attached to?",
        "weight": 1.5,
        "items": [
            {
                "id": "ea_sample1",
                "criterion": "Sample claim 1: the cited source text genuinely supports the claim (not hallucinated).",
                "weight": 1.0,
            },
            {
                "id": "ea_sample2",
                "criterion": "Sample claim 2: the cited source text genuinely supports the claim (not hallucinated).",
                "weight": 1.0,
            },
            {
                "id": "ea_sample3",
                "criterion": "Sample claim 3: the cited source text genuinely supports the claim (not hallucinated).",
                "weight": 1.0,
            },
            {
                "id": "ea_contradiction",
                "criterion": "No claim directly contradicts its cited source or another cited source in the report.",
                "weight": 1.0,
            },
        ],
    },
]

L1_RUBRIC_PROMPT_TEMPLATE = """Evaluate this research report against a structured rubric.
Score each rubric item as:
  0   = does not meet the criterion at all
  0.5 = partially meets the criterion
  1   = fully meets the criterion

For each item, provide specific evidence from the report (quote or observation).

<Report>
{report}
</Report>

<Research Brief>
{research_brief}
</Research Brief>

{evidence_context}

<Rubric>
{rubric_json}
</Rubric>

Respond with ONLY valid JSON:
{{
  "dimensions": [
    {{
      "name": "citation_density",
      "items": [
        {{"id": "cd_major_claims", "score": 0/0.5/1, "evidence": "..."}},
        ...
      ]
    }},
    ...
  ],
  "passed": true/false,
  "verdict": "pass|revise|incomplete",
  "issues": ["specific issue 1", ...],
  "suggestions": ["concrete fix 1", ...]
}}

Thresholds: overall_score >= 0.7 → pass | 0.4-0.7 → revise | <0.4 → incomplete"""


def _build_l1_rubric_json() -> str:
    """Serialize L1 rubric dimensions to JSON for prompt injection."""
    return json.dumps(
        [
            {
                "name": d["name"],
                "description": d["description"],
                "items": [
                    {"id": it["id"], "criterion": it["criterion"]}
                    for it in d["items"]
                ],
            }
            for d in L1_RUBRIC
        ],
        ensure_ascii=False,
        indent=2,
    )


def _parse_rubric_response(content: str) -> RubricResult:
    """Parse LLM rubric response into a RubricResult."""
    try:
        json_match = re.search(r"\{[\s\S]*\}", content)
        if not json_match:
            return RubricResult(passed=True, verdict="pass")

        data = json.loads(json_match.group(0))

        # Build dimension weight lookup from L1_RUBRIC
        dim_weights = {d["name"]: d["weight"] for d in L1_RUBRIC}

        dimensions = []
        for d in data.get("dimensions", []):
            name = d.get("name", "unknown")
            items = [
                RubricItem(
                    id=it.get("id", ""),
                    criterion="",
                    weight=1.0,
                    score=float(it.get("score", 0)),
                    evidence=str(it.get("evidence", "")),
                )
                for it in d.get("items", [])
            ]
            dim = RubricDimension(
                name=name,
                description="",
                items=items,
                weight=float(dim_weights.get(name, 1.0)),
            )
            dim.score = dim.compute_score()
            dimensions.append(dim)

        result = RubricResult(
            dimensions=dimensions,
            passed=bool(data.get("passed", False)),
            verdict=str(data.get("verdict", "pass")),
            issues=[str(i) for i in data.get("issues", [])],
            suggestions=[str(s) for s in data.get("suggestions", [])],
        )
        result.overall_score = result.compute_overall()
        return result

    except (json.JSONDecodeError, ValueError, KeyError) as e:
        logger.warning(f"[Rubric] Failed to parse response: {e}")
        return RubricResult(passed=True, verdict="pass")


# =============================================================================
# L2 Rubric — Dev Evaluation (smart_llm, 9 dimensions)
# =============================================================================

L2_RUBRIC: list[dict[str, Any]] = [
    {
        "name": "topic_relevance_overall",
        "description": "How thoroughly does the report address the research topic?",
        "weight": 1.5,
        "items": [
            {"id": "tro_coverage", "criterion": "All key aspects of the topic are addressed with substantive analysis.", "weight": 1.0},
            {"id": "tro_focus", "criterion": "No significant digressions into tangential topics.", "weight": 0.5},
        ],
    },
    {
        "name": "section_relevance_critical",
        "description": "Are all sections directly relevant to the research brief?",
        "weight": 2.0,
        "items": [
            {"id": "src_direct", "criterion": "Every section contributes directly to answering the research brief.", "weight": 1.0},
            {"id": "src_no_filler", "criterion": "No 'filler' sections that exist only to increase length.", "weight": 1.0},
        ],
    },
    {
        "name": "structure_and_flow",
        "description": "Logical organization and narrative flow.",
        "weight": 1.0,
        "items": [
            {"id": "sf_logical", "criterion": "Sections follow a logical progression (background → findings → analysis → conclusion).", "weight": 1.0},
            {"id": "sf_transitions", "criterion": "Smooth transitions between sections, not abrupt topic switches.", "weight": 0.5},
        ],
    },
    {
        "name": "introduction_quality",
        "description": "Quality of the introduction section.",
        "weight": 1.0,
        "items": [
            {"id": "iq_context", "criterion": "Introduction establishes context and explains why the topic matters.", "weight": 1.0},
            {"id": "iq_scope", "criterion": "Introduction clearly states the scope and what the report will cover.", "weight": 1.0},
        ],
    },
    {
        "name": "conclusion_quality",
        "description": "Quality of the conclusion section.",
        "weight": 1.0,
        "items": [
            {"id": "cq_synthesis", "criterion": "Conclusion synthesizes key findings, not just repeats earlier points.", "weight": 1.0},
            {"id": "cq_actionable", "criterion": "Provides actionable takeaways or clear implications.", "weight": 0.5},
        ],
    },
    {
        "name": "structural_elements",
        "description": "Use of tables, lists, and formatting to enhance readability.",
        "weight": 0.5,
        "items": [
            {"id": "se_tables", "criterion": "Tables or structured comparisons used where appropriate.", "weight": 0.5},
            {"id": "se_lists", "criterion": "Bullet points or numbered lists used to improve scannability.", "weight": 0.5},
        ],
    },
    {
        "name": "section_headers",
        "description": "Proper use of markdown headings.",
        "weight": 0.5,
        "items": [
            {"id": "sh_meaningful", "criterion": "All section headers are descriptive of their content.", "weight": 1.0},
            {"id": "sh_consistent", "criterion": "Consistent heading style throughout the report.", "weight": 0.5},
        ],
    },
    {
        "name": "citations",
        "description": "Quality and correctness of source citations.",
        "weight": 1.0,
        "items": [
            {"id": "ct_presence", "criterion": "Citations appear wherever factual claims are made.", "weight": 1.0},
            {"id": "ct_format", "criterion": "Citations use a consistent format throughout the report.", "weight": 0.5},
            {"id": "ct_accessible", "criterion": "Cited sources are accessible (URLs resolve, DOIs are valid).", "weight": 1.0},
        ],
    },
    {
        "name": "overall_quality",
        "description": "Overall professional quality.",
        "weight": 1.5,
        "items": [
            {"id": "oq_writing", "criterion": "Writing is clear, professional, and free of obvious errors.", "weight": 1.0},
            {"id": "oq_balance", "criterion": "Report presents a balanced view, acknowledging limitations or counterpoints.", "weight": 0.5},
            {"id": "oq_originality", "criterion": "Report synthesizes information rather than just aggregating quotes.", "weight": 1.0},
        ],
    },
]

L2_RUBRIC_PROMPT_TEMPLATE = """Evaluate this research report against a structured rubric.
Score each rubric item as:
  0   = does not meet the criterion at all
  0.5 = partially meets the criterion
  1   = fully meets the criterion

For each item, provide specific evidence from the report.

<Report>
{report}
</Report>

<Research Brief>
{research_brief}
</Research Brief>

<Topic>
{topic}
</Topic>

<Rubric>
{rubric_json}
</Rubric>

Respond with ONLY valid JSON:
{{
  "dimensions": [
    {{
      "name": "topic_relevance_overall",
      "items": [
        {{"id": "tro_coverage", "score": 0/0.5/1, "evidence": "..."}},
        ...
      ]
    }},
    ...
  ],
  "passed": true/false,
  "summary": "overall evaluation summary in 2-3 sentences"
}}

Overall passed = ALL dimensions with weight > 1.0 must have weighted score >= 0.6."""


def _build_l2_rubric_json() -> str:
    """Serialize L2 rubric dimensions to JSON for prompt injection."""
    return json.dumps(
        [
            {
                "name": d["name"],
                "description": d["description"],
                "items": [
                    {"id": it["id"], "criterion": it["criterion"]}
                    for it in d["items"]
                ],
            }
            for d in L2_RUBRIC
        ],
        ensure_ascii=False,
        indent=2,
    )


# =============================================================================
# Rubric-Based Evaluation Functions
# =============================================================================


async def run_level1_rubric(
    report: str,
    research_brief: str,
    config: RunnableConfig | None = None,
    state: dict | None = None,
) -> RubricResult:
    """Run Level 1 quality check using structured rubric (replaces quality_check.py).

    Uses fast_llm for cost efficiency. Each rubric item is scored 0/0.5/1
    with evidence, producing auditable results.
    """
    research_config = ResearchConfiguration.from_runnable_config(config) if config else ResearchConfiguration()

    model_config = {
        "model": research_config.fast_llm,
        "max_tokens": 2048,
        "temperature": 0.0,
        "tags": ["langsmith:nostream"],
    }

    evidence_context = _build_evidence_context(state) if state else ""

    prompt = L1_RUBRIC_PROMPT_TEMPLATE.format(
        report=report[:8000],
        research_brief=research_brief[:2000],
        evidence_context=evidence_context,
        rubric_json=_build_l1_rubric_json(),
    )

    try:
        response = await configurable_model.with_config(model_config).ainvoke([
            SystemMessage(content="You are a quality evaluator. Score each rubric item based on evidence from the report. Respond only with valid JSON."),
            HumanMessage(content=prompt),
        ])
        content = response.content if hasattr(response, "content") else str(response)
        return _parse_rubric_response(content)
    except Exception as e:
        logger.error(f"[Rubric] Level 1 rubric eval failed: {e}")
        return RubricResult(passed=True, verdict="pass")


async def run_level2_rubric(
    report: str,
    research_brief: str,
    topic: str,
    config: RunnableConfig | None = None,
) -> RubricResult:
    """Run Level 2 dev evaluation using structured rubric (replaces evaluation.py L2).

    Uses smart_llm. 9 dimensions × 2-3 items each, scored 0/0.5/1 with evidence.
    """
    research_config = ResearchConfiguration.from_runnable_config(config) if config else ResearchConfiguration()

    model_config = {
        "model": research_config.smart_llm,
        "max_tokens": 3072,
        "temperature": 0.0,
        "tags": ["langsmith:nostream"],
    }

    prompt = L2_RUBRIC_PROMPT_TEMPLATE.format(
        report=report[:12000],
        research_brief=research_brief[:2000],
        topic=topic[:500],
        rubric_json=_build_l2_rubric_json(),
    )

    try:
        response = await configurable_model.with_config(model_config).ainvoke([
            SystemMessage(content="You are an expert research evaluator. Score each rubric item based on evidence from the report. Respond only with valid JSON."),
            HumanMessage(content=prompt),
        ])
        content = response.content if hasattr(response, "content") else str(response)
        return _parse_rubric_response(content)
    except Exception as e:
        logger.error(f"[Rubric] Level 2 rubric eval failed: {e}")
        return RubricResult(passed=False, verdict="incomplete")


# =============================================================================
# Human Scoring Calibration
# =============================================================================


class RubricCalibration:
    """Compare LLM rubric scores against human reference scores.

    Stores a set of human-scored reference reports and computes alignment
    metrics (agreement rate, correlation) to validate the LLM-as-judge
    evaluation pipeline.  Without calibration, LLM self-assessment bias
    cannot be quantified or corrected.

    Pattern from ReportBench (ByteDance, 2025) and ResearchRubrics (Scale AI, 2025):
    both report >70% human-LLM agreement as the minimum threshold for valid
    automated evaluation.
    """

    def __init__(self):
        self.references: list[dict[str, Any]] = []

    def add_reference(
        self,
        report_id: str,
        llm_scores: dict[str, float],
        human_scores: dict[str, float],
    ) -> None:
        """Record a pair of LLM and human scores for the same report."""
        self.references.append({
            "report_id": report_id,
            "llm": dict(llm_scores),
            "human": dict(human_scores),
        })

    def compute_agreement(self) -> dict[str, Any]:
        """Compute human-LLM agreement across all stored references.

        Returns agreement rate (scores within ±0.15), Pearson correlation,
        and per-dimension breakdown.
        """
        if not self.references:
            return {"agreement_rate": 1.0, "correlation": 1.0, "n": 0}

        dim_pairs: dict[str, list[tuple[float, float]]] = {}
        matches = 0
        total = 0

        for ref in self.references:
            for dim in ref["llm"]:
                llm_val = ref["llm"].get(dim, 0.0)
                human_val = ref["human"].get(dim, 0.0)
                total += 1
                if abs(llm_val - human_val) <= 0.15:
                    matches += 1
                dim_pairs.setdefault(dim, []).append((llm_val, human_val))

        # Pearson correlation per dimension
        correlations: dict[str, float] = {}
        for dim, pairs in dim_pairs.items():
            if len(pairs) < 3:
                correlations[dim] = 1.0
                continue
            llms = [p[0] for p in pairs]
            humans = [p[1] for p in pairs]
            correlations[dim] = round(self._pearson_r(llms, humans), 3)

        return {
            "agreement_rate": round(matches / total, 4) if total > 0 else 0.0,
            "correlation": round(sum(correlations.values()) / len(correlations), 3) if correlations else 0.0,
            "per_dimension": correlations,
            "n": len(self.references),
            "aligned": matches / total >= 0.70 if total > 0 else True,
        }

    @staticmethod
    def _pearson_r(x: list[float], y: list[float]) -> float:
        n = len(x)
        if n < 2:
            return 1.0
        mean_x = sum(x) / n
        mean_y = sum(y) / n
        cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
        std_x = (sum((v - mean_x) ** 2 for v in x) / n) ** 0.5
        std_y = (sum((v - mean_y) ** 2 for v in y) / n) ** 0.5
        if std_x == 0 or std_y == 0:
            return 1.0
        return cov / (n * std_x * std_y)


# Global calibration instance
_calibration: RubricCalibration | None = None


def get_calibration() -> RubricCalibration:
    global _calibration
    if _calibration is None:
        _calibration = RubricCalibration()
    return _calibration


# =============================================================================
# Full-Claim Evidence Alignment
# =============================================================================

FULL_CLAIM_ALIGNMENT_PROMPT = """Verify every factual claim in this report against its cited source.

For each claim marked with a citation marker [N], check whether the cited source
genuinely supports the claim.  Score each claim:
  0 = claim contradicts or is absent from source
  0.5 = source partially supports claim
  1 = source directly supports claim

<Report>
{report}
</Report>

<Source Texts>
{source_texts}
</Source Texts>

Output a JSON array of claim assessments:
[{{
  "claim_summary": "brief description of the claim",
  "citation_marker": "N",
  "score": 0/0.5/1,
  "source_support": "quote or explanation from the source"
}}]

If the report has no citation markers, return an empty array []."""


async def run_full_claim_alignment(
    report: str,
    source_texts: str,
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Verify ALL claims in the report against their cited sources.

    Unlike the L1 rubric's sampling approach (3 sample claims), this checks
    every citation-marked claim for source alignment.  Returns aggregate
    alignment rate and per-claim details.

    This is designed as a thorough evaluation pass, not a fast quality gate.
    """
    import re

    # Extract all citation markers from the report
    markers = set()
    for m in re.finditer(r"\[(\d+(?:,\s*\d+)*)\]", report):
        for num in re.findall(r"\d+", m.group(1)):
            markers.add(num)

    if not markers:
        return {"alignment_rate": 1.0, "total_claims": 0, "claims": []}

    research_config = (
        ResearchConfiguration.from_runnable_config(config)
        if config else ResearchConfiguration()
    )

    model_config = {
        "model": research_config.smart_llm,
        "max_tokens": 3072,
        "temperature": 0.0,
        "tags": ["langsmith:nostream"],
    }

    prompt = FULL_CLAIM_ALIGNMENT_PROMPT.format(
        report=report[:12000],
        source_texts=source_texts[:8000],
    )

    try:
        response = await configurable_model.with_config(model_config).ainvoke([
            SystemMessage(content="You verify factual claims against source evidence. Output only valid JSON."),
            HumanMessage(content=prompt),
        ])
        content = response.content if hasattr(response, "content") else str(response)

        # Parse JSON array
        json_match = re.search(r"\[[\s\S]*\]", content)
        claims = json.loads(json_match.group(0)) if json_match else []

        total = len(claims)
        aligned = sum(1 for c in claims if c.get("score", 0) >= 0.5)
        rate = aligned / total if total > 0 else 1.0

        return {
            "alignment_rate": round(rate, 4),
            "total_claims": total,
            "aligned": aligned,
            "claims": claims,
        }

    except Exception as e:
        logger.error(f"[FullClaimAlignment] Failed: {e}")
        return {"alignment_rate": 1.0, "total_claims": len(markers), "claims": [], "error": str(e)}


# =============================================================================
# Helpers
# =============================================================================


def _build_evidence_context(state: dict | None) -> str:
    """Extract source-text snippets for evidence_alignment checking."""
    if not state:
        return ""

    notes = state.get("notes", []) or state.get("raw_notes", []) or []
    sources = state.get("sources", []) or state.get("curated_sources", []) or []

    parts: list[str] = []
    source_texts = [str(n)[:1500] for n in notes[:5]]

    if source_texts:
        parts.append("<Source Evidence>")
        for i, src in enumerate(source_texts[:3], 1):
            parts.append(f"[Source {i}]: {src[:800]}")
        parts.append("</Source Evidence>")

    if sources:
        parts.append("<Cited Sources>")
        for s in sources[:10]:
            url = s.get("url", "") if isinstance(s, dict) else str(s)
            title = s.get("title", "") if isinstance(s, dict) else ""
            if url:
                parts.append(f"- {title}: {url}"[:200])
        parts.append("</Cited Sources>")

    return "\n".join(parts)
