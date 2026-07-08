"""Prompt Loader — filesystem-based prompt management with fallback.

Loads system prompts from markdown files
organized by function in a configurable directory. Users can customize prompts
by editing the .md files without touching Python code.

Usage:
    loader = PromptLoader()
    prompt = loader.get("clarify_with_user")  # tries file, falls back to default

Directory structure (default: agent/prompts/industry_research/):
    clarify_with_user.md
    research_brief.md
    complexity_classifier.md
    research_architect.md
    source_scout.md
    compression.md
    final_report.md
    direct_answer.md
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default hardcoded fallbacks — mirrors agent.core.prompts constants so the loader
# is self-contained and doesn't create a circular import on the prompts module.
_FALLBACKS: dict[str, str] = {}

# Map of prompt_name → (default_file_name, env_var_override)
_PROMPT_REGISTRY: dict[str, tuple[str, str]] = {
    "clarify_with_user":    ("clarify_with_user.md",    "SOULSEARCHER_PROMPT_CLARIFY"),
    "research_brief":       ("research_brief.md",       "SOULSEARCHER_PROMPT_BRIEF"),
    "complexity_classifier": ("complexity_classifier.md","SOULSEARCHER_PROMPT_COMPLEXITY"),
    "research_architect":      ("research_architect.md",      "SOULSEARCHER_PROMPT_ARCHITECT"),
    "source_scout":           ("source_scout.md",           "SOULSEARCHER_PROMPT_SOURCE_SCOUT"),
    "compression":          ("compression.md",          "SOULSEARCHER_PROMPT_COMPRESSION"),
    "final_report":         ("final_report.md",         "SOULSEARCHER_PROMPT_REPORT"),
    "final_report_html":    ("final_report_html.md",    "SOULSEARCHER_PROMPT_REPORT_HTML"),
    "direct_answer":        ("direct_answer.md",        "SOULSEARCHER_PROMPT_DIRECT"),
    "source_curation":      ("source_curation.md",      "SOULSEARCHER_PROMPT_CURATION"),
    "summarize_webpage":    ("summarize_webpage.md",    "SOULSEARCHER_PROMPT_SUMMARIZE"),
}


class PromptLoader:
    """Load prompts from .md files with hardcoded fallback.

    The filesystem is the source of
    truth, and the Python constants are the fallback for when files don't exist.
    """

    def __init__(
        self,
        base_dir: Optional[str] = None,
        *,
        load_fallbacks: bool = True,
    ):
        """Initialize the prompt loader.

        Args:
            base_dir: Directory containing .md prompt files.
                      Defaults to SOULSEARCHER_PROMPTS_PATH env var, or
                      agent/prompts/industry_research/ relative to this file.
            load_fallbacks: If True, import hardcoded fallbacks from agent.core.prompts
                            on first load.
        """
        if base_dir:
            self._base_dir = Path(base_dir)
        elif os.environ.get("SOULSEARCHER_PROMPTS_PATH"):
            self._base_dir = Path(os.environ["SOULSEARCHER_PROMPTS_PATH"])
        else:
            self._base_dir = Path(__file__).resolve().parent / "industry_research"

        self._load_fallbacks = load_fallbacks
        self._cache: dict[str, str] = {}
        self._fallbacks_loaded = False

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, name: str, **kwargs) -> str:
        """Get a prompt by name.

        Resolution order:
        1. SOULSEARCHER_PROMPT_<NAME> environment variable (per-prompt override)
        2. .md file in base_dir
        3. Hardcoded fallback from agent.core.prompts

        Args:
            name: Prompt name (e.g. "research_architect", "final_report").
            **kwargs: Format arguments passed to str.format() on the prompt.

        Returns:
            The resolved prompt string. If kwargs are provided, the prompt
            is formatted with them.
        """
        prompt = self._load(name)
        if kwargs:
            try:
                prompt = prompt.format(**kwargs)
            except KeyError as e:
                logger.warning(
                    "[PromptLoader] Missing format key %s in prompt '%s' — "
                    "returning unformatted",
                    e, name,
                )
        return prompt

    def list(self) -> list[str]:
        """List all known prompt names."""
        return sorted(_PROMPT_REGISTRY.keys())

    def reload(self) -> None:
        """Clear the cache so the next get() re-reads from disk."""
        self._cache.clear()
        self._fallbacks_loaded = False
        logger.info("[PromptLoader] Cache cleared")

    def get_raw(self, name: str) -> str:
        """Get the raw prompt without formatting."""
        return self._load(name)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self, name: str) -> str:
        if name in self._cache:
            return self._cache[name]

        prompt: Optional[str] = None

        # 1. Per-prompt environment variable override
        if name in _PROMPT_REGISTRY:
            env_var = _PROMPT_REGISTRY[name][1]
            prompt = os.environ.get(env_var)

        # 2. Read from .md file
        if prompt is None:
            file_path = self._resolve_file_path(name)
            if file_path and file_path.is_file():
                try:
                    prompt = file_path.read_text(encoding="utf-8").strip()
                    logger.debug("[PromptLoader] Loaded %s from %s", name, file_path)
                except Exception as e:
                    logger.warning(
                        "[PromptLoader] Failed to read %s: %s — falling back",
                        file_path, e,
                    )

        # 3. Hardcoded fallback
        if prompt is None:
            prompt = self._get_fallback(name)

        if prompt is None:
            raise KeyError(
                f"Prompt '{name}' not found — no file at {self._base_dir} "
                f"and no fallback registered"
            )

        self._cache[name] = prompt
        return prompt

    def _resolve_file_path(self, name: str) -> Optional[Path]:
        """Resolve a prompt name to its .md file path."""
        if name in _PROMPT_REGISTRY:
            file_name = _PROMPT_REGISTRY[name][0]
        else:
            file_name = f"{name}.md"
        return self._base_dir / file_name

    def _get_fallback(self, name: str) -> Optional[str]:
        """Get the hardcoded fallback for a prompt name."""
        if not self._load_fallbacks:
            return None

        if not self._fallbacks_loaded:
            self._import_fallbacks()
            self._fallbacks_loaded = True

        return _FALLBACKS.get(name)

    def _import_fallbacks(self) -> None:
        """Import hardcoded prompts from agent.core.prompts as fallbacks."""
        try:
            from agent.core.prompts import (
                CLARIFY_WITH_USER_PROMPT,
                COMPLEXITY_CLASSIFIER_PROMPT,
                COMPRESSION_SYSTEM_PROMPT,
                DIRECT_ANSWER_PROMPT,
                FINAL_REPORT_PROMPT,
                HTML_REPORT_PROMPT,
                LEAD_RESEARCHER_PROMPT,
                RESEARCH_BRIEF_PROMPT,
                RESEARCHER_SYSTEM_PROMPT,
                SOURCE_CURATION_PROMPT,
                SUMMARIZE_WEBPAGE_PROMPT,
            )

            _FALLBACKS.update({
                "clarify_with_user": CLARIFY_WITH_USER_PROMPT,
                "research_brief": RESEARCH_BRIEF_PROMPT,
                "complexity_classifier": COMPLEXITY_CLASSIFIER_PROMPT,
                "research_architect": LEAD_RESEARCHER_PROMPT,
                "source_scout": RESEARCHER_SYSTEM_PROMPT,
                "compression": COMPRESSION_SYSTEM_PROMPT,
                "final_report": FINAL_REPORT_PROMPT,
                "final_report_html": HTML_REPORT_PROMPT,
                "direct_answer": DIRECT_ANSWER_PROMPT,
                "source_curation": SOURCE_CURATION_PROMPT,
                "summarize_webpage": SUMMARIZE_WEBPAGE_PROMPT,
            })
            logger.debug(
                "[PromptLoader] Imported %d fallback prompts from agent.core.prompts",
                len(_FALLBACKS),
            )
        except ImportError:
            logger.debug("[PromptLoader] agent.core.prompts not available for fallbacks")


# =============================================================================
# Global prompt loader instance
# =============================================================================

_default_loader: Optional[PromptLoader] = None


def get_prompt_loader(base_dir: Optional[str] = None) -> PromptLoader:
    """Get or create the global PromptLoader instance."""
    global _default_loader
    if _default_loader is None:
        _default_loader = PromptLoader(base_dir=base_dir)
        logger.info(
            "[PromptLoader] Initialized with base_dir=%s",
            _default_loader.base_dir,
        )
    return _default_loader


def reset_prompt_loader() -> None:
    """Reset the global PromptLoader (useful for testing)."""
    global _default_loader
    _default_loader = None
