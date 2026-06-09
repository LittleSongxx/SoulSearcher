"""Memory storage with per-user isolation."""

from __future__ import annotations

import json
import logging
import os
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from common.config import settings

logger = logging.getLogger(__name__)

DEFAULT_USER_ID = "default"


def _empty_profile() -> dict[str, Any]:
    return {
        "role": "",
        "expertise": "",
        "preferences": {},
        "preferredSources": [],
        "language": "",
        "format": "",
        "verbosity": "",
        "researchGoals": [],
    }


def create_empty_memory() -> dict[str, Any]:
    return {
        "profile": _empty_profile(),
        "user": {
            "workContext": {"summary": "", "updatedAt": ""},
            "personalContext": {"summary": "", "updatedAt": ""},
            "topOfMind": {"summary": "", "updatedAt": ""},
        },
        "history": {
            "recentMonths": {"summary": "", "updatedAt": ""},
            "earlierContext": {"summary": "", "updatedAt": ""},
            "longTermBackground": {"summary": "", "updatedAt": ""},
        },
        "facts": [],
    }


def normalize_memory_data(data: dict[str, Any] | None) -> dict[str, Any]:
    """Return memory data with all expected structured sections present."""
    normalized = create_empty_memory()
    if not isinstance(data, dict):
        return normalized

    profile = data.get("profile")
    if isinstance(profile, dict):
        merged_profile = dict(normalized["profile"])
        for key in merged_profile:
            value = profile.get(key)
            if key == "preferences" and isinstance(value, dict):
                merged_profile[key] = {
                    str(k): str(v) for k, v in value.items() if str(k).strip()
                }
            elif key in {"preferredSources", "researchGoals"} and isinstance(value, list):
                merged_profile[key] = [str(v) for v in value if str(v).strip()]
            elif isinstance(value, str):
                merged_profile[key] = value
        normalized["profile"] = merged_profile

    for section_name in ("user", "history"):
        section = data.get(section_name)
        if not isinstance(section, dict):
            continue
        for key, default_value in normalized[section_name].items():
            value = section.get(key)
            if isinstance(value, dict):
                normalized[section_name][key] = {
                    "summary": str(value.get("summary", "") or ""),
                    "updatedAt": str(value.get("updatedAt", "") or ""),
                }
            elif isinstance(value, str):
                normalized[section_name][key] = {
                    "summary": value,
                    "updatedAt": default_value.get("updatedAt", ""),
                }

    facts = data.get("facts")
    if isinstance(facts, list):
        normalized["facts"] = [f for f in facts if isinstance(f, dict)]

    return normalized


