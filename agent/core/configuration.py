"""Configuration management for the Deep Research Agent.

Pattern from open_deep_research's Configuration system:
- RunnableConfig-based with sensible defaults
- Environment variable overrides
- Per-model settings (research, compression, summarization, report)

Extended with gpt-researcher's three-tier model routing:
- fast_llm: cheap, fast model for high-volume simple tasks
- smart_llm: balanced model for writing and understanding
- strategic_llm: powerful model for reasoning and planning
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Annotated, Any, Optional

from langchain_core.runnables import RunnableConfig
from pydantic import Field

from common.config import settings as app_settings


@dataclass(kw_only=True)
class ResearchConfiguration:
    """Configuration for the Deep Research workflow.

    All fields can be overridden via RunnableConfig's configurable dict.
    Sensible defaults are provided for all settings.
    """

    # =========================================================================
    # Input Gateway
    # =========================================================================
    allow_clarification: bool = field(
        default_factory=lambda: getattr(app_settings, "allow_clarification", True)
    )
    """Whether to allow the clarify-with-user step before research."""

    # =========================================================================
    # Model Configuration (Fast / Smart / Strategic)
    # =========================================================================
    fast_llm: str = field(
        default_factory=lambda: os.environ.get(
            "FAST_LLM",
            getattr(app_settings, "fast_llm_model", "") or app_settings.primary_model
        )
    )
    """Fast, cheap model for summarization, memory extraction, simple queries."""

    smart_llm: str = field(
        default_factory=lambda: os.environ.get(
            "SMART_LLM",
            getattr(app_settings, "smart_llm_model", "") or app_settings.primary_model
        )
    )
    """Balanced model for research, writing, compression."""

    strategic_llm: str = field(
        default_factory=lambda: os.environ.get(
            "STRATEGIC_LLM",
            getattr(app_settings, "strategic_llm_model", "") or app_settings.reasoning_model
        )
    )
    """Most capable model for planning, complex analysis, deep research."""

    fast_llm_max_tokens: int = field(
        default_factory=lambda: getattr(app_settings, "fast_llm_max_tokens", 4096)
    )
    smart_llm_max_tokens: int = field(
        default_factory=lambda: getattr(app_settings, "smart_llm_max_tokens", 8192)
    )
    strategic_llm_max_tokens: int = field(
        default_factory=lambda: getattr(app_settings, "strategic_llm_max_tokens", 8192)
    )

    # =========================================================================
    # Phase-specific Model Mapping (open_deep_research pattern)
    # =========================================================================
    research_model: str = field(
        default_factory=lambda: os.environ.get(
            "RESEARCH_MODEL",
            getattr(app_settings, "research_model", "")
        )
    )
    """Model for the research phase (falls back to smart_llm)."""

    compression_model: str = field(
        default_factory=lambda: os.environ.get(
            "COMPRESSION_MODEL",
            getattr(app_settings, "compression_model", "")
        )
    )
    """Model for research compression (falls back to smart_llm)."""

    summarization_model: str = field(
        default_factory=lambda: os.environ.get(
            "SUMMARIZATION_MODEL",
            getattr(app_settings, "summarization_model", "")
        )
    )
    """Model for webpage summarization (falls back to fast_llm)."""

    final_report_model: str = field(
        default_factory=lambda: os.environ.get(
            "FINAL_REPORT_MODEL",
            getattr(app_settings, "final_report_model", "")
        )
    )
    """Model for final report generation (falls back to smart_llm)."""

    research_model_max_tokens: int = 4096
    compression_model_max_tokens: int = 8192
    summarization_model_max_tokens: int = 2048
    final_report_model_max_tokens: int = 8192

    # =========================================================================
    # Research Execution Limits
    # =========================================================================
    max_concurrent_research_units: int = field(
        default_factory=lambda: int(
            os.environ.get("MAX_CONCURRENT_RESEARCH", "5")
        )
    )
    """Maximum parallel researcher subgraphs per supervisor iteration."""

    max_researcher_iterations: int = field(
        default_factory=lambda: int(
            os.environ.get("MAX_RESEARCHER_ITERATIONS", "6")
        )
    )
    """Maximum supervisor loop iterations before forced completion."""

    max_react_tool_calls: int = field(
        default_factory=lambda: int(
            os.environ.get("MAX_REACT_TOOL_CALLS", "8")
        )
    )
    """Maximum tool-calling loop iterations within a single researcher."""

    max_structured_output_retries: int = 3
    """Maximum retries for structured output parsing failures."""

    # =========================================================================
    # Compression Configuration
    # =========================================================================
    compression_small_threshold: int = 8000
    """Content below this size (chars) passes through without compression."""

    compression_medium_threshold: int = 50000
    """Content between small and medium uses embedding-based filtering.
    Above medium uses LLM-based compression."""

    similarity_threshold: float = field(
        default_factory=lambda: float(
            os.environ.get("SIMILARITY_THRESHOLD", "0.35")
        )
    )
    """Embedding similarity threshold for context compression."""

    # =========================================================================
    # Source Curation
    # =========================================================================
    max_curated_sources: int = 10
    """Maximum number of sources retained after curation."""

    # =========================================================================
    # Report Generation
    # =========================================================================
    max_report_revisions: int = 2
    """Maximum revision attempts if quality check fails."""

    report_format: str = field(
        default_factory=lambda: (
            os.environ.get("REPORT_FORMAT", "markdown").strip().lower() or "markdown"
        )
    )
    """Output format for the final report: 'markdown' (default) or 'html'.
    HTML reports support embedded images, styled tables, SVG charts, callout
    boxes, and responsive layout. Markdown remains the default for simplicity
    and lower token cost."""

    html_report_embed_images: bool = field(
        default_factory=lambda: os.environ.get(
            "HTML_REPORT_EMBED_IMAGES", ""
        ).strip().lower() in {"1", "true", "yes", "y", "on"}
    )
    """When report_format='html', automatically inject viewed_images from the
    research phase into the final report as <img> tags."""

    # =========================================================================
    # Memory (Phase 4)
    # =========================================================================
    memory_enabled: bool = False
    memory_namespace: str = "research_memory"

    # =========================================================================
    # Vision / Multimodal Configuration (deer-flow pattern)
    # =========================================================================
    supports_vision: bool = field(
        default_factory=lambda: getattr(app_settings, "supports_vision", False)
    )
    """Whether the configured model supports vision/image inputs.
    When enabled, the view_image tool and image injection middleware are activated."""

    vision_model: str = field(
        default_factory=lambda: os.environ.get(
            "VISION_MODEL",
            getattr(app_settings, "vision_model", "") or ""
        )
    )
    """Specific vision-capable model override. Falls back to smart_llm when empty."""

    vision_enrich_data: bool = field(
        default_factory=lambda: os.environ.get(
            "VISION_ENRICH_DATA", ""
        ).strip().lower() in {"1", "true", "yes", "y", "on"}
    )
    """Whether to allow the researcher LLM to extract images from web pages.
    When enabled, the extract_web_images tool is available and the LLM can choose
    to download and analyze images from fetched pages (charts, diagrams, etc.).
    This is more expensive (bandwidth + vision tokens) and should only be enabled
    for research domains where visual data is high-value (financial reports,
    data dashboards, architecture diagrams, etc.)."""

    # =========================================================================
    # MCP Configuration
    # =========================================================================
    mcp_enabled: bool = field(
        default_factory=lambda: getattr(app_settings, "mcp_enabled", False)
    )
    mcp_prompt: str = ""

    # =========================================================================
    # Task-Type-Based Model Overrides (open_deep_research 4-role pattern)
    # =========================================================================
    # Each task type can override the complexity-based default.  Empty means
    # "use the complexity-based selection".  This follows open_deep_research's
    # decomposition of LLM roles — Summarization / Research / Compression /
    # Final Report — extended with finer-grained Weaver-specific tasks.

    query_generation_model: str = ""
    """Model for generating search queries (high-volume, cheap). Falls back to fast_llm."""

    content_summarization_model: str = ""
    """Model for summarising single web pages (highest volume, cheapest). Falls back to fast_llm."""

    web_reading_model: str = ""
    """Model for reading/extracting facts from web content. Falls back to fast_llm."""

    result_synthesis_model: str = ""
    """Model for synthesising multiple search results. Falls back to smart_llm."""

    strategic_decision_model: str = ""
    """Model for supervisor-level strategy decisions. Falls back to strategic_llm."""

    quality_check_model: str = ""
    """Model for Level-1 quality checks (fast, cheap). Falls back to fast_llm."""

    # =========================================================================
    # Methods
    # =========================================================================

    @classmethod
    def from_runnable_config(
        cls, config: Optional[RunnableConfig] = None
    ) -> ResearchConfiguration:
        """Create configuration from a RunnableConfig, with overrides from configurable dict.

        Pattern from open_deep_research: Configuration.from_runnable_config(config).
        """
        if config is None:
            return cls()

        configurable = config.get("configurable") or {}
        if not isinstance(configurable, dict):
            return cls()

        # Build kwargs from configurable overrides
        kwargs: dict[str, Any] = {}
        for field_name in cls.__dataclass_fields__:
            if field_name in configurable:
                kwargs[field_name] = configurable[field_name]

        return cls(**kwargs)

    # ------------------------------------------------------------------
    # Task-Type-Based Model Routing
    # ------------------------------------------------------------------
    # Follows open_deep_research's decomposition of LLM roles into
    # summarization / research / compression / final-report, extended with
    # finer-grained Weaver task types.  Each task has a dedicated override
    # field; when empty the complexity-based fallback is used.
    #
    # Rationale (from Anthropic's "Building Effective Agents"):
    #   "LLMs generally perform better when each consideration is handled
    #    by a separate LLM call."  Separating task types lets us route
    #    cheap/fast models to high-volume mechanical work while reserving
    #    powerful models for reasoning-heavy decisions.
    # ------------------------------------------------------------------

    _TASK_FALLBACK_MAP: dict[str, str] = {
        "query_generation":       "fast_llm",
        "content_summarization":  "fast_llm",
        "web_reading":            "fast_llm",
        "result_synthesis":       "smart_llm",
        "strategic_decision":     "strategic_llm",
        "compression":            "smart_llm",
        "report_writing":         "smart_llm",
        "quality_check":          "fast_llm",
    }

    def get_model_for_task(self, task_type: str, complexity: str = "standard") -> str:
        """Select the model for a specific task type.

        Checks the dedicated override field first (e.g. ``query_generation_model``),
        then falls back to the complexity-based default from ``_TASK_FALLBACK_MAP``,
        which resolves to fast_llm / smart_llm / strategic_llm.

        Task types (from open_deep_research 4-role extension):
          - query_generation       — generating diverse search queries
          - content_summarization  — condensing a single web page
          - web_reading            — extracting structured facts from content
          - result_synthesis       — merging multiple search results
          - strategic_decision     — supervisor-level strategy choices
          - compression            — compressing accumulated research
          - report_writing         — composing the final report
          - quality_check          — Level-1 instant validation
        """
        override_attr = f"{task_type}_model"
        override = getattr(self, override_attr, "")
        if override:
            return override

        fallback_attr = self._TASK_FALLBACK_MAP.get(task_type, "smart_llm")
        return getattr(self, fallback_attr, self.smart_llm)

    # ------------------------------------------------------------------
    # Legacy complexity-based routing (kept for minimal-diff transitions)
    # ------------------------------------------------------------------

    def get_model_for_complexity(self, complexity: str) -> str:
        """Get the appropriate research model based on task complexity.

        Simple → fast_llm, Standard → smart_llm, Deep → strategic_llm.
        """
        research_model = self.research_model
        if research_model:
            return research_model
        if complexity == "simple":
            return self.fast_llm
        elif complexity == "deep":
            return self.strategic_llm
        else:
            return self.smart_llm

    def get_supervisor_model(self, complexity: str) -> str:
        """Get the appropriate supervisor model.

        Simple tasks skip the supervisor entirely.
        Standard uses smart_llm, Deep uses strategic_llm.
        """
        if complexity == "standard":
            return self.smart_llm
        return self.strategic_llm

    def get_compression_model(self) -> str:
        """Get the compression model (smart_llm for understanding/rewriting)."""
        return self.compression_model or self.smart_llm

    def get_summarization_model(self) -> str:
        """Get the summarization model (always fast_llm for cost efficiency)."""
        return self.summarization_model or self.fast_llm

    def get_report_model(self, complexity: str) -> str:
        """Get the report generation model.

        Simple → fast_llm, Standard → smart_llm, Deep → strategic_llm.
        """
        if self.final_report_model:
            return self.final_report_model
        if complexity == "simple":
            return self.fast_llm
        elif complexity == "deep":
            return self.strategic_llm
        return self.smart_llm

    def get_model_max_tokens(self, model_name: str) -> int:
        """Get max tokens for a model, consulting the model token limit map."""
        # Per-model token limits. Add new models here.
        # For DashScope Qwen models: qwen3.6-flash=131072, qwen3.6-plus=131072, qwen3.7-max=131072
        token_limits: dict[str, int] = {
            "qwen3.6-flash": 131072,
            "qwen3.6-plus": 131072,
            "qwen3.7-max": 131072,
            "gpt-4.1-mini": 1048576,
            "gpt-4.1": 1048576,
            "gpt-4o": 128000,
            "gpt-4o-mini": 128000,
            "o3-mini": 200000,
            "o1": 200000,
            "claude-sonnet-4-6": 200000,
            "claude-opus-4-7": 200000,
            "deepseek-v4-flash": 131072,
            "deepseek-v4-pro": 131072,
        }
        return token_limits.get(model_name, 131072)
