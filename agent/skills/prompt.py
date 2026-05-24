"""Skills system-prompt section builder.

Generates the <available_skills> block for the agent system prompt,
following DeerFlow's progressive loading pattern.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_SKILLS_PROMPT_CACHE: tuple[tuple, str] | None = None


def _build_skill_evolution_section(enabled: bool) -> str:
    """Build the skill evolution prompt section when agents can create/modify skills."""
    if not enabled:
        return ""
    return """
<skill_evolution>
You can create and improve skills to capture reusable workflows. After completing a
complex task, consider whether a new or updated skill would help future runs.

- Use the `skill_manage` tool to create, edit, or patch skills under skills/custom/.
- Prefer `patch` over `edit` to make targeted changes to existing skills.
- Confirm with the user before creating a new skill (unless they explicitly requested it).
</skill_evolution>"""


def get_skills_prompt_section(available_skills: set[str] | None = None, container_base_path: str = "/mnt/skills") -> str:
    """Generate the skills prompt section with available skills list.

    Args:
        available_skills: Optional set of skill names to include (None = all enabled).
        container_base_path: Base path where skills are mounted in the container.

    Returns:
        The rendered <skill_system> XML block, or empty string if no skills.
    """
    from agent.skills.storage import get_or_new_skill_storage

    global _SKILLS_PROMPT_CACHE

    try:
        storage = get_or_new_skill_storage()
        skills = storage.load_skills(enabled_only=True)
    except Exception:
        logger.warning("Failed to load skills for prompt section", exc_info=True)
        return ""

    if not skills:
        return ""

    if available_skills is not None:
        skills = [s for s in skills if s.name in available_skills]
        if not skills:
            return ""

    # Build cache key from skill signatures
    skill_signature = tuple(
        (s.name, s.description, s.category.value, s.get_container_file_path(container_base_path))
        for s in skills
    )
    available_key = tuple(sorted(available_skills)) if available_skills is not None else None
    cache_key = (skill_signature, available_key)

    if _SKILLS_PROMPT_CACHE is not None and _SKILLS_PROMPT_CACHE[0] == cache_key:
        return _SKILLS_PROMPT_CACHE[1]

    # Check if skill_evolution is enabled
    from common.config import settings
    skill_evolution_enabled = getattr(settings, "skill_evolution_enabled", False)
    skill_evolution_section = _build_skill_evolution_section(skill_evolution_enabled)

    # Build the available skills list
    skills_xml_parts = []
    for skill in skills:
        editability = "[built-in]" if skill.category.value == "public" else "[custom, editable]"
        skills_xml_parts.append(
            f"    <skill>\n"
            f"        <name>{skill.name}</name>\n"
            f"        <description>{skill.description} {editability}</description>\n"
            f"        <location>{skill.get_container_file_path(container_base_path)}</location>\n"
            f"    </skill>"
        )

    skills_list = "\n".join(skills_xml_parts)

    result = f"""<skill_system>
You have access to skills that provide optimized workflows for specific tasks.

**Progressive Loading Pattern:**
1. When a user query matches a skill's use case, immediately call `read_file` on the skill's main file using the path attribute
2. Read and understand the skill's workflow and instructions
3. The skill file contains references to external resources under the same folder
4. Load referenced resources only when needed during execution
5. Follow the skill's instructions precisely

**Skills are located at:** {container_base_path}

<available_skills>
{skills_list}
</available_skills>{skill_evolution_section}
</skill_system>"""

    _SKILLS_PROMPT_CACHE = (cache_key, result)
    return result


# ---------------------------------------------------------------------------
# Skill context builders for different pipeline phases
# ---------------------------------------------------------------------------

_RESEARCH_SECTIONS = [
    "## When to Use",
    "## Core Principle",
    "## Research Methodology",
]

_WRITING_SECTIONS = [
    "## Output Format",
    "## Writing Guidelines",
    "## Report Structure Template",
    "## Formatting & Tone Standards",
    "## Script Writing Guidelines",
    "## Style-Specific Guidelines",
    "## Newsletter Output Template",
    "## Review Output Template",
    "## Supported Output Formats",
    "## Output",
]


def build_skill_context(skill_ids: list[str], purpose: str = "research") -> str:
    """Build skill context string for injection into prompts.

    Args:
        skill_ids: List of skill names to include.
        purpose: "research" to extract methodology sections,
                 "writing" to extract output/writing sections.

    Returns:
        Formatted XML string for prompt injection, or "" if no skills matched.
    """
    if not skill_ids:
        return ""

    import os
    from pathlib import Path

    from agent.skills.parser import parse_skill_file
    from agent.skills.types import SkillCategory

    sections = _RESEARCH_SECTIONS if purpose == "research" else _WRITING_SECTIONS
    tag = "Skill Guidance" if purpose == "research" else "Skill Writing Guidance"
    instruction = (
        "Apply the methodology from these skills during research."
        if purpose == "research"
        else "Apply the writing and formatting guidelines from these skills to the final output."
    )

    skills_base = os.environ.get(
        "WEAVER_SKILLS_PATH",
        os.path.join(os.path.dirname(__file__), "..", "..", "skills", "public"),
    )
    skills_base = os.path.abspath(skills_base)

    if not os.path.isdir(skills_base):
        return ""

    parts = [f"\n\n<{tag}>\nThe following skills are active:\n"]
    loaded = 0

    for entry in sorted(os.listdir(skills_base)):
        entry_path = os.path.join(skills_base, entry)
        if not os.path.isdir(entry_path):
            continue

        if entry not in skill_ids:
            continue

        skill_file = os.path.join(entry_path, "SKILL.md")
        if not os.path.isfile(skill_file):
            continue

        try:
            skill = parse_skill_file(Path(skill_file), SkillCategory.PUBLIC, Path(entry_path))
            if not skill:
                continue

            parts.append(f"- **{skill.name}**: {skill.description}")
            loaded += 1

            content = open(skill_file, "r", encoding="utf-8").read()
            for section in sections:
                section_start = content.find(section)
                if section_start >= 0:
                    next_heading = content.find("\n## ", section_start + len(section) + 1)
                    section_text = (
                        content[section_start:next_heading]
                        if next_heading > 0
                        else content[section_start:2000]
                    )
                    if len(section_text) > 20:
                        parts.append(f"  {section_text[:800]}...\n")
                        break
        except Exception:
            parts.append(f"- Skill: {entry}")

    if loaded == 0:
        return ""

    parts.append(f"\n{instruction}\n")
    parts.append(f"</{tag}>\n")
    return "\n".join(parts)


def clear_skills_prompt_cache() -> None:
    """Clear the skills prompt cache (call when skills change)."""
    global _SKILLS_PROMPT_CACHE
    _SKILLS_PROMPT_CACHE = None


async def refresh_skills_system_prompt_cache_async() -> None:
    """Async wrapper to clear the skills prompt cache."""
    clear_skills_prompt_cache()
