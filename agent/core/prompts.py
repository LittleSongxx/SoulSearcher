"""Prompt templates for fixed-role vertical industry research."""

from __future__ import annotations

from pathlib import Path

CLARIFY_WITH_USER_PROMPT = """Decide whether the user query needs clarification before industry research starts."""
WRITE_RESEARCH_BRIEF_PROMPT = """Convert the user query into a concise industry, market, company, policy, or technology research brief."""
LEAD_RESEARCHER_PROMPT = """You are ResearchArchitect in a fixed-role vertical research pipeline. Produce section tasks, evidence requirements, required metrics, and source priorities."""
RESEARCHER_PROMPT = """You are SourceScout for vertical industry research. Gather authority-ranked sources for the assigned section."""
COMPRESS_RESEARCH_PROMPT = """Normalize collected evidence into concise ledger-ready passages without introducing unsupported claims."""
FINAL_REPORT_PROMPT = """Write the final vertical research report only from evidence ledger items and extracted datapoints."""
SOURCE_CURATION_PROMPT = """Rank sources by authority, freshness, corroboration value, and section coverage."""

_PROMPTS = {
    "clarify_with_user": CLARIFY_WITH_USER_PROMPT,
    "write_research_brief": WRITE_RESEARCH_BRIEF_PROMPT,
    "research_architect": LEAD_RESEARCHER_PROMPT,
    "source_scout": RESEARCHER_PROMPT,
    "compress_research": COMPRESS_RESEARCH_PROMPT,
    "final_report": FINAL_REPORT_PROMPT,
    "source_curation": SOURCE_CURATION_PROMPT,
}


def resolve_prompt(name: str, fallback: str | None = None) -> str:
    """Resolve a prompt by name from local markdown files or built-in defaults."""
    prompt_dir = Path(__file__).resolve().parents[1] / "prompts" / "industry_research"
    path = prompt_dir / f"{name}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return _PROMPTS.get(name, fallback or "")
