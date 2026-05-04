from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Optional

from agent.workflows.research_brief import ResearchBrief


@dataclass
class StrategyDecision:
    strategy: str
    reason: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _cfg(config: Dict[str, Any]) -> Dict[str, Any]:
    value = config.get("configurable") if isinstance(config, dict) else {}
    return value if isinstance(value, dict) else {}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _explicit_strategy(config: Dict[str, Any]) -> str:
    cfg = _cfg(config)
    value = cfg.get("deepsearch_strategy") or cfg.get("strategy")
    strategy = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "reflection": "reflection_loop",
        "reflect": "reflection_loop",
        "supervisor": "supervisor_workers",
        "workers": "supervisor_workers",
        "supervisor_worker": "supervisor_workers",
        "linear_light": "linear_light",
        "light": "linear_light",
        "hybrid": "hybrid_private_web",
    }
    return aliases.get(strategy, strategy)


def _explicit_mode(config: Dict[str, Any]) -> str:
    mode = str(_cfg(config).get("deepsearch_mode") or "").strip().lower().replace("-", "_")
    if mode == "reflection":
        return "reflection_loop"
    if mode in {"supervisor", "workers", "supervisor_worker"}:
        return "supervisor_workers"
    return mode


def select_deepsearch_strategy(
    *,
    brief: ResearchBrief,
    config: Dict[str, Any],
    settings: Any,
    simple_query_detector: Optional[Callable[[str], bool]] = None,
) -> StrategyDecision:
    strategy = _explicit_strategy(config)
    if strategy in {"linear_light", "linear", "tree", "reflection_loop", "supervisor_workers", "hybrid_private_web"}:
        return StrategyDecision(strategy=strategy, reason="runtime strategy override", confidence=1.0)

    explicit_mode = _explicit_mode(config)
    if explicit_mode in {"linear", "tree", "reflection_loop", "supervisor_workers"}:
        return StrategyDecision(strategy=explicit_mode, reason="runtime mode override", confidence=1.0)

    configured_mode = str(getattr(settings, "deepsearch_mode", "auto") or "auto").strip().lower().replace("-", "_")
    if configured_mode in {"linear", "tree", "reflection_loop", "supervisor_workers"}:
        return StrategyDecision(strategy=configured_mode, reason="settings mode override", confidence=1.0)

    topic = brief.original_query or brief.clarified_goal
    if simple_query_detector and simple_query_detector(topic):
        return StrategyDecision(
            strategy="linear_light",
            reason="simple factual query detected",
            parameters={
                "deepsearch_max_epochs": 1,
                "deepsearch_query_num": 1,
                "deepsearch_results_per_query": 5,
                "deepsearch_visualize_browser": False,
            },
            confidence=0.9,
        )

    cfg = _cfg(config)
    low_budget = False
    if "deepsearch_max_epochs" in cfg and "deepsearch_query_num" in cfg:
        try:
            low_budget = int(cfg.get("deepsearch_max_epochs")) <= 2 and int(cfg.get("deepsearch_query_num")) <= 2
        except (TypeError, ValueError):
            low_budget = False
    if _truthy(cfg.get("use_reflection_loop")) or low_budget:
        return StrategyDecision(strategy="reflection_loop", reason="low budget reflection loop selected", confidence=0.75)

    if brief.source_policy in {"hybrid", "private-first"}:
        return StrategyDecision(strategy="hybrid_private_web", reason="brief requests hybrid/private evidence", confidence=0.8)

    use_tree = bool(getattr(settings, "tree_exploration_enabled", True))
    if use_tree and brief.complexity == "broad":
        return StrategyDecision(strategy="tree", reason="broad research brief", confidence=0.8)
    if use_tree:
        return StrategyDecision(strategy="tree", reason="tree exploration enabled", confidence=0.65)
    return StrategyDecision(strategy="linear", reason="tree exploration disabled", confidence=0.7)
