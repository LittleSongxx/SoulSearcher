"""Report Generation: Source Curation → Report Composition → Final Assembly.

The final stage of the unified Deep Research pipeline.
Integrates patterns from:
- open_deep_research: final_report_generation with token-limit retry logic
- gpt-researcher: source curation with quality ranking
- Unified design: complexity-aware model selection for report writing

Pipeline:
1. Source Curation: Rank and filter collected sources
2. Report Generation: Multi-stage writing with token-limit handling
3. Quality Check: Level 1 fast_llm evaluation → auto-revise (up to 2 iterations)
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, get_buffer_string
from langchain_core.runnables import RunnableConfig

from agent.core.configuration import ResearchConfiguration
from agent.core.model_routing import build_model_config, configurable_model
from agent.core.prompts import (
    HTML_REPORT_CSS_TEMPLATE,
    resolve_prompt,
)
from agent.core.state import AgentState
from agent.runtime.context import get_viewed_images
from agent.workflows.citation_agent import build_claim_citation_matrix
from agent.workflows.evidence_ledger import (
    build_citation_table,
    evaluate_citation_gate,
    format_citation_table_for_prompt,
)
from agent.workflows.evidence_ledger import (
    build_evidence_ledger as build_structured_evidence_ledger,
)
from agent.workflows.evidence_ledger import (
    evidence_passages as structured_evidence_passages,
)
from agent.workflows.plan_graph import (
    apply_replan_actions,
    build_gap_replan_actions,
    ensure_plan_graph,
    summarize_plan_graph,
    todos_from_plan_graph,
)
from agent.workflows.report_artifact_service import (
    ingest_memory_artifacts as _ingest_memory_artifacts,
)
from agent.workflows.report_artifact_service import (
    persist_workspace_artifacts as _persist_workspace_artifacts,
)
from agent.workflows.research_todo import (
    emit_todo_updates,
    summarize_todos,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Report Structure Templates (Report Composer — Step 2 of 4)
# =============================================================================

REPORT_STRUCTURE_TEMPLATES = {
    "academic": """Report structure: Academic Literature Review.
- Title: A concise, descriptive title
- ## Abstract: 3-5 sentence summary of findings
- ## Introduction: Context, scope, and research questions
- ## Literature Review: Organized by theme/subtopic with sub-sections
- ## Analysis & Synthesis: Cross-cutting themes, contradictions, gaps
- ## Conclusion: Key insights and directions for future research
- ### Sources: Full citations with links""",

    "market_research": """Report structure: Market Research Report.
- Title: Market analysis title
- ## Executive Summary: Key findings for decision-makers
- ## Market Overview: Size, growth, trends
- ## Competitive Landscape: Key players, market share, positioning
- ## Customer Segments: Demographics, behaviors, needs
- ## Opportunities & Risks: Strategic analysis
- ## Recommendations: Actionable next steps
- ### Sources: Data sources and references""",

    "comparison": """Report structure: Comparative Analysis.
- Title: Comparison-focused title
- ## Overview: What is being compared and why
- ## Option A: Detailed analysis with pros/cons
- ## Option B: Detailed analysis with pros/cons
- (## Option C, D, etc. as needed)
- ## Head-to-Head Comparison Table: Key dimensions
- ## Recommendation: Which to choose and when
- ### Sources: References""",

    "how_to": """Report structure: Practical Guide / How-To.
- Title: Action-oriented title
- ## Overview: What this guide covers and prerequisites
- ## Step-by-Step Instructions: Numbered sections, one per major step
  - Each step: explanation → action → expected result → troubleshooting
- ## Best Practices: Key tips and common pitfalls
- ## Tools & Resources: Recommended tools, links, further reading
- ### Sources: References""",

    "summary": """Report structure: Topic Summary / Briefing.
- Title: Clear topic summary title
- ## Overview: 2-3 paragraph executive summary
- ## Background: Historical context and key developments
- ## Current State: Where things stand now
- ## Key Players / Stakeholders: Who matters and why
- ## Future Outlook: Trends and predictions
- ### Sources: Key references""",

    "deep_analysis": """Report structure: In-Depth Research Analysis.
- Title: Comprehensive analysis title
- ## Executive Summary: Key takeaways
- ## Introduction: Research questions and methodology
- ## Background & Context: Historical and domain context
- ## Findings: Multiple sub-sections organized by theme
  - Each: evidence → analysis → implications
- ## Cross-Cutting Analysis: Patterns across findings
- ## Limitations & Gaps: What wasn't covered and why
- ## Conclusion: Synthesis and recommendations
- ## Appendices: Data tables, methodology details (if needed)
- ### Sources: Comprehensive reference list""",

    "policy_brief": """Report structure: Policy Brief / Regulatory Analysis.
- Title: Policy-focused title with jurisdiction and topic
- ## Executive Summary: 3-5 key findings for policymakers
- ## Policy Context: Legislative/regulatory background and timeline
- ## Current Framework: Existing rules, their rationale, and implementation status
- ## Comparative Analysis: How other jurisdictions handle this issue
- ## Stakeholder Impact: Effects on industry, consumers, government
- ## Recommendations: Actionable policy options with pros/cons
- ### Sources: Legal references, official documents, expert commentary""",

    "investment_analysis": """Report structure: Investment Research Report.
- Title: Ticker/company name — investment thesis
- ## Investment Summary: Thesis in 3-5 sentences, target price/valuation
- ## Business Overview: Revenue model, competitive moat, market position
- ## Financial Analysis: Revenue growth, margins, cash flow, balance sheet health
- ## Industry & Competitive Landscape: Market structure, peers comparison table
- ## Catalysts & Risks: Near-term triggers, downside scenarios
- ## Valuation: Comparable company analysis, DCF assumptions
- ## Conclusion: Clear buy/hold/sell recommendation with conviction level
- ### Sources: Financial filings, analyst reports, industry data""",

    "technical_review": """Report structure: Technical Review / Architecture Analysis.
- Title: Technology, system, or architecture under review
- ## Overview: What is being reviewed and evaluation criteria
- ## Architecture & Design: System components, data flow, design patterns
- ## Key Technical Decisions: Trade-offs made, alternatives considered
- ## Performance & Scalability: Benchmarks, bottlenecks, scaling characteristics
- ## Code Quality & Maintainability: Patterns, test coverage, documentation
- ## Security & Compliance: Threat surface, known issues, compliance status
- ## Recommendations: Prioritized improvements with effort/impact estimates
- ### Sources: Code repositories, documentation, benchmarks, CVE references""",

    "trend_report": """Report structure: Trend Analysis / Horizon Scan.
- Title: Domain trend analysis with time horizon
- ## Executive Summary: Major trends and their significance
- ## Methodology: Data sources, search strategy, time range
- ## Trend 1..N: For each trend — evidence, trajectory, key players, implications
- ## Cross-Trend Patterns: Interactions and reinforcing/opposing forces
- ## Emerging Signals: Weak signals worth monitoring
- ## Outlook: 2-5 year projections with confidence levels
- ### Sources: Publications, patents, funding data, expert interviews""",

    "newsletter": """Report structure: Newsletter / Curated Briefing.
- Title: Issue title with date and volume number
- ## Top Stories: 3-5 stories with 2-3 sentence summaries and links
- ## Deep Dive: One story with detailed analysis and context
- ## Industry Moves: Key hires, funding rounds, acquisitions
- ## Worth Reading: Curated links with one-line descriptions
- ## Coming Up: Events, deadlines, expected announcements
- ### Sources: Original reporting, press releases, official announcements""",

    "audit_report": """Report structure: Audit / Reproducibility Report.
- Title: Audit scope and subject
- ## Executive Summary: Overall finding and confidence level
- ## Audit Scope & Methodology: What was checked and how
- ## Claim-by-Claim Verification: Each claim → evidence found → verdict (✓/✗/⚠)
- ## Reproducibility Assessment: Environment, dependencies, execution results
- ## Data Integrity: Source data availability, preprocessing, statistical validity
- ## Findings & Recommendations: Systematic issues and corrective actions
- ## Appendix: Full evidence table, reproduction logs
- ### Sources: Original paper, code repository, reproduced outputs""",
}

