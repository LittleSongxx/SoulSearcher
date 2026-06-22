"""Slash skill activation helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import os

from common.config import settings


@dataclass(frozen=True)
class SlashSkillActivation:
    skill_name: str
    remaining_text: str
    skill_file: Path
    content: str


_SLASH_RE = re.compile(r"^/([A-Za-z0-9_.-]+)(?:\s+([\s\S]*))?$")


def resolve_slash_skill(text: str, active_skill_ids: list[str] | None = None) -> SlashSkillActivation | None:
    if not getattr(settings, "slash_skill_activation_enabled", True):
        return None
    match = _SLASH_RE.match(str(text or "").strip())
    if not match:
        return None
    name = match.group(1).strip()
    remaining = (match.group(2) or "").strip()
    if active_skill_ids is not None and name not in set(active_skill_ids):
        active_skill_ids.append(name)

    repo_root = Path(__file__).resolve().parents[2]
    skills_root = Path(
        os.environ.get("WEAVER_SKILLS_PATH", "")
        or repo_root / "skills" / "public"
    ).resolve()
    candidates = [
        skills_root / name / "SKILL.md",
        repo_root / "skills" / "custom" / name / "SKILL.md",
    ]
    for skill_file in candidates:
        try:
            resolved = skill_file.resolve()
            if not resolved.is_file():
                continue
            content = resolved.read_text(encoding="utf-8")
            return SlashSkillActivation(
                skill_name=name,
                remaining_text=remaining,
                skill_file=resolved,
                content=content,
            )
        except OSError:
            continue
    return None


def build_slash_skill_context(activation: SlashSkillActivation) -> str:
    task_text = activation.remaining_text or "No task text was provided after the slash skill command."
    return (
        "<slash_skill_activation>\n"
        f"The user explicitly activated /{activation.skill_name}. Treat the task text as:\n"
        f"<user_request>{task_text}</user_request>\n\n"
        f"<skill name=\"{activation.skill_name}\" path=\"{activation.skill_file}\">\n"
        f"{activation.content}\n"
        "</skill>\n"
        "</slash_skill_activation>"
    )
