from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from common.skills_loader import SkillProfile, load_all_skills


@dataclass
class SelectedSkillContext:
    skill_id: str
    name: str
    reason: str
    category: str = ""
    mode: str = ""
    tags: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    system_prompt: str = ""

    def to_dict(self, *, include_prompt: bool = False) -> dict[str, Any]:
        payload = asdict(self)
        if not include_prompt:
            payload.pop("system_prompt", None)
        return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def select_research_skills(
    *,
    query: str,
    research_brief: Any = None,
    config: Optional[dict[str, Any]] = None,
    max_skills: int = 3,
) -> list[SelectedSkillContext]:
    cfg = _configurable(config or {})
    explicit_ids = _string_list(
        cfg.get("skill_ids")
        or cfg.get("deepsearch_skill_ids")
        or _brief_value(research_brief, "skill_ids")
    )
    explicit_tags = set(_string_list(cfg.get("skill_tags") or cfg.get("deepsearch_skill_tags")))
    mode = str(cfg.get("route") or cfg.get("resolved_route") or "deep").strip().lower() or "deep"
    skills = load_all_skills(use_cache=True)
    ranked: list[tuple[int, str, SkillProfile]] = []
    for skill in skills:
        if skill.mode not in {mode, "deep", "agent"}:
            continue
        score, reason = _score_skill(skill, query=query, explicit_ids=explicit_ids, explicit_tags=explicit_tags)
        if score <= 0:
            continue
        ranked.append((score, reason, skill))
    ranked.sort(key=lambda item: (-item[0], item[2].id))
    selected: list[SelectedSkillContext] = []
    seen = set()
    for _score, reason, skill in ranked:
        if skill.id in seen:
            continue
        seen.add(skill.id)
        selected.append(
            SelectedSkillContext(
                skill_id=skill.id,
                name=skill.name,
                reason=reason,
                category=skill.category,
                mode=skill.mode,
                tags=list(skill.tags),
                tools=list(skill.tools),
                system_prompt=skill.system_prompt,
            )
        )
        if len(selected) >= max(1, int(max_skills or 1)):
            break
    return selected


def format_skill_context(skills: list[SelectedSkillContext], *, max_chars: int = 12000) -> str:
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
    skill: SkillProfile,
    *,
    query: str,
    explicit_ids: list[str],
    explicit_tags: set[str],
) -> tuple[int, str]:
    if explicit_ids and skill.id in explicit_ids:
        return 100, "explicit skill id"
    score = 0
    reasons: list[str] = []
    query_l = str(query or "").lower()
    text = " ".join(
        [skill.id, skill.name, skill.description, skill.description_en, " ".join(skill.tags)]
        + list(skill.example_queries or [])
    ).lower()
    if explicit_tags and explicit_tags.intersection(set(skill.tags)):
        score += 60
        reasons.append("matched requested tags")
    if skill.category == "research":
        score += 15
        reasons.append("research category")
    for token in _query_terms(query_l):
        if len(token) >= 3 and token in text:
            score += 8
    if any(example.lower() in query_l or query_l in example.lower() for example in skill.example_queries or []):
        score += 30
        reasons.append("matched example query")
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
