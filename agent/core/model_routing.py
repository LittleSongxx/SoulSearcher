"""Three-tier Model Routing for Deep Research Agent.

Implements the unified design's 3x5 model routing matrix:
- fast_llm: summarization (always), simple research, memory extraction
- smart_llm: compression, standard research, supervisor, report, evaluation
- strategic_llm: deep research supervisor, deep research execution

Integrates with the existing ModelRouter from agent.core.multi_model
while adding the complexity-aware routing from the unified design.

Usage:
    router = get_research_model_router()
    llm = router.get_model("summarization", complexity="standard")
"""

from __future__ import annotations

import logging
from typing import Optional

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig

from agent.core.configuration import ResearchConfiguration
from agent.core.llm_factory import create_chat_model

logger = logging.getLogger(__name__)

# =============================================================================
# Centralized configurable model (pattern from open_deep_research)
# =============================================================================

configurable_model = init_chat_model(
    configurable_fields=("model", "max_tokens", "api_key"),
)


# =============================================================================
# Model Routing Matrix
# =============================================================================

class ResearchModelRouter:
    """Routes tasks to appropriate models based on phase × complexity.

    ┌──────────────────┬──────────┬──────────┬─────────────┐
    │ Phase            │ Simple   │ Standard │ Deep        │
    ├──────────────────┼──────────┼──────────┼─────────────┤
    │ summarization    │ fast_llm │ fast_llm │ fast_llm    │
    │ research         │ fast_llm │ smart_llm│ strategic   │
    │ supervisor       │ —        │ smart_llm│ strategic   │
    │ compression      │ —        │ smart_llm│ smart_llm   │
    │ report           │ fast_llm │ smart_llm│ strategic   │
    │ evaluation       │ fast_llm │ smart_llm│ smart_llm   │
    │ memory           │ fast_llm │ fast_llm │ fast_llm    │
    └──────────────────┴──────────┴──────────┴─────────────┘
    """

    def __init__(self, config: Optional[ResearchConfiguration] = None):
        self.config = config or ResearchConfiguration()

    def get_model(
        self,
        phase: str,
        complexity: str = "standard",
        runnable_config: Optional[RunnableConfig] = None,
    ) -> BaseChatModel:
        """Get the appropriate model for a given phase and complexity.

        Args:
            phase: One of "summarization", "research", "supervisor",
                   "compression", "report", "evaluation", "memory".
            complexity: "simple", "standard", or "deep".
            runnable_config: Optional LangChain RunnableConfig.

        Returns:
            Configured BaseChatModel instance.
        """
        model_name = self._get_model_name(phase, complexity, runnable_config)
        max_tokens = self._get_max_tokens(phase)
        return self._build_model(model_name, max_tokens)

    def _get_model_name(
        self,
        phase: str,
        complexity: str,
        runnable_config: Optional[RunnableConfig] = None,
    ) -> str:
        """Resolve model name from config overrides, phase defaults, and complexity."""
        # Check runtime config overrides first
        if runnable_config:
            cfg = runnable_config.get("configurable") or {}
            if isinstance(cfg, dict):
                override_key = f"{phase}_model"
                override = cfg.get(override_key)
                if isinstance(override, str) and override.strip():
                    return override.strip()

        # Phase-specific routing
        if phase == "summarization":
            return self.config.get_summarization_model()
        elif phase == "research":
            return self.config.get_model_for_complexity(complexity)
        elif phase == "supervisor":
            return self.config.get_supervisor_model(complexity)
        elif phase == "compression":
            return self.config.get_compression_model()
        elif phase == "report":
            return self.config.get_report_model(complexity)
        elif phase == "evaluation":
            return self.config.smart_llm
        elif phase == "memory":
            return self.config.fast_llm
        else:
            logger.warning(f"Unknown phase '{phase}', falling back to smart_llm")
            return self.config.smart_llm

    def _get_max_tokens(self, phase: str) -> int:
        """Get max_tokens for a given phase."""
        if phase in ("summarization", "memory"):
            return self.config.summarization_model_max_tokens
        elif phase in ("research", "supervisor"):
            return self.config.research_model_max_tokens
        elif phase == "compression":
            return self.config.compression_model_max_tokens
        elif phase == "report":
            return self.config.final_report_model_max_tokens
        return 4096

    def _build_model(self, model_name: str, max_tokens: int) -> BaseChatModel:
        """Build a chat model instance."""
        try:
            return create_chat_model(model_name, temperature=0)
        except Exception:
            # Fallback: use init_chat_model with configurable fields
            return configurable_model.with_config({
                "model": model_name,
                "max_tokens": max_tokens,
            })


# =============================================================================
# Global instance
# =============================================================================

_global_research_router: Optional[ResearchModelRouter] = None


def get_research_model_router() -> ResearchModelRouter:
    """Get or create the global research model router."""
    global _global_research_router
    if _global_research_router is None:
        _global_research_router = ResearchModelRouter()
    return _global_research_router