def _default_memory_base_dir() -> Path:
    override = (os.getenv("WEAVER_DATA_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path("data")


class MemoryStorage(ABC):
    """Abstract memory storage interface."""

    @abstractmethod
    def load(self, agent_name: str | None = None, user_id: str | None = None) -> dict[str, Any]:
        ...

    @abstractmethod
    def save(self, data: dict[str, Any], agent_name: str | None = None, user_id: str | None = None) -> bool:
        ...

    @abstractmethod
    def reload(self, agent_name: str | None = None, user_id: str | None = None) -> dict[str, Any]:
        ...


class LocalMemoryStorage(MemoryStorage):
    """File-based memory storage with per-user isolation."""

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or _default_memory_base_dir()
        self._lock = threading.Lock()
        self._cache: dict[tuple[str | None, str | None], dict[str, Any]] = {}

    def _storage_path(self, agent_name: str | None, user_id: str | None) -> Path:
        uid = user_id or DEFAULT_USER_ID
        if agent_name:
            return self.base_dir / "users" / uid / "agents" / agent_name / "memory.json"
        return self.base_dir / "users" / uid / "memory.json"

    def _load_from_disk(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return create_empty_memory()
        try:
            return normalize_memory_data(json.loads(path.read_text(encoding="utf-8") or "{}"))
        except (json.JSONDecodeError, OSError):
            return create_empty_memory()

    def load(self, agent_name: str | None = None, user_id: str | None = None) -> dict[str, Any]:
        cache_key = (agent_name, user_id)
        with self._lock:
            if cache_key in self._cache:
                return dict(self._cache[cache_key])
        data = self._load_from_disk(self._storage_path(agent_name, user_id))
        with self._lock:
            self._cache[cache_key] = data
        return dict(data)

    def save(self, data: dict[str, Any], agent_name: str | None = None, user_id: str | None = None) -> bool:
        path = self._storage_path(agent_name, user_id)
        try:
            data = normalize_memory_data(data)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
            cache_key = (agent_name, user_id)
            with self._lock:
                self._cache[cache_key] = dict(data)
            return True
        except OSError:
            logger.exception("Failed to save memory to %s", path)
            return False

    def reload(self, agent_name: str | None = None, user_id: str | None = None) -> dict[str, Any]:
        cache_key = (agent_name, user_id)
        with self._lock:
            self._cache.pop(cache_key, None)
        return self.load(agent_name, user_id)


_memory_storage: MemoryStorage | None = None


def get_memory_storage() -> MemoryStorage:
    global _memory_storage
    if _memory_storage is None:
        storage_path = getattr(settings, "memory_storage_path", "")
        if storage_path:
            _memory_storage = LocalMemoryStorage(Path(storage_path))
        else:
            _memory_storage = LocalMemoryStorage()
    return _memory_storage


def reset_memory_storage() -> None:
    global _memory_storage
    _memory_storage = None


def format_memory_for_injection(memory_data: dict[str, Any], max_tokens: int = 2000) -> str:
    """Format memory data for <system-reminder> injection.

    Includes top facts sorted by confidence and context summaries.
    """
    memory_data = normalize_memory_data(memory_data)
    parts: list[str] = []

    profile = memory_data.get("profile", {})
    profile_lines: list[str] = []
    for key, label in (
        ("role", "role"),
        ("expertise", "expertise"),
        ("language", "language"),
        ("format", "format"),
        ("verbosity", "verbosity"),
    ):
        value = profile.get(key) if isinstance(profile, dict) else ""
        if isinstance(value, str) and value.strip():
            profile_lines.append(f"- {label}: {value.strip()}")

    preferences = profile.get("preferences", {}) if isinstance(profile, dict) else {}
    if isinstance(preferences, dict) and preferences:
        rendered = ", ".join(
            f"{str(k).strip()}={str(v).strip()}"
            for k, v in preferences.items()
            if str(k).strip() and str(v).strip()
        )
        if rendered:
            profile_lines.append(f"- preferences: {rendered}")

    for key, label in (("preferredSources", "preferred_sources"), ("researchGoals", "research_goals")):
        value = profile.get(key) if isinstance(profile, dict) else []
        if isinstance(value, list):
            rendered = ", ".join(str(v).strip() for v in value[:8] if str(v).strip())
            if rendered:
                profile_lines.append(f"- {label}: {rendered}")

    if profile_lines:
        parts.append("<memory_profile>\n" + "\n".join(profile_lines) + "\n</memory_profile>")

    # Context summaries
    user_sections = memory_data.get("user", {})
    for key in ("workContext", "personalContext", "topOfMind"):
        section = user_sections.get(key, {})
        summary = section.get("summary", "") if isinstance(section, dict) else ""
        if summary:
            parts.append(f"<{key}>{summary}</{key}>")

    # Top facts by confidence
    facts = sorted(
        memory_data.get("facts", []),
        key=lambda f: f.get("confidence", 0),
        reverse=True,
    )
    if facts:
        fact_lines = []
        for f in facts[:15]:
            content = f.get("content", "")
            category = f.get("category", "context")
            fact_lines.append(f"- [{category}] {content}")
        parts.append("<known_facts>\n" + "\n".join(fact_lines) + "\n</known_facts>")

    result = "\n".join(parts)
    # Rough token limit enforcement
    if len(result) // 4 > max_tokens:
        result = result[: max_tokens * 4]
    return result
