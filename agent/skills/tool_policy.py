from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol, TypeVar

from agent.skills.types import Skill

if TYPE_CHECKING:
    from typing import Any

logger = logging.getLogger(__name__)

ToolT = TypeVar("ToolT", bound="NamedTool")


class NamedTool(Protocol):
    name: str


def allowed_tool_names_for_skills(skills: list[Skill]) -> set[str] | None:
    """Return the union of explicit skill allowed-tools declarations.

    None means legacy allow-all behavior. It is returned only when no loaded
    skill declares allowed-tools. Once any skill declares the field, legacy
    skills without the field contribute no tools instead of disabling the
    explicit restrictions from other skills.
    """
    if not skills:
        return None

    allowed: set[str] = set()
    has_explicit_declaration = False
    for skill in skills:
        if skill.allowed_tools is None:
            continue
        has_explicit_declaration = True
        if not skill.allowed_tools:
            logger.info("Skill %s declared empty allowed-tools", skill.name)
        allowed.update(skill.allowed_tools)

    if not has_explicit_declaration:
        return None
    return allowed


def filter_tools_by_skill_allowed_tools(tools: list, skills: list[Skill]) -> list:
    """Filter a tool list to only those allowed by loaded skills."""
    allowed = allowed_tool_names_for_skills(skills)
    if allowed is None:
        return tools
    return [tool for tool in tools if tool.name in allowed]
