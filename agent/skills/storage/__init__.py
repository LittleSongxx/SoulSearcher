"""SkillStorage singleton factory.

Mirrors the pattern used by sandbox_provider.py.
"""

from __future__ import annotations

from agent.skills.storage.local_skill_storage import LocalSkillStorage
from agent.skills.storage.skill_storage import SkillStorage

_default_skill_storage: SkillStorage | None = None


def get_or_new_skill_storage(skills_path: str | None = None) -> SkillStorage:
    """Return a SkillStorage instance — singleton or explicit-path instance.

    When ``skills_path`` is provided, creates a new instance (never cached).
    Otherwise returns the process singleton, creating it on first call.
    """
    global _default_skill_storage

    if skills_path is not None:
        return LocalSkillStorage(host_path=str(skills_path))

    if _default_skill_storage is None:
        from common.config import settings

        public_dir = getattr(settings, "skills_public_dir", "skills/public")
        _default_skill_storage = LocalSkillStorage(host_path=str(public_dir).replace("/public", ""))

    return _default_skill_storage


def get_skill_storage() -> SkillStorage:
    """Return the process singleton skill storage."""
    return get_or_new_skill_storage()


def reset_skill_storage() -> None:
    """Clear the cached singleton (used in tests and hot-reload scenarios)."""
    global _default_skill_storage
    _default_skill_storage = None


__all__ = [
    "LocalSkillStorage",
    "SkillStorage",
    "get_or_new_skill_storage",
    "get_skill_storage",
    "reset_skill_storage",
]
