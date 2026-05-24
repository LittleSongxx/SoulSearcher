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
from datetime import datetime

from langchain_core.messages import AIMessage, HumanMessage, get_buffer_string
from langchain_core.runnables import RunnableConfig

from agent.core.configuration import ResearchConfiguration
from agent.core.model_routing import configurable_model
from agent.core.prompts import (
    HTML_REPORT_CSS_TEMPLATE,
    resolve_prompt,
)
from agent.core.state import AgentState

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
    model_config = {
        "model": model_name,
        "max_tokens": max_tokens,
        "tags": ["langsmith:nostream"],
    }

    messages = state.get("messages", [])
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
    skill_writing_context = build_skill_context(skill_ids, purpose="writing")

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
                    messages=get_buffer_string(messages),
                    findings=findings_truncated,
                    date=current_date,
                    skill_writing_context=skill_writing_context,
                )
            else:
                prompt = resolve_prompt(prompt_name,
                    research_brief=research_brief,
                    messages=get_buffer_string(messages),
                    findings=findings_truncated,
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
            tracker = get_token_tracker()
            usage = getattr(response, "usage_metadata", None) or {}
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            if input_tokens or output_tokens:
                tracker.record("report", input_tokens, output_tokens)

            final_content = response.content

            # === HTML Post-processing: Inject images ===
            if report_format == "html" and embed_images:
                configurable = config.get("configurable", {})
                viewed_images = configurable.get("viewed_images", {})
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
            # Skip quality check for HTML reports (different evaluation criteria)
            if report_format != "html":
                try:
                    from agent.workflows.quality_check import (
                        generate_report_with_quality_check,
                    )
                    final_content = await generate_report_with_quality_check(
                        state=state,
                        report_content=final_content,
                        config=config,
                        max_revisions=research_config.max_report_revisions,
                    )
                except ImportError:
                    logger.debug("[Report] Quality check not available")
                except Exception as e:
                    logger.warning(f"[Report] Quality check skipped: {e}")

            # === Memory Update ===
            from agent.core.middleware import get_memory_middleware
            memory_mw = get_memory_middleware()
            user_id = config.get("configurable", {}).get("user_id", "default")
            memory_content = final_content
            if report_format == "html":
                import re
                memory_content = re.sub(
                    r"<[^>]+>", "", final_content[:10000]
                )
            try:
                await memory_mw.update_memory(
                    user_id=user_id,
                    query=research_brief[:500] if research_brief else "",
                    findings=memory_content[:5000],
                    facts=[n[:200] for n in notes[:10] if n],
                )
                logger.info("[Report] Memory updated for user '%s'", user_id)
            except Exception as e:
                logger.warning(f"[Report] Memory update failed: {e}")

            # === Level 2 Dev Evaluation (rubric-based, with legacy fallback) ===
            l2_result = None
            try:
                from agent.workflows.rubric import run_level2_rubric
                eval_content = final_content
                if report_format == "html":
                    import re
                    eval_content = re.sub(
                        r"<[^>]+>", "", final_content[:20000]
                    )
                l2_result = await run_level2_rubric(
                    report=eval_content,
                    research_brief=research_brief,
                    topic=research_brief[:500] if research_brief else "",
                    config=config,
                )
                logger.info(
                    f"[Report] Level 2 rubric eval: score={l2_result.overall_score:.2f}, "
                    f"passed={l2_result.passed}, verdict={l2_result.verdict}"
                )
            except ImportError:
                # Fall back to legacy prompt-based L2 evaluation
                try:
                    from agent.workflows.evaluation import run_level2_evaluation
                    eval_content = final_content
                    if report_format == "html":
                        import re
                        eval_content = re.sub(
                            r"<[^>]+>", "", final_content[:20000]
                        )
                    eval_result = await run_level2_evaluation(
                        report=eval_content,
                        research_brief=research_brief,
                        topic=research_brief[:500] if research_brief else "",
                        model_name=research_config.smart_llm,
                    )
                    logger.info(
                        f"[Report] Level 2 eval: score={eval_result.overall_score:.2f}, "
                        f"passed={eval_result.overall_passed}"
                    )
                except ImportError:
                    logger.debug("[Report] Evaluation system not available")
                except Exception as e:
                    logger.warning(f"[Report] Level 2 evaluation failed: {e}")
            except Exception as e:
                logger.warning(f"[Report] Level 2 rubric evaluation failed: {e}")

            # === Level 3 Deep Evaluation (strategic_llm, for deep complexity only) ===
            if complexity == "deep":
                try:
                    from agent.workflows.evaluation import run_level3_evaluation
                    eval_content = final_content
                    if report_format == "html":
                        import re
                        eval_content = re.sub(
                            r"<[^>]+>", "", final_content[:20000]
                        )
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

            return {
                "final_report": final_content,
                "messages": [AIMessage(content=final_content)],
                "report_format": report_format,
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
                    **cleared_state,
                }

    # All retries exhausted
    logger.error("[Report] Maximum retries exhausted")
    return {
        "final_report": "Error: Report generation failed after maximum retries.",
        "messages": [AIMessage(content="Report generation failed after maximum retries.")],
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

    # Format sources for the prompt
    sources_text = "\n".join([
        f"{i+1}. {s.get('title', 'Unknown')}: {s.get('url', '')}"
        for i, s in enumerate(sources[:50])  # Limit to 50 for prompt size
    ])

    prompt = resolve_prompt("source_curation",
        research_topic=research_topic,
        sources=sources_text,
        max_sources=min(max_sources, len(sources)),
    )

    model_config = {
        "model": research_config.smart_llm,
        "max_tokens": 2048,
        "tags": ["langsmith:nostream"],
    }

    try:
        response = await configurable_model.with_config(model_config).ainvoke([
            HumanMessage(content=prompt)
        ])

        # Parse JSON array from response
        import json
        import re

        content = response.content if hasattr(response, "content") else str(response)
        json_match = re.search(r"\[[\s\S]*\]", content)
        if json_match:
            curated = json.loads(json_match.group(0))
            return curated[:max_sources]

    except Exception as e:
        logger.warning(f"[CurateSources] LLM curation failed: {e}")

    # Fallback: return sources as-is
    return sources[:max_sources]