def compose_report_structure(research_brief: str) -> str:
    """Select the best report structure template based on research brief content.

    Uses keyword heuristics for fast classification (no LLM call needed).
    Falls back to 'summary' for ambiguous topics.
    """
    brief_lower = research_brief.lower()[:1000]

    # Keyword-based classification
    scores = {
        "academic": sum(
            1 for kw in ["literature", "review", "paper", "research", "scholar",
                          "study", "academic", "scientific", "peer-review"]
            if kw in brief_lower
        ),
        "market_research": sum(
            1 for kw in ["market", "industry", "revenue", "competitor", "growth",
                          "trend", "forecast", "market share", "customer segment"]
            if kw in brief_lower
        ),
        "comparison": sum(
            1 for kw in ["compare", "vs", "versus", "alternative", "difference",
                          "better", "pros and cons", "which is", "or"]
            if kw in brief_lower
        ),
        "how_to": sum(
            1 for kw in ["how to", "guide", "steps", "tutorial", "setup",
                          "install", "configure", "build", "create a"]
            if kw in brief_lower
        ),
        "deep_analysis": sum(
            1 for kw in ["analyze", "deep", "comprehensive", "complex", "multi-faceted",
                          "thorough", "investigation", "implications", "root cause"]
            if kw in brief_lower
        ),
        "summary": 0.5,  # Default baseline
    }

    best_type = max(scores, key=scores.get)
    logger.info(f"[ReportComposer] Selected '{best_type}' structure for report")
    return REPORT_STRUCTURE_TEMPLATES.get(best_type, REPORT_STRUCTURE_TEMPLATES["summary"])


# =============================================================================
# HTML Report Helpers
# =============================================================================


def _inject_images_into_html(
    html: str,
    viewed_images: dict[str, dict[str, str]],
) -> str:
    """Replace <!-- IMAGE: key --> placeholders with actual <img> tags.

    Scans the HTML for image placeholder comments and replaces each with a
    <figure> containing a base64-encoded <img> tag. Handles both exact key
    matching and fuzzy URL matching.
    """
    import re

    if not viewed_images:
        return html

    # Build lookup: key → {base64, mime_type}
    # Also index by URL suffix for fuzzy matching
    for key, img_data in viewed_images.items():
        base64_data = img_data.get("base64", "")
        mime_type = img_data.get("mime_type", "image/png")
        if not base64_data:
            continue

        img_tag = (
            f'<figure>\n'
            f'  <img src="data:{mime_type};base64,{base64_data}"\n'
            f'       alt="Research image from {key[:100]}"\n'
            f'       loading="lazy" style="max-width:100%; border-radius:6px;'
            f'box-shadow:0 1px 3px rgba(0,0,0,0.1);">\n'
            f'  <figcaption>Source: {key[:120]}</figcaption>\n'
            f'</figure>'
        )

        # Try exact placeholder match first
        placeholder = f"<!-- IMAGE: {key} -->"
        if placeholder in html:
            html = html.replace(placeholder, img_tag)
            logger.info(f"[HTMLReport] Injected image: {key[:80]}")
            continue

        # Try fuzzy: IMAGE placeholder with partial key match
        pattern = re.compile(
            r'<!--\s*IMAGE:\s*' + re.escape(key[:60]) + r'.*?-->',
            re.IGNORECASE,
        )
        if pattern.search(html):
            html = pattern.sub(img_tag, html, count=1)
            logger.info(f"[HTMLReport] Injected image (fuzzy): {key[:80]}")
            continue

    # Second pass: handle any remaining IMAGE placeholders for which we have
    # images, using URL suffix matching
    remaining_placeholders = re.findall(r'<!--\s*IMAGE:\s*(.*?)\s*-->', html)
    for placeholder_key in remaining_placeholders:
        for img_key, img_data in viewed_images.items():
            if placeholder_key in img_key or img_key.endswith(placeholder_key):
                base64_data = img_data.get("base64", "")
                mime_type = img_data.get("mime_type", "image/png")
                if not base64_data:
                    continue
                img_tag = (
                    f'<figure>\n'
                    f'  <img src="data:{mime_type};base64,{base64_data}"\n'
                    f'       alt="Research image" loading="lazy"\n'
                    f'       style="max-width:100%; border-radius:6px;'
                    f'box-shadow:0 1px 3px rgba(0,0,0,0.1);">\n'
                    f'  <figcaption>Source: {img_key[:120]}</figcaption>\n'
                    f'</figure>'
                )
                old = f"<!-- IMAGE: {placeholder_key} -->"
                html = html.replace(old, img_tag)
                logger.info(
                    f"[HTMLReport] Injected image (fuzzy key): "
                    f"{img_key[:80]}"
                )
                break

    return html


