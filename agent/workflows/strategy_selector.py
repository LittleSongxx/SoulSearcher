from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from agent.workflows.research_brief import ResearchBrief

SUPPORTED_DEEPSEARCH_STRATEGIES = {"tree", "supervisor_workers"}


@dataclass
class StrategyDecision:
    strategy: str
    reason: str
    parameters: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _cfg(config: dict[str, Any]) -> dict[str, Any]:
    value = config.get("configurable") if isinstance(config, dict) else {}
    return value if isinstance(value, dict) else {}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _explicit_strategy(config: dict[str, Any]) -> str:
    cfg = _cfg(config)
    value = cfg.get("deepsearch_strategy") or cfg.get("strategy")
    strategy = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "reflection": "supervisor_workers",
        "reflect": "supervisor_workers",
        "reflection_loop": "supervisor_workers",
        "supervisor": "supervisor_workers",
        "workers": "supervisor_workers",
        "supervisor_worker": "supervisor_workers",
        "linear": "supervisor_workers",
        "linear_light": "supervisor_workers",
        "light": "supervisor_workers",
        "hybrid": "supervisor_workers",
        "hybrid_private_web": "supervisor_workers",
    }
    return aliases.get(strategy, strategy)


def _explicit_mode(config: dict[str, Any]) -> str:
    mode = (
        str(_cfg(config).get("deepsearch_mode") or "").strip().lower().replace("-", "_")
    )
    if mode in {"reflection", "reflection_loop"}:
        return "supervisor_workers"
    if mode in {"supervisor", "workers", "supervisor_worker"}:
        return "supervisor_workers"
    if mode in {"linear", "linear_light", "light"}:
        return "supervisor_workers"
    if mode in {"hybrid", "hybrid_private_web"}:
        return "supervisor_workers"
    return mode


def select_deepsearch_strategy(
    *,
    brief: ResearchBrief,
    config: dict[str, Any],
    settings: Any,
    simple_query_detector: Optional[Callable[[str], bool]] = None,
) -> StrategyDecision:
    strategy = _explicit_strategy(config)
    if strategy in SUPPORTED_DEEPSEARCH_STRATEGIES:
        return StrategyDecision(
            strategy=strategy, reason="runtime strategy override", confidence=1.0
        )

    explicit_mode = _explicit_mode(config)
    if explicit_mode in SUPPORTED_DEEPSEARCH_STRATEGIES:
        return StrategyDecision(
            strategy=explicit_mode, reason="runtime mode override", confidence=1.0
        )

    configured_mode = (
        str(
            getattr(settings, "deepsearch_mode", "supervisor_workers")
            or "supervisor_workers"
        )
        .strip()
        .lower()
        .replace("-", "_")
    )
    if configured_mode in {
        "reflection",
        "reflection_loop",
        "hybrid",
        "hybrid_private_web",
    }:
        configured_mode = "supervisor_workers"
    if configured_mode in {"linear", "linear_light", "light"}:
        configured_mode = "supervisor_workers"
    if configured_mode in SUPPORTED_DEEPSEARCH_STRATEGIES:
        return StrategyDecision(
            strategy=configured_mode, reason="settings mode override", confidence=1.0
        )

    _ = simple_query_detector

    cfg = _cfg(config)
    low_budget = False
    if "deepsearch_max_epochs" in cfg and "deepsearch_query_num" in cfg:
        try:
            low_budget = (
                int(cfg.get("deepsearch_max_epochs")) <= 2
                and int(cfg.get("deepsearch_query_num")) <= 2
            )
        except (TypeError, ValueError):
            low_budget = False
    if _truthy(cfg.get("use_reflection_loop")):
        return StrategyDecision(
            strategy="supervisor_workers",
            reason="reflection loop is not enabled for this product profile",
            confidence=0.75,
        )
    return StrategyDecision(
        strategy="supervisor_workers",
        reason="default supervisor-workers deep research strategy",
        parameters={"low_budget": low_budget} if low_budget else {},
        confidence=0.85,
    )
