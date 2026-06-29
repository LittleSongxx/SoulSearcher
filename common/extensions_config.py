"""Unified extensions configuration for MCP servers and skills."""

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SkillStateConfig(BaseModel):
    """Configuration for a single skill's state."""

    enabled: bool = Field(default=True, description="Whether this skill is enabled")


class ExtensionsConfig(BaseModel):
    """Unified configuration for MCP servers and skills."""

    mcp_servers: dict[str, Any] = Field(
        default_factory=dict,
        description="Map of MCP server name to configuration",
        alias="mcpServers",
    )
    skills: dict[str, SkillStateConfig] = Field(
        default_factory=dict,
        description="Map of skill name to state configuration",
    )
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    @classmethod
    def resolve_config_path(cls, config_path: str | None = None) -> Path | None:
        """Resolve the extensions config file path.

        Priority:
        1. Explicit ``config_path`` argument.
        2. ``SOULSEARCHER_EXTENSIONS_CONFIG_PATH`` environment variable.
        3. ``extensions_config.json`` in current directory.
        4. ``extensions_config.json`` in parent directory (project root).
        """
        if config_path:
            path = Path(config_path)
            if not path.exists():
                raise FileNotFoundError(f"Extensions config file not found at {path}")
            return path

        if env_path := os.getenv("SOULSEARCHER_EXTENSIONS_CONFIG_PATH"):
            path = Path(env_path)
            if not path.exists():
                raise FileNotFoundError(f"Extensions config file not found at {path}")
            return path

        for candidate in (
            Path("extensions_config.json"),
            Path("..") / "extensions_config.json",
        ):
            resolved = candidate.resolve()
            if resolved.exists():
                return resolved

        return None

    @classmethod
    def from_file(cls, config_path: str | None = None) -> "ExtensionsConfig":
        """Load extensions config from JSON file.

        Returns an empty config if the file is not found (extensions are optional).
        """
        resolved_path = cls.resolve_config_path(config_path)
        if resolved_path is None:
            return cls(mcp_servers={}, skills={})

        try:
            with open(resolved_path, encoding="utf-8") as f:
                config_data = json.load(f)
            return cls.model_validate(config_data)
        except json.JSONDecodeError as e:
            raise ValueError(f"Extensions config file at {resolved_path} is not valid JSON: {e}") from e
        except Exception as e:
            raise RuntimeError(f"Failed to load extensions config from {resolved_path}: {e}") from e

    def is_skill_enabled(self, skill_name: str, skill_category: str) -> bool:
        """Check if a skill is enabled.

        Args:
            skill_name: Name of the skill
            skill_category: Category of the skill

        Returns:
            True if enabled, False otherwise
        """
        skill_config = self.skills.get(skill_name)
        if skill_config is None:
            # Default to enable for public & custom skill
            return skill_category in ("public", "custom")
        return skill_config.enabled


_extensions_config: ExtensionsConfig | None = None


def get_extensions_config() -> ExtensionsConfig:
    """Get the extensions config instance (cached singleton)."""
    global _extensions_config
    if _extensions_config is None:
        _extensions_config = ExtensionsConfig.from_file()
    return _extensions_config


def reload_extensions_config(config_path: str | None = None) -> ExtensionsConfig:
    """Reload the extensions config from file and update the cached instance."""
    global _extensions_config
    _extensions_config = ExtensionsConfig.from_file(config_path)
    return _extensions_config


def reset_extensions_config() -> None:
    """Reset the cached extensions config instance."""
    global _extensions_config
    _extensions_config = None