def _wrap_html_document(content: str, title: str = "Research Report") -> str:
    """Wrap HTML content in a complete document with DOCTYPE, head, and body.

    If the content already starts with <style> or contains a <style> block,
    it's treated as a fragment and wrapped. If it's already a complete HTML
    document, it's returned as-is.
    """
    content_stripped = content.strip()

    # Already a complete HTML document
    if content_stripped.startswith("<!DOCTYPE") or content_stripped.startswith(
        "<html"
    ):
        return content_stripped

    # Has style block — wrap in document
    import re

    style_block = ""
    body_content = content_stripped

    # Extract existing <style> block if present at the start
    style_match = re.match(
        r'(<style[^>]*>.*?</style>)', content_stripped, re.DOTALL
    )
    if style_match:
        style_block = style_match.group(1)
        body_content = content_stripped[style_match.end():].strip()

    # If no style block, inject our default
    if not style_block:
        style_block = f"<style>{HTML_REPORT_CSS_TEMPLATE}</style>"

    # Ensure content is wrapped in article
    if "<article" not in body_content:
        body_content = (
            f'<article class="research-report">\n{body_content}\n</article>'
        )

    return (
        f"<!DOCTYPE html>\n"
        f'<html lang="en">\n'
        f"<head>\n"
        f'<meta charset="UTF-8">\n'
        f'<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f"<title>{title}</title>\n"
        f"{style_block}\n"
        f"</head>\n"
        f"<body>\n"
        f"{body_content}\n"
        f"</body>\n"
        f"</html>"
    )


def _strip_markup_for_evaluation(content: str) -> str:
    text = str(content or "")
    if "<" in text and ">" in text:
        text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _eval_result_to_gate(name: str, level: int, result: Any) -> dict[str, Any]:
    score = float(getattr(result, "overall_score", 0.0) or 0.0)
    passed = bool(getattr(result, "overall_passed", False))
    errors = list(getattr(result, "errors", []) or [])
    metadata = dict(getattr(result, "metadata", {}) or {})
    verdict = "pass" if passed and not errors else ("incomplete" if errors else "revise")
    return {
        "name": name,
        "level": level,
        "passed": passed and not errors,
        "score": round(max(0.0, min(1.0, score)), 4),
        "verdict": verdict,
        "threshold": 0.7,
        "details": {
            "summary": getattr(result, "summary", ""),
            "errors": errors,
            "metadata": metadata,
        },
    }


