from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from threading import Lock
from typing import Any, Optional

from common.config import settings


@dataclass(frozen=True)
class DeepResearchBudget:
    max_research_units: int = 0
    max_tool_calls_per_unit: int = 0
    max_search_queries: int = 0
    max_mcp_tools: int = 0
    max_context_tokens: int = 0
    max_compression_attempts: int = 0
    max_reflection_rounds: int = 0
    max_seconds_per_worker: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BudgetEvent:
    type: str
    reason: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value not in (None, "", [], {})}


class ResearchBudgetRuntime:
    def __init__(self, budget: DeepResearchBudget) -> None:
        self.budget = budget
        self.research_units_started = 0
        self.search_queries_used = 0
        self.compression_attempts_used = 0
        self.reflection_rounds_used = 0
        self.tool_calls_by_unit: dict[str, int] = {}
        self.stop_reasons: list[str] = []
        self.events: list[BudgetEvent] = []
        self._lock = Lock()

    def reserve_research_units(self, units: list[Any]) -> tuple[list[Any], list[Any]]:
        if not units:
            return [], []
        with self._lock:
            if self.budget.max_research_units <= 0:
                self.research_units_started += len(units)
                return list(units), []
            remaining = max(0, self.budget.max_research_units - self.research_units_started)
            accepted = list(units[:remaining])
            rejected = list(units[remaining:])
            self.research_units_started += len(accepted)
            if rejected:
                self._record_locked(
                    "research_unit_budget_exhausted",
                    "maximum research units reached",
                    {"rejected_count": len(rejected), "limit": self.budget.max_research_units},
                )
            return accepted, rejected

    def reserve_search_query(self, query: str = "") -> bool:
        with self._lock:
            if self.budget.max_search_queries <= 0:
                self.search_queries_used += 1
                return True
            if self.search_queries_used >= self.budget.max_search_queries:
                self._record_locked(
                    "search_query_budget_exhausted",
                    "maximum search queries reached",
                    {"query": query, "limit": self.budget.max_search_queries},
                )
                return False
            self.search_queries_used += 1
            return True

    def reserve_tool_call(self, unit_id: str, tool_name: str = "") -> bool:
        unit = str(unit_id or "global")
        with self._lock:
            count = self.tool_calls_by_unit.get(unit, 0)
            if self.budget.max_tool_calls_per_unit > 0 and count >= self.budget.max_tool_calls_per_unit:
                self._record_locked(
                    "tool_call_budget_exhausted",
                    "maximum tool calls per research unit reached",
                    {"unit_id": unit, "tool": tool_name, "limit": self.budget.max_tool_calls_per_unit},
                )
                return False
            self.tool_calls_by_unit[unit] = count + 1
            return True

    def reserve_compression_attempt(self, unit_id: str = "") -> bool:
        with self._lock:
            if self.budget.max_compression_attempts <= 0:
                self.compression_attempts_used += 1
                return True
            if self.compression_attempts_used >= self.budget.max_compression_attempts:
                self._record_locked(
                    "compression_budget_exhausted",
                    "maximum compression attempts reached",
                    {"unit_id": unit_id, "limit": self.budget.max_compression_attempts},
                )
                return False
            self.compression_attempts_used += 1
            return True

    def reserve_reflection_round(self, round_index: int = 0) -> bool:
        with self._lock:
            if self.budget.max_reflection_rounds <= 0:
                self.reflection_rounds_used += 1
                return True
            if self.reflection_rounds_used >= self.budget.max_reflection_rounds:
                self._record_locked(
                    "reflection_budget_exhausted",
                    "maximum reflection rounds reached",
                    {"round_index": round_index, "limit": self.budget.max_reflection_rounds},
                )
                return False
            self.reflection_rounds_used += 1
            return True

    def check_worker_timeout(self, worker_id: str, started_at: float) -> bool:
        if self.budget.max_seconds_per_worker <= 0:
            return False
        elapsed = time.time() - started_at
        if elapsed >= self.budget.max_seconds_per_worker:
            with self._lock:
                self._record_locked(
                    "worker_timeout",
                    f"worker {worker_id} exceeded time budget ({elapsed:.1f}s >= {self.budget.max_seconds_per_worker:.1f}s)",
                    {"worker_id": worker_id, "elapsed_s": round(elapsed, 2), "limit_s": self.budget.max_seconds_per_worker},
                )
            return True
        return False

    def record_stop_reason(self, reason: str, details: Optional[dict[str, Any]] = None) -> None:
        with self._lock:
            self._record_locked("budget_stop", reason, dict(details or {}))

    def to_artifact(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": 1,
                "budget": self.budget.to_dict(),
                "usage": {
                    "research_units_started": self.research_units_started,
                    "search_queries_used": self.search_queries_used,
                    "compression_attempts_used": self.compression_attempts_used,
                    "reflection_rounds_used": self.reflection_rounds_used,
                    "tool_calls_by_unit": dict(self.tool_calls_by_unit),
                },
                "stop_reasons": list(self.stop_reasons),
                "events": [event.to_dict() for event in self.events],
            }

    def _record_locked(self, event_type: str, reason: str, details: dict[str, Any]) -> None:
        if reason and reason not in self.stop_reasons:
            self.stop_reasons.append(reason)
        self.events.append(BudgetEvent(type=event_type, reason=reason, details=details))


