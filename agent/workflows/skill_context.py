from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from agent.skills.types import Skill


@dataclass
class SelectedSkillContext:
    skill_id: str
    name: str
    reason: str
    category: str = ""
    tags: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    system_prompt: str = ""

    def to_dict(self, *, include_prompt: bool = False) -> dict[str, Any]:
        payload = asdict(self)
        if not include_prompt:
            payload.pop("system_prompt", None)
        return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def _load_skill_body(skill: Skill) -> str:
    """Read the SKILL.md body (content after YAML frontmatter)."""
    try:
        content = skill.skill_file.read_text(encoding="utf-8")
        # Strip YAML frontmatter
        parts = content.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
        return content.strip()
    except Exception:
        return ""


def select_research_skills(
    *,
    query: str,
    research_brief: Any = None,
    config: Optional[dict[str, Any]] = None,
    max_skills: int = 3,
) -> list[SelectedSkillContext]:
    """Select the most relevant skills for a research query using keyword scoring."""
    from agent.skills.storage import get_or_new_skill_storage

    cfg = _configurable(config or {})
    explicit_ids = _string_list(
        cfg.get("skill_ids")
        or cfg.get("deepsearch_skill_ids")
        or _brief_value(research_brief, "skill_ids")
    )
    explicit_tags = set(_string_list(cfg.get("skill_tags") or cfg.get("deepsearch_skill_tags")))

    try:
        storage = get_or_new_skill_storage()
        skills = storage.load_skills(enabled_only=True)
    except Exception:
        return []

    ranked: list[tuple[int, str, Skill]] = []
    for skill in skills:
        score, reason = _score_skill(skill, query=query, explicit_ids=explicit_ids)
        if score <= 0:
            continue
        ranked.append((score, reason, skill))

    ranked.sort(key=lambda item: (-item[0], item[2].name))

    selected: list[SelectedSkillContext] = []
    seen: set[str] = set()
    for _score, reason, skill in ranked:
        if skill.name in seen:
            continue
        seen.add(skill.name)
        body = _load_skill_body(skill)
        selected.append(
            SelectedSkillContext(
                skill_id=skill.name,
                name=skill.name,
                reason=reason,
                category=skill.category.value if skill.category else "",
                tools=list(skill.allowed_tools or []),
                system_prompt=body,
            )
        )
        if len(selected) >= max(1, int(max_skills or 1)):
            break
    return selected


def format_skill_context(skills: list[SelectedSkillContext], *, max_chars: int = 12000) -> str:
    """Format selected skills as <skill> XML blocks for system prompt injection."""
    blocks: list[str] = []
    used = 0
    for skill in skills or []:
        body = skill.system_prompt.strip()
        if not body:
            continue
        block = f'<skill id="{skill.skill_id}" name="{skill.name}">\n{body}\n</skill>'
        remaining = max_chars - used
        if remaining <= 0:
            break
        if len(block) > remaining:
            block = block[: max(0, remaining - 14)] + "\n</skill>"
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks)


def _score_skill(
    skill: Skill,
    *,
    query: str,
    explicit_ids: list[str],
) -> tuple[int, str]:
    if explicit_ids and skill.name in explicit_ids:
        return 100, "explicit skill id"

    score = 0
    reasons: list[str] = []
    query_l = str(query or "").lower()

    # Build searchable text from skill metadata
    text_parts = [skill.name, skill.description]
    text = " ".join(text_parts).lower()

    # Research skills get a baseline boost
    desc_lower = skill.description.lower()
    if any(kw in desc_lower for kw in ["research", "search", "analysis", "analyze"]):
        score += 15
        reasons.append("research-related")

    # Token matching
    for token in _query_terms(query_l):
        if len(token) >= 3 and token in text:
            score += 8

    return score, ", ".join(reasons) or "matched query context"


def _query_terms(query: str) -> list[str]:
    separators = [",", ".", ";", ":", "?", "!", "，", "。", "；", "：", "？", "！", "\n", "\t"]
    text = query
    for sep in separators:
        text = text.replace(sep, " ")
    return [part.strip() for part in text.split() if part.strip()]


def _configurable(config: dict[str, Any]) -> dict[str, Any]:
    cfg = config.get("configurable") if isinstance(config, dict) else {}
    return cfg if isinstance(cfg, dict) else {}


def _brief_value(research_brief: Any, key: str) -> Any:
    if isinstance(research_brief, dict):
        return research_brief.get(key)
    return getattr(research_brief, key, None)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list):
        return [str(part).strip() for part in value if str(part).strip()]
    return []
