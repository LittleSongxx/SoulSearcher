"""
Skills Loader — parse Markdown skill files from the skills/ directory.

Each skill is a .md file with YAML frontmatter (metadata) and a Markdown body
(used as the system_prompt for the LLM).

Example file (skills/code-assistant.md):

    ---
    id: code-assistant
    name: 代码助手
    name_en: Code Assistant
    ...
    ---
    You are a professional code assistant...
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_CACHE: Optional[List["SkillProfile"]] = None


@dataclass
class SkillProfile:
    """Parsed representation of a single skill .md file."""

    id: str
    name: str
    name_en: str = ""
    description: str = ""
    description_en: str = ""
    icon: str = "⚡"
    category: str = "tool"  # research | code | writing | data | creative | tool
    mode: str = "agent"  # direct | agent | deep
    tools: List[str] = field(default_factory=list)
    example_queries: List[str] = field(default_factory=list)
    is_preset: bool = True
    system_prompt: str = ""
    # Source file path (for debugging)
    _source: str = ""

    # ---- helpers used by the backend ----

    def to_enabled_tools(self) -> Dict[str, bool]:
        """Convert the tools list to an ``enabled_tools`` dict compatible with AgentProfile."""
        return {tool: True for tool in self.tools}

    def to_agent_profile_dict(self) -> Dict[str, Any]:
        """Return a dict that can be used as ``configurable.agent_profile``."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "model": "",
            "enabled_tools": self.to_enabled_tools(),
            "mcp_servers": None,
            "metadata": {"skill": True, "category": self.category},
        }

    def to_summary_dict(self) -> Dict[str, Any]:
        """Metadata-only dict for the listing API (no full prompt)."""
        return {
            "id": self.id,
            "name": self.name,
            "name_en": self.name_en,
            "description": self.description,
            "description_en": self.description_en,
            "icon": self.icon,
            "category": self.category,
            "mode": self.mode,
            "tools": self.tools,
            "example_queries": self.example_queries,
            "is_preset": self.is_preset,
        }

    def to_full_dict(self) -> Dict[str, Any]:
        """Full dict including system_prompt."""
        d = self.to_summary_dict()
        d["system_prompt"] = self.system_prompt
        return d


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_skill_file(path: Path) -> Optional[SkillProfile]:
    """Parse a single .md skill file into a SkillProfile."""
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to read skill file %s: %s", path, exc)
        return None

    # Split YAML frontmatter from body
    if not raw.startswith("---"):
        logger.warning("Skill file %s missing YAML frontmatter (no leading ---)", path)
        return None

    parts = raw.split("---", 2)
    if len(parts) < 3:
        logger.warning("Skill file %s has malformed frontmatter", path)
        return None

    frontmatter_str = parts[1].strip()
    body = parts[2].strip()

    try:
        meta = yaml.safe_load(frontmatter_str)
    except yaml.YAMLError as exc:
        logger.warning("Skill file %s has invalid YAML: %s", path, exc)
        return None

    if not isinstance(meta, dict):
        logger.warning("Skill file %s frontmatter is not a mapping", path)
        return None

    skill_id = meta.get("id") or path.stem
    return SkillProfile(
        id=str(skill_id),
        name=str(meta.get("name") or skill_id),
        name_en=str(meta.get("name_en") or ""),
        description=str(meta.get("description") or ""),
        description_en=str(meta.get("description_en") or ""),
        icon=str(meta.get("icon") or "⚡"),
        category=str(meta.get("category") or "tool"),
        mode=str(meta.get("mode") or "agent"),
        tools=list(meta.get("tools") or []),
        example_queries=list(meta.get("example_queries") or []),
        is_preset=bool(meta.get("is_preset", True)),
        system_prompt=body,
        _source=str(path),
    )


def _default_skills_dir() -> Path:
    """Return the default skills/ directory at the project root."""
    return Path(__file__).resolve().parents[1] / "skills"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_all_skills(
    skills_dir: Optional[Path] = None,
    *,
    use_cache: bool = True,
) -> List[SkillProfile]:
    """
    Load and return all skill profiles from the skills/ directory.

    Results are cached in-memory after the first call.  Pass
    ``use_cache=False`` or call ``reload_skills()`` to refresh.
    """
    global _CACHE

    if use_cache and _CACHE is not None:
        return list(_CACHE)

    directory = skills_dir or _default_skills_dir()
    if not directory.is_dir():
        logger.info("Skills directory %s does not exist; returning empty list", directory)
        return []

    skills: List[SkillProfile] = []
    for md_file in sorted(directory.glob("*.md")):
        skill = _parse_skill_file(md_file)
        if skill is not None:
            skills.append(skill)

    with _LOCK:
        _CACHE = list(skills)

    logger.info("Loaded %d skills from %s", len(skills), directory)
    return skills


def get_skill(skill_id: str) -> Optional[SkillProfile]:
    """Return a single skill by ID, or None."""
    for s in load_all_skills():
        if s.id == skill_id:
            return s
    return None


def reload_skills(skills_dir: Optional[Path] = None) -> List[SkillProfile]:
    """Force-reload skills from disk (clears cache)."""
    global _CACHE
    with _LOCK:
        _CACHE = None
    return load_all_skills(skills_dir, use_cache=False)