def build_deepsearch_budget(*, config: dict[str, Any], research_brief: Any = None) -> DeepResearchBudget:
    cfg = _configurable(config)
    brief_policy = _brief_budget_policy(research_brief)
    rounds = _int_setting(cfg, brief_policy, "deepsearch_supervisor_rounds", "rounds", int(getattr(settings, "deepsearch_supervisor_rounds", 2) or 2))
    workers = _int_setting(cfg, brief_policy, "deepsearch_supervisor_max_workers", "workers", int(getattr(settings, "deepsearch_supervisor_max_workers", 4) or 4))
    queries_per_worker = _int_setting(cfg, brief_policy, "deepsearch_supervisor_queries_per_worker", "queries_per_worker", int(getattr(settings, "deepsearch_supervisor_queries_per_worker", 2) or 2))
    default_units = max(1, rounds * workers)
    default_queries = max(1, default_units * queries_per_worker + int(getattr(settings, "deepsearch_prewrite_gap_followup_queries", 2) or 2))
    max_research_units = _int_setting(cfg, brief_policy, "deepsearch_max_research_units", "max_research_units", default_units)
    max_tool_calls_per_unit = _int_setting(cfg, brief_policy, "deepsearch_max_tool_calls_per_unit", "max_tool_calls_per_unit", queries_per_worker)
    max_search_queries = _int_setting(cfg, brief_policy, "deepsearch_max_search_queries", "max_search_queries", default_queries)
    max_mcp_tools = _int_setting(cfg, brief_policy, "mcp_max_tools", "max_mcp_tools", int(getattr(settings, "mcp_max_tools", 0) or 0))
    max_context_tokens = _int_setting(cfg, brief_policy, "deepsearch_max_context_tokens", "max_context_tokens", int(getattr(settings, "deepsearch_max_context_tokens", 0) or 0))
    max_compression_attempts = _int_setting(cfg, brief_policy, "deepsearch_max_compression_attempts", "max_compression_attempts", max_research_units)
    max_reflection_rounds = _int_setting(cfg, brief_policy, "deepsearch_max_reflection_rounds", "max_reflection_rounds", rounds)
    max_seconds_per_worker = _float_setting(cfg, brief_policy, "deepsearch_max_seconds_per_worker", "max_seconds_per_worker", float(getattr(settings, "deepsearch_max_seconds_per_worker", 0.0) or 0.0))
    return DeepResearchBudget(
        max_research_units=max(0, max_research_units),
        max_tool_calls_per_unit=max(0, max_tool_calls_per_unit),
        max_search_queries=max(0, max_search_queries),
        max_mcp_tools=max(0, max_mcp_tools),
        max_context_tokens=max(0, max_context_tokens),
        max_compression_attempts=max(0, max_compression_attempts),
        max_reflection_rounds=max(0, max_reflection_rounds),
        max_seconds_per_worker=max(0.0, max_seconds_per_worker),
    )


def _configurable(config: dict[str, Any]) -> dict[str, Any]:
    cfg = config.get("configurable") if isinstance(config, dict) else {}
    return cfg if isinstance(cfg, dict) else {}


def _brief_budget_policy(research_brief: Any) -> dict[str, Any]:
    if isinstance(research_brief, dict):
        value = research_brief.get("budget_policy")
    else:
        value = getattr(research_brief, "budget_policy", None)
    return value if isinstance(value, dict) else {}


def _int_setting(cfg: dict[str, Any], brief_policy: dict[str, Any], config_key: str, brief_key: str, default: int) -> int:
    for value in (cfg.get(config_key), brief_policy.get(brief_key), brief_policy.get(config_key)):
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return int(default)


def _float_setting(cfg: dict[str, Any], brief_policy: dict[str, Any], config_key: str, brief_key: str, default: float) -> float:
    for value in (cfg.get(config_key), brief_policy.get(brief_key), brief_policy.get(config_key)):
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return float(default)