def _build_claim_artifacts(claim_alignment: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    claims: list[dict[str, Any]] = []
    annotations: list[dict[str, Any]] = []
    for claim in claim_alignment.get("claims", []) or []:
        if not isinstance(claim, dict):
            continue
        try:
            score = float(claim.get("score", 0.0) or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        score = max(0.0, min(1.0, score))
        if score >= 1.0:
            status = "supported"
        elif score >= 0.5:
            status = "partial"
        else:
            status = "unsupported"
        citation_marker = str(claim.get("citation_marker", "")).strip()
        claim_summary = str(claim.get("claim_summary", "")).strip()
        support = str(claim.get("source_support", "")).strip()
        source_id = str(claim.get("source_id", "")).strip()
        canonical_url = str(claim.get("canonical_url", "")).strip()
        snippet_hash = str(claim.get("snippet_hash", "")).strip()
        claims.append(
            {
                "claim": claim_summary,
                "status": status,
                "score": round(score, 4),
                "citation_marker": citation_marker,
                "notes": support,
                "evidence_passages": [support] if support else [],
                "source_id": source_id,
                "canonical_url": canonical_url,
                "snippet_hash": snippet_hash,
            }
        )
        annotations.append(
            {
                "citation_marker": citation_marker,
                "claim_summary": claim_summary,
                "status": status,
                "score": round(score, 4),
                "source_id": source_id,
                "canonical_url": canonical_url,
                "snippet_hash": snippet_hash,
            }
        )
    return claims, annotations


def _build_evidence_ledger(
    state: dict[str, Any],
    *,
    curated_sources: list[dict[str, Any]] | None = None,
    notes: list[str] | None = None,
    max_items: int = 24,
) -> list[dict[str, Any]]:
    """Report-local API wrapper around the centralized evidence ledger."""
    return build_structured_evidence_ledger(
        state,
        curated_sources=curated_sources,
        notes=notes,
        max_items=max_items,
    )


def _evidence_passages(evidence_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Report-local API wrapper around centralized passage generation."""
    return structured_evidence_passages(evidence_items)


def _build_quality_summary(
    report_format: str,
    quality_result: Any | None,
    l3_result: Any | None,
) -> dict[str, Any]:
    quality_result = quality_result or None
    l1_snapshot = (
        dict((quality_result.metadata or {}).get("level1_rubric", {}))
        if quality_result else {}
    )
    l2_snapshot = (
        dict((quality_result.metadata or {}).get("level2_rubric", {}))
        if quality_result else {}
    )
    claim_alignment = (
        dict((quality_result.metadata or {}).get("claim_alignment", {}))
        if quality_result else {}
    )
    quality_gates = list(getattr(quality_result, "gates", []) or []) if quality_result else []
    overall_verdict = getattr(quality_result, "verdict", "incomplete") if quality_result else "incomplete"
    overall_passed = bool(getattr(quality_result, "passed", False)) if quality_result else False
    overall_score = (
        float(getattr(quality_result, "score", 0.0) or 0.0)
        if quality_result else 0.0
    )
    if quality_result is None and l3_result is not None:
        overall_passed = bool(getattr(l3_result, "overall_passed", False))
        overall_verdict = "pass" if overall_passed else "revise"
        overall_score = float(getattr(l3_result, "overall_score", 0.0) or 0.0)
    if l3_result is not None and not bool(getattr(l3_result, "overall_passed", False)):
        overall_passed = False
        if overall_verdict == "pass":
            overall_verdict = "revise"
    return {
        "overall_score": round(overall_score, 4),
        "overall_verdict": overall_verdict,
        "publish_ready": overall_passed,
        "level1_score": round(float(l1_snapshot.get("overall_score", 0.0) or 0.0), 4),
        "level2_score": round(float(l2_snapshot.get("overall_score", 0.0) or 0.0), 4),
        "level3_score": round(float(getattr(l3_result, "overall_score", 0.0) or 0.0), 4) if l3_result else None,
        "citation_coverage_score": round(float((getattr(quality_result, "dimensions", {}) or {}).get("citation_density", 0.0) or 0.0), 4) if quality_result else 0.0,
        "claim_alignment_rate": round(float(claim_alignment.get("alignment_rate", 1.0) or 0.0), 4),
        "claim_alignment_total_claims": int(claim_alignment.get("total_claims", 0) or 0),
        "degradation_detected": bool(getattr(l3_result, "metadata", {}).get("degradation_detected", False)) if l3_result else False,
        "report_format": report_format,
        "quality_gate_count": len(quality_gates) + (1 if l3_result else 0),
    }


def _build_followup_research_brief(
    research_brief: str,
    quality_gates: list[dict[str, Any]],
    quality_result: Any | None,
) -> str:
    """Create a focused research brief for quality-driven follow-up research."""
    issues: list[str] = []
    suggestions: list[str] = []
    if quality_result is not None:
        issues.extend(str(item) for item in getattr(quality_result, "issues", []) or [])
        suggestions.extend(
            str(item) for item in getattr(quality_result, "suggestions", []) or []
        )
    for gate in quality_gates:
        if not isinstance(gate, dict) or gate.get("passed"):
            continue
        details = gate.get("details") if isinstance(gate.get("details"), dict) else {}
        summary = details.get("summary") or details.get("error") or gate.get("name")
        if summary:
            issues.append(str(summary))

    unique_issues = []
    seen = set()
    for issue in issues:
        text = re.sub(r"\s+", " ", issue).strip()
        if text and text not in seen:
            seen.add(text)
            unique_issues.append(text)

    unique_suggestions = []
    seen.clear()
    for suggestion in suggestions:
        text = re.sub(r"\s+", " ", suggestion).strip()
        if text and text not in seen:
            seen.add(text)
            unique_suggestions.append(text)

    issue_text = "\n".join(f"- {issue}" for issue in unique_issues[:8])
    suggestion_text = "\n".join(f"- {item}" for item in unique_suggestions[:6])
    return (
        f"{research_brief}\n\n"
        "[Quality Follow-up Research]\n"
        "The previous draft did not pass quality gates. Conduct only the "
        "additional research needed to fix the gaps below, prioritizing "
        "source-backed evidence and citations. Return concise findings that can "
        "be merged with the existing notes.\n\n"
        f"Issues to address:\n{issue_text or '- Insufficient evidence or citation alignment.'}\n\n"
        f"Suggested fixes:\n{suggestion_text or '- Gather stronger sources and verify unsupported claims.'}"
    )


def _quality_gap_texts(
    quality_gates: list[dict[str, Any]],
    quality_result: Any | None,
) -> list[str]:
    gaps: list[str] = []
    if quality_result is not None:
        gaps.extend(str(item) for item in getattr(quality_result, "issues", []) or [])
        gaps.extend(str(item) for item in getattr(quality_result, "suggestions", []) or [])
    for gate in quality_gates:
        if not isinstance(gate, dict) or gate.get("passed"):
            continue
        details = gate.get("details") if isinstance(gate.get("details"), dict) else {}
        for key in ("missing_dimensions", "issues", "unsupported_claims", "citations_missing"):
            value = details.get(key)
            if isinstance(value, list):
                gaps.extend(str(item) for item in value)
        summary = details.get("summary") or details.get("error") or gate.get("name")
        if summary:
            gaps.append(str(summary))
    output: list[str] = []
    seen: set[str] = set()
    for gap in gaps:
        text = re.sub(r"\s+", " ", str(gap or "")).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output[:8]


async def _emit_report_plan_replan(
    thread_id: str,
    plan_graph: dict[str, Any],
    *,
    reason: str,
) -> None:
    if not thread_id:
        return
    try:
        from agent.core.events import ToolEvent, get_emitter

        emitter = await get_emitter(thread_id)
        payload = {
            "reason": reason,
            "plan_graph": plan_graph,
            "plan_summary": summarize_plan_graph(plan_graph),
        }
        await emitter.emit(ToolEvent.REPLAN_APPLIED, payload)
        await emitter.emit(ToolEvent.PLAN_GRAPH_UPDATE, payload)
    except Exception:
        return


# =============================================================================
# Final Report Generation Node
# =============================================================================

async def final_report_generation(
    state: AgentState, config: RunnableConfig
) -> dict:
    """Generate the final comprehensive research report.

    Takes all collected research findings (notes from supervisor's ConductResearch
    calls) and synthesizes them into a well-structured, comprehensive report.

    Includes token-limit retry logic from open_deep_research:
    - Progressive truncation when context is too large
    - 3 retry attempts with 10% reduction each time

    Pattern from open_deep_research: final_report_generation node.
    """
    research_config = ResearchConfiguration.from_runnable_config(config)

    notes = state.get("notes", [])
    research_brief = state.get("research_brief", "")
    complexity = state.get("complexity", "standard")
    curated_sources = state.get("curated_sources", [])
    report_format = state.get("report_format") or research_config.report_format
    embed_images = research_config.html_report_embed_images

    # Build findings string
    findings = "\n\n".join(notes) if notes else "No research findings available."

    # Optionally include curated sources context
    if curated_sources:
        if report_format == "html":
            source_lines = [
                '<section class="curated-sources">\n'
                '<h2>Curated Sources (ranked by quality)</h2>\n<ol>\n'
            ]
            for i, source in enumerate(curated_sources[:15], 1):
                source_lines.append(
                    f'<li>{source.get("title", "Unknown")}: '
                    f'<a href="{source.get("url", "")}">{source.get("url", "")}</a> '
                    f'(relevance: {source.get("relevance_score", "N/A")})</li>\n'
                )
            source_lines.append("</ol>\n</section>\n")
            findings += "\n".join(source_lines)
        else:
            source_lines = ["\n\n## Curated Sources (ranked by quality)\n"]
            for i, source in enumerate(curated_sources[:15], 1):
                source_lines.append(
                    f"{i}. {source.get('title', 'Unknown')}: {source.get('url', '')} "
                    f"(relevance: {source.get('relevance_score', 'N/A')})"
                )
            findings += "\n".join(source_lines)

    # Configure the report model
    model_name = research_config.get_report_model(complexity)
    # HTML reports benefit from more output tokens
    max_tokens = research_config.final_report_model_max_tokens
    if report_format == "html":
        max_tokens = max(max_tokens * 2, 16384)
    model_config = build_model_config(
        model=model_name,
        max_tokens=max_tokens,
        tags=["langsmith:nostream"],
    )

    messages = state.get("messages", [])
    message_text = get_buffer_string(messages)
    cleared_state = {
        "notes": {"type": "override", "value": []},
        "supervisor_messages": {"type": "override", "value": []},
    }

    # Attempt report generation with token-limit retry logic
    max_retries = 3
    findings_truncated = findings

    # === Select prompt based on report format ===
    current_date = datetime.now().strftime("%Y-%m-%d")

    # === Skill Writing Context (inject output/writing guidelines from active skills) ===
    from agent.skills.prompt import build_skill_context
    skill_ids = state.get("skill_ids", [])
    skill_writing_context = build_skill_context(
        skill_ids,
        purpose="writing",
        query=f"{research_brief}\n{message_text}\nformat: {report_format}",
        max_chars=3600,
    )

    quality_result = None
    l3_result = None
    pre_quality_artifacts = dict(state.get("deepsearch_artifacts", {}) or {})
    research_todos = list(state.get("research_todos", []) or [])
    plan_graph = ensure_plan_graph(
        state.get("plan_graph") or pre_quality_artifacts.get("plan_graph"),
        fallback_todos=research_todos,
    )
    research_todos = todos_from_plan_graph(plan_graph) or research_todos
    todo_summary = summarize_todos(research_todos)
    pre_quality_artifacts["plan_graph"] = plan_graph
    pre_quality_artifacts["plan_events"] = list(plan_graph.get("events", []) or [])
    pre_quality_artifacts["plan_summary"] = summarize_plan_graph(plan_graph)
    pre_quality_artifacts["research_todos"] = research_todos
    pre_quality_artifacts["todo_summary"] = todo_summary
    preliminary_evidence = _build_evidence_ledger(
        state,
        curated_sources=curated_sources,
        notes=notes,
    )
    citation_table = build_citation_table(preliminary_evidence)
    citation_table_prompt = format_citation_table_for_prompt(citation_table)
    if preliminary_evidence:
        pre_quality_artifacts["evidence_items"] = preliminary_evidence
        pre_quality_artifacts["passages"] = _evidence_passages(preliminary_evidence)
    if citation_table:
        pre_quality_artifacts["citation_table"] = citation_table
    if isinstance(state.get("retrieval_policy"), dict) and state.get("retrieval_policy"):
        pre_quality_artifacts["retrieval_policy"] = dict(state.get("retrieval_policy") or {})
    quality_state = dict(state)
    quality_state["deepsearch_artifacts"] = pre_quality_artifacts

    if report_format == "html":
        prompt_name = "final_report_html"
    else:
        prompt_name = "final_report"
        report_structure = compose_report_structure(research_brief)

    for attempt in range(max_retries + 1):
        try:
            if report_format == "html":
                prompt = resolve_prompt(prompt_name,
                    research_brief=research_brief,
                    messages=message_text,
                    findings=findings_truncated,
                    citation_table=citation_table_prompt,
                    date=current_date,
                    skill_writing_context=skill_writing_context,
                )
            else:
                prompt = resolve_prompt(prompt_name,
                    research_brief=research_brief,
                    messages=message_text,
                    findings=findings_truncated,
                    citation_table=citation_table_prompt,
                    date=current_date,
                    skill_writing_context=skill_writing_context,
                )
                # Inject selected structure template into the markdown prompt
                prompt = prompt.replace(
                    "Report structure options:",
                    f"{report_structure}\n\nAdditional structure options (current selection above):"
                )

            response = await configurable_model.with_config(model_config).ainvoke([
                HumanMessage(content=prompt)
            ])

            logger.info(
                f"[Report] Generated final report "
                f"(model={model_name}, format={report_format}, "
                f"length={len(response.content)} chars)"
            )

            # === Token Usage Tracking ===
            from agent.core.middleware import get_token_tracker
            tracker = get_token_tracker(config)
            usage = getattr(response, "usage_metadata", None) or {}
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            if input_tokens or output_tokens:
                tracker.record("report", input_tokens, output_tokens)

            final_content = response.content

            # === HTML Post-processing: Inject images ===
            if report_format == "html" and embed_images:
                viewed_images = get_viewed_images(config)
                if viewed_images:
                    try:
                        final_content = _inject_images_into_html(
                            final_content, viewed_images
                        )
                        logger.info(
                            f"[Report] Injected {len(viewed_images)} image(s) "
                            f"into HTML report"
                        )
                    except Exception as e:
                        logger.warning(
                            f"[Report] Image injection failed: {e}"
                        )

                # Wrap in complete HTML document
                title = (
                    research_brief[:120] if research_brief else "Research Report"
                )
                final_content = _wrap_html_document(final_content, title=title)

            # === Level 1 Quality Check (Phase 3) ===
            if report_format != "html" or research_config.evaluation_html_quality_check:
                try:
                    from agent.workflows.quality_check import (
                        generate_report_with_quality_check,
                    )
                    final_content, quality_result = await generate_report_with_quality_check(
                        state=quality_state,
                        report_content=final_content,
                        config=config,
                        max_revisions=research_config.max_report_revisions,
                    )
                except ImportError:
                    logger.debug("[Report] Quality check not available")
                except Exception as e:
                    logger.warning(f"[Report] Quality check skipped: {e}")

            # === Level 3 Deep Evaluation (strategic_llm, for deep complexity only) ===
            if complexity == "deep":
                try:
                    from agent.workflows.evaluation import run_level3_evaluation
                    eval_content = _strip_markup_for_evaluation(final_content)[:20000]
                    l3_result = await run_level3_evaluation(
                        report=eval_content,
                        research_brief=research_brief,
                        model_name=research_config.strategic_llm,
                    )
                    logger.info(
                        f"[Report] Level 3 deep eval: score={l3_result.overall_score:.2f}, "
                        f"passed={l3_result.overall_passed}, "
                        f"degradation={l3_result.metadata.get('degradation_detected', False)}"
                    )
                    if l3_result.metadata.get("degradation_detected"):
                        logger.warning(
                            f"[Report] Degradation detected: "
                            f"{l3_result.metadata.get('degradation_details', '')}"
                        )
                except ImportError:
                    logger.debug("[Report] Level 3 evaluation not available")
                except Exception as e:
                    logger.warning(f"[Report] Level 3 deep evaluation failed: {e}")

            quality_gates = list(getattr(quality_result, "gates", []) or []) if quality_result else []
            strict_citations = (
                research_config.deep_research_strict_citations
                and not research_config.legacy_citation_mode
            )
            if strict_citations:
                citation_gate = evaluate_citation_gate(
                    _strip_markup_for_evaluation(final_content),
                    sources=citation_table or preliminary_evidence,
                    evidence_items=preliminary_evidence,
                    passages=_evidence_passages(preliminary_evidence),
                    require_evidence=True,
                    require_citations=True,
                )
                quality_gates.append(
                    {
                        "name": "strict_current_run_citation_gate",
                        "level": 1,
                        "passed": bool(citation_gate.get("passed")),
                        "score": float(citation_gate.get("score", 0.0) or 0.0),
                        "verdict": citation_gate.get("verdict", "incomplete"),
                        "threshold": 1.0,
                        "details": citation_gate,
                    }
                )
                if not citation_gate.get("passed"):
                    if quality_result is not None:
                        quality_result.passed = False
                        quality_result.verdict = "incomplete"
                        quality_result.score = min(
                            float(getattr(quality_result, "score", 0.0) or 0.0),
                            0.49,
                        )
                        quality_result.issues.extend(citation_gate.get("issues", []) or [])
                        quality_result.suggestions.extend(citation_gate.get("suggestions", []) or [])
                    else:
                        from agent.workflows.quality_check import QualityCheckResult

                        quality_result = QualityCheckResult(
                            passed=False,
                            score=0.0,
                            verdict="incomplete",
                            issues=list(citation_gate.get("issues", []) or []),
                            suggestions=list(citation_gate.get("suggestions", []) or []),
                            gates=[],
                            metadata={},
                        )
            if l3_result is not None:
                quality_gates.append(
                    _eval_result_to_gate("level3_deep_evaluation", 3, l3_result)
                )

            claim_alignment = (
                dict((quality_result.metadata or {}).get("claim_alignment", {}))
                if quality_result else {}
            )
            claim_artifacts, citation_annotations = _build_claim_artifacts(
                claim_alignment
            )
            quality_evidence_items = list(
                (quality_result.metadata or {}).get("evidence_items", [])
            ) if quality_result else []
            evidence_items = _build_evidence_ledger(
                quality_state,
                curated_sources=curated_sources,
                notes=notes,
            )
            seen_evidence = {
                (
                    str(item.get("url") or item.get("source") or ""),
                    str(item.get("content") or item.get("text") or "")[:240],
                )
                for item in evidence_items
                if isinstance(item, dict)
            }
            for item in quality_evidence_items:
                if not isinstance(item, dict):
                    continue
                key = (
                    str(item.get("url") or item.get("source") or ""),
                    str(item.get("content") or item.get("text") or "")[:240],
                )
                if key not in seen_evidence:
                    evidence_items.append(item)
                    seen_evidence.add(key)
            final_citation_table = build_citation_table(evidence_items)
            claim_citation_matrix = build_claim_citation_matrix(
                _strip_markup_for_evaluation(final_content),
                final_citation_table,
            )
            quality_summary = _build_quality_summary(
                report_format=report_format,
                quality_result=quality_result,
                l3_result=l3_result,
            )
            quality_summary.update(claim_citation_matrix.get("summary", {}))
            evaluation_available = quality_result is not None or l3_result is not None
            delivery_ready = (
                bool(quality_summary.get("publish_ready"))
                if evaluation_available
                else True
            )
            followup_count = int(state.get("quality_followup_count", 0) or 0)
            can_follow_up = (
                evaluation_available
                and
                not delivery_ready
                and complexity != "simple"
                and followup_count < research_config.max_quality_followup_rounds
            )
            quality_summary["delivery_status"] = (
                "ready" if delivery_ready else (
                    "followup_research" if can_follow_up else "quality_failed"
                )
            )
            if not evaluation_available:
                quality_summary["delivery_status"] = "delivered_without_quality_eval"
                quality_summary["evaluation_available"] = False
                quality_summary["publish_ready"] = True
            else:
                quality_summary["evaluation_available"] = True
            if not delivery_ready:
                quality_gates.append({
                    "name": "delivery_gate",
                    "level": 3,
                    "passed": False,
                    "score": float(quality_summary.get("overall_score", 0.0) or 0.0),
                    "verdict": "blocked",
                    "threshold": float(research_config.evaluation_pass_threshold),
                    "details": {
                        "issues": list(getattr(quality_result, "issues", []) or [])[:8]
                        if quality_result else ["Quality evaluation did not pass."],
                        "requires_followup_research": any(
                            gate.get("verdict") == "incomplete"
                            for gate in quality_gates
                            if isinstance(gate, dict)
                        ) or can_follow_up,
                    },
                })
            quality_summary["quality_gate_count"] = len(quality_gates)

            deepsearch_artifacts = dict(quality_state.get("deepsearch_artifacts", {}) or {})
            if isinstance(state.get("retrieval_policy"), dict) and state.get("retrieval_policy"):
                deepsearch_artifacts["retrieval_policy"] = dict(state.get("retrieval_policy") or {})
            if not isinstance(deepsearch_artifacts.get("sources"), list) or not deepsearch_artifacts.get("sources"):
                deepsearch_artifacts["sources"] = list(curated_sources or state.get("sources", []))
            deepsearch_artifacts["passages"] = _evidence_passages(evidence_items)
            deepsearch_artifacts["citation_table"] = final_citation_table
            deepsearch_artifacts["claim_citation_matrix"] = claim_citation_matrix
            deepsearch_artifacts["quality_summary"] = quality_summary
            deepsearch_artifacts["quality_gates"] = quality_gates
            deepsearch_artifacts["delivery_status"] = quality_summary["delivery_status"]
            if can_follow_up:
                followup_requests = list(
                    deepsearch_artifacts.get("quality_followup_requests", []) or []
                )
                followup_brief = _build_followup_research_brief(
                    research_brief,
                    quality_gates,
                    quality_result,
                )
                followup_requests.append({
                    "round": followup_count + 1,
                    "brief": followup_brief,
                    "quality_summary": quality_summary,
                })
                previous_todos = list(research_todos)
                gap_texts = _quality_gap_texts(quality_gates, quality_result)
                if not gap_texts:
                    gap_texts = [
                        (
                            f"Follow-up research round {followup_count + 1}: "
                            f"{followup_brief[:180]}"
                        )
                    ]
                plan_graph = apply_replan_actions(
                    plan_graph,
                    build_gap_replan_actions(
                        gap_texts,
                        source="quality_gate",
                    ),
                    reason=f"quality follow-up round {followup_count + 1}",
                    source="quality_gate",
                )
                research_todos = todos_from_plan_graph(plan_graph)
                todo_summary = summarize_todos(research_todos)
                thread_id = str(
                    (config.get("configurable") or {}).get("thread_id") or ""
                )
                await emit_todo_updates(thread_id, research_todos, previous_todos)
                await _emit_report_plan_replan(
                    thread_id,
                    plan_graph,
                    reason=f"quality follow-up round {followup_count + 1}",
                )
                deepsearch_artifacts["quality_followup_requests"] = followup_requests
                deepsearch_artifacts["plan_graph"] = plan_graph
                deepsearch_artifacts["plan_events"] = list(plan_graph.get("events", []) or [])
                deepsearch_artifacts["plan_summary"] = summarize_plan_graph(plan_graph)
                deepsearch_artifacts["research_todos"] = research_todos
                deepsearch_artifacts["todo_summary"] = todo_summary
                deepsearch_artifacts["quality_details"] = {
                    "level1_rubric": dict((quality_result.metadata or {}).get("level1_rubric", {})) if quality_result else {},
                    "level2_rubric": dict((quality_result.metadata or {}).get("level2_rubric", {})) if quality_result else {},
                    "claim_alignment": claim_alignment,
                    "level3_evaluation": l3_result.to_dict() if l3_result is not None and hasattr(l3_result, "to_dict") else {},
                }
                deepsearch_artifacts["claims"] = claim_artifacts
                deepsearch_artifacts["citation_annotations"] = citation_annotations
                deepsearch_artifacts["evidence_items"] = evidence_items
                deepsearch_artifacts["citation_table"] = final_citation_table
                deepsearch_artifacts["claim_citation_matrix"] = claim_citation_matrix
                deepsearch_artifacts["research_brief"] = {
                    "research_brief": research_brief,
                    "complexity": complexity,
                    "report_format": report_format,
                }
                deepsearch_artifacts = _persist_workspace_artifacts(
                    config,
                    deepsearch_artifacts,
                    report_format=report_format,
                )
                logger.info(
                    "[Report] Quality gates failed; routing to follow-up research "
                    "(round %d/%d)",
                    followup_count + 1,
                    research_config.max_quality_followup_rounds,
                )
                return {
                    "messages": [
                        AIMessage(
                            content=(
                                "Quality gates found gaps; running focused "
                                "follow-up research before delivering the report."
                            )
                        )
                    ],
                    "research_brief": followup_brief,
                    "quality_summary": quality_summary,
                    "quality_gates": quality_gates,
                    "quality_followup_required": True,
                    "quality_followup_count": followup_count + 1,
                    "deepsearch_artifacts": deepsearch_artifacts,
                    "plan_graph": {"type": "override", "value": plan_graph},
                    "plan_events": {
                        "type": "override",
                        "value": list(plan_graph.get("events", []) or []),
                    },
                    "plan_version": int(plan_graph.get("version") or 1),
                    "research_plan": {
                        "type": "override",
                        "value": [str(task.get("title") or "") for task in plan_graph.get("tasks", [])],
                    },
                    "research_todos": {"type": "override", "value": research_todos},
                    "todo_summary": todo_summary,
                    "supervisor_messages": {"type": "override", "value": []},
                    "research_iterations": 0,
                    "final_report": "",
                }
            deepsearch_artifacts["quality_details"] = {
                "level1_rubric": dict((quality_result.metadata or {}).get("level1_rubric", {})) if quality_result else {},
                "level2_rubric": dict((quality_result.metadata or {}).get("level2_rubric", {})) if quality_result else {},
                "claim_alignment": claim_alignment,
                "level3_evaluation": l3_result.to_dict() if l3_result is not None and hasattr(l3_result, "to_dict") else {},
            }
            deepsearch_artifacts["claims"] = claim_artifacts
            deepsearch_artifacts["citation_annotations"] = citation_annotations
            deepsearch_artifacts["evidence_items"] = evidence_items
            deepsearch_artifacts["citation_table"] = final_citation_table
            deepsearch_artifacts["claim_citation_matrix"] = claim_citation_matrix
            deepsearch_artifacts["plan_graph"] = plan_graph
            deepsearch_artifacts["plan_events"] = list(plan_graph.get("events", []) or [])
            deepsearch_artifacts["plan_summary"] = summarize_plan_graph(plan_graph)
            deepsearch_artifacts["research_todos"] = research_todos
            deepsearch_artifacts["todo_summary"] = todo_summary
            deepsearch_artifacts["research_brief"] = {
                "research_brief": research_brief,
                "complexity": complexity,
                "report_format": report_format,
            }
            deepsearch_artifacts = _persist_workspace_artifacts(
                config,
                deepsearch_artifacts,
                report_content=final_content,
                report_format=report_format,
            )
            await _ingest_memory_artifacts(
                config,
                research_brief=research_brief,
                final_content=final_content,
                artifacts=deepsearch_artifacts,
                notes=notes,
            )

            return {
                "final_report": final_content,
                "messages": [AIMessage(content=final_content)],
                "report_format": report_format,
                "quality_summary": quality_summary,
                "quality_gates": quality_gates,
                "quality_followup_required": False,
                "deepsearch_artifacts": deepsearch_artifacts,
                "plan_graph": {"type": "override", "value": plan_graph},
                "plan_events": {
                    "type": "override",
                    "value": list(plan_graph.get("events", []) or []),
                },
                "plan_version": int(plan_graph.get("version") or 1),
                "research_plan": {
                    "type": "override",
                    "value": [str(task.get("title") or "") for task in plan_graph.get("tasks", [])],
                },
                "research_todos": {"type": "override", "value": research_todos},
                "todo_summary": todo_summary,
                **cleared_state,
            }

        except Exception as e:
            error_str = str(e)
            is_token_error = (
                "token" in error_str.lower() or
                "context" in error_str.lower() or
                "length" in error_str.lower()
            )

            if is_token_error:
                model_limit = research_config.get_model_max_tokens(model_name)

                if attempt == 0:
                    truncate_limit = model_limit * 4
                else:
                    truncate_limit = int(truncate_limit * 0.9)

                findings_truncated = findings[:truncate_limit]
                logger.warning(
                    f"[Report] Token limit exceeded, retrying with "
                    f"{len(findings_truncated)} chars (attempt {attempt + 1})"
                )
                continue
            else:
                logger.error(f"[Report] Generation error: {e}")
                return {
                    "final_report": f"Error generating final report: {error_str}",
                    "messages": [AIMessage(content="Report generation failed.")],
                    "quality_followup_required": False,
                    **cleared_state,
                }

    # All retries exhausted
    logger.error("[Report] Maximum retries exhausted")
    return {
        "final_report": "Error: Report generation failed after maximum retries.",
        "messages": [AIMessage(content="Report generation failed after maximum retries.")],
        "quality_followup_required": False,
        **cleared_state,
    }


# =============================================================================
# Source Curation (Standalone - can be called by supervisor or report)
# =============================================================================

async def curate_sources(
    research_topic: str,
    sources: list[dict],
    config: RunnableConfig,
    max_sources: int = 10,
) -> list[dict]:
    """Curate and rank sources by credibility and relevance.

    Uses LLM to evaluate each source against the research topic.
    Pattern from gpt-researcher's SourceCurator.

    Args:
        research_topic: The research topic for relevance assessment.
        sources: List of source dicts with 'url' and 'title'.
        config: RunnableConfig.
        max_sources: Maximum number of curated sources to return.

    Returns:
        Ranked list of source dicts with credibility_score and relevance_score.
    """
    if not sources:
        return []

    research_config = ResearchConfiguration.from_runnable_config(config)
    ranked = _rank_sources_hybrid(research_topic, sources, max_sources=max_sources)
    if not ranked:
        return sources[:max_sources]

    sources_text = "\n".join(
        [
            f"{i+1}. {s.get('title', 'Unknown')}: {s.get('url', '')}"
            for i, s in enumerate(ranked[:50])
        ]
    )
    prompt = resolve_prompt(
        "source_curation",
        research_topic=research_topic,
        sources=sources_text,
        max_sources=min(max_sources, len(ranked)),
    )
    model_config = build_model_config(
        model=research_config.smart_llm,
        max_tokens=2048,
        tags=["langsmith:nostream"],
    )

    try:
        response = await configurable_model.with_config(model_config).ainvoke([
            HumanMessage(content=prompt)
        ])
        import json
        import re

        content = response.content if hasattr(response, "content") else str(response)
        json_match = re.search(r"\[[\s\S]*\]", content)
        if json_match:
            curated = json.loads(json_match.group(0))
            if isinstance(curated, list) and curated:
                merged = _merge_ranked_sources(ranked, curated, max_sources=max_sources)
                return merged[:max_sources]
    except Exception as e:
        logger.warning(f"[CurateSources] LLM curation failed: {e}")

    return ranked[:max_sources]


def _rank_sources_hybrid(
    research_topic: str,
    sources: list[dict],
    *,
    max_sources: int,
) -> list[dict]:
    from agent.memory.retrieval import tokenize
    from agent.workflows.source_registry import SourceRegistry

    registry = SourceRegistry()
    topic_tokens = tokenize(research_topic)
    ranked: list[tuple[float, dict[str, Any]]] = []
    for index, source in enumerate(sources, 1):
        if not isinstance(source, dict):
            continue
        url = str(source.get("url") or source.get("source_url") or "").strip()
        title = str(source.get("title") or source.get("name") or url or "").strip()
        snippet = str(source.get("snippet") or source.get("summary") or source.get("content") or title)
        record = registry.register(url=url, title=title) if url else None
        canonical_url = record.canonical_url if record else url
        domain = str(source.get("domain") or (record.domain if record else "") or "").lower()
        source_tokens = tokenize(f"{title} {snippet} {canonical_url}")
        relevance = 0.0
        if topic_tokens and source_tokens:
            overlap = len(topic_tokens & source_tokens)
            relevance = overlap / max(1, min(len(topic_tokens), len(source_tokens)))
        authority = _source_authority_score(domain, canonical_url)
        recency = _source_recency_score(source)
        coverage = _source_coverage_score(source)
        quality = _source_quality_score(source)
        hybrid = (
            relevance * 0.34
            + authority * 0.24
            + recency * 0.18
            + coverage * 0.14
            + quality * 0.10
        )
        ranked.append(
            (
                hybrid,
                {
                    **source,
                    "id": source.get("id") or source.get("source_id") or f"source_{index}",
                    "source_id": source.get("source_id") or (record.source_id if record else ""),
                    "url": canonical_url or url,
                    "canonical_url": canonical_url or url,
                    "title": title,
                    "domain": domain,
                    "relevance_score": round(relevance, 4),
                    "authority_score": round(authority, 4),
                    "recency_score": round(recency, 4),
                    "coverage_score": round(coverage, 4),
                    "quality_score": round(quality, 4),
                },
            )
        )
    ranked.sort(key=lambda item: (-item[0], str(item[1].get("title") or "").lower()))
    return [item[1] for item in ranked[:max_sources]]


def _merge_ranked_sources(
    ranked: list[dict[str, Any]],
    curated: list[dict[str, Any]],
    *,
    max_sources: int,
) -> list[dict[str, Any]]:
    if not curated:
        return ranked[:max_sources]
    by_key: dict[str, dict[str, Any]] = {}
    for source in ranked:
        key = _source_key(source)
        if key:
            by_key[key] = source
    merged: list[dict[str, Any]] = []
    for item in curated:
        if not isinstance(item, dict):
            continue
        key = _source_key(item)
        if key and key in by_key:
            merged.append({**by_key[key], **item})
        else:
            merged.append(item)
    if len(merged) < max_sources:
        for source in ranked:
            key = _source_key(source)
            if key and any(_source_key(item) == key for item in merged):
                continue
            merged.append(source)
            if len(merged) >= max_sources:
                break
    return merged


def _source_key(source: dict[str, Any]) -> str:
    return str(
        source.get("source_id")
        or source.get("id")
        or source.get("canonical_url")
        or source.get("url")
        or ""
    ).strip().lower()


def _source_authority_score(domain: str, url: str) -> float:
    text = f"{domain} {url}".lower()
    if any(token in text for token in ("arxiv.org", "pubmed", "doi.org", "nature.com", "science.org", "acm.org", "ieee.org")):
        return 1.0
    if any(token in text for token in ("wikipedia.org", "medium.com", "substack.com")):
        return 0.3
    if any(token in text for token in (".gov", ".edu", ".ac.uk", ".org")):
        return 0.85
    return 0.55


def _source_recency_score(source: dict[str, Any]) -> float:
    raw = str(
        source.get("publishedDate")
        or source.get("published_date")
        or source.get("retrieved_at")
        or ""
    ).strip()
    if not raw:
        return 0.5
    for candidate in (raw, raw.replace("Z", "+00:00")):
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            age_days = max(0.0, (datetime.now(UTC) - dt).total_seconds() / 86400.0)
            return 1.0 / (1.0 + age_days / 30.0)
        except Exception:
            continue
    return 0.5


def _source_coverage_score(source: dict[str, Any]) -> float:
    text = " ".join(
        str(source.get(key) or "").strip()
        for key in ("snippet", "summary", "content", "description", "title")
    )
    if len(text) >= 800:
        return 1.0
    if len(text) >= 300:
        return 0.8
    if len(text) >= 120:
        return 0.6
    if text:
        return 0.4
    return 0.0


def _source_quality_score(source: dict[str, Any]) -> float:
    scores = []
    for key in ("relevance_score", "authority_score", "coverage_score", "quality_score"):
        try:
            value = float(source.get(key))
            scores.append(max(0.0, min(1.0, value)))
        except (TypeError, ValueError):
            continue
    if scores:
        return sum(scores) / len(scores)
    return 0.5
