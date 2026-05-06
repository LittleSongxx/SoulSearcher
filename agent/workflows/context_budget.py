from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from agent.workflows.model_context_policy import build_model_context_policy


@dataclass(frozen=True)
class ContextBudget:
    stage: str
    max_tokens: int
    max_chars: int
    reserved_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ContextBudgetUsage:
    stage: str
    original_chars: int = 0
    kept_chars: int = 0
    truncated_items: int = 0
    metadata_preserved_items: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value not in (None, "", [], {})
        }


class ContextBudgetManager:
    def __init__(self, budgets: dict[str, ContextBudget]) -> None:
        self.budgets = budgets
        self.usage: dict[str, ContextBudgetUsage] = {}

    def cap_text(self, text: str, *, stage: str, suffix: str = "\n[truncated]") -> str:
        budget = self._budget(stage)
        value = str(text or "")
        usage = self._usage(stage)
        usage.original_chars += len(value)
        if budget.max_chars <= 0 or len(value) <= budget.max_chars:
            usage.kept_chars += len(value)
            return value
        usage.truncated_items += 1
        capped = _cap_to_chars(value, budget.max_chars, suffix)
        usage.kept_chars += len(capped)
        return capped

    def cap_text_list(self, items: list[str], *, stage: str) -> list[str]:
        budget = self._budget(stage)
        if budget.max_chars <= 0:
            return list(items or [])
        output: list[str] = []
        used = 0
        for item in items or []:
            text = str(item or "")
            remaining = budget.max_chars - used
            if remaining <= 0:
                self._usage(stage).truncated_items += 1
                break
            capped = _cap_to_chars(text, remaining)
            output.append(capped)
            used += len(capped)
            usage = self._usage(stage)
            usage.original_chars += len(text)
            usage.kept_chars += len(capped)
            if len(capped) < len(text):
                usage.truncated_items += 1
                break
        return output

    def cap_results(
        self,
        results: list[dict[str, Any]],
        *,
        stage: str,
        text_keys: tuple[str, ...] = ("content", "snippet", "raw_content", "body"),
    ) -> list[dict[str, Any]]:
        budget = self._budget(stage)
        if budget.max_chars <= 0:
            return list(results or [])
        output: list[dict[str, Any]] = []
        used = 0
        for result in results or []:
            if not isinstance(result, dict):
                continue
            item = dict(result)
            for key in text_keys:
                if key not in item or not isinstance(item.get(key), str):
                    continue
                remaining = max(0, budget.max_chars - used)
                if remaining <= 0:
                    item[key] = ""
                    self._usage(stage).truncated_items += 1
                    continue
                original = item[key]
                capped = _cap_to_chars(original, remaining)
                item[key] = capped
                used += len(capped)
                usage = self._usage(stage)
                usage.original_chars += len(original)
                usage.kept_chars += len(capped)
                usage.metadata_preserved_items += 1
                if len(capped) < len(original):
                    usage.truncated_items += 1
            output.append(item)
            if used >= budget.max_chars:
                break
        return output

    def to_artifact(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "budgets": {key: budget.to_dict() for key, budget in self.budgets.items()},
            "usage": {key: usage.to_dict() for key, usage in self.usage.items()},
        }

    def _budget(self, stage: str) -> ContextBudget:
        return self.budgets.get(stage) or ContextBudget(
            stage=stage, max_tokens=0, max_chars=0
        )

    def _usage(self, stage: str) -> ContextBudgetUsage:
        if stage not in self.usage:
            self.usage[stage] = ContextBudgetUsage(stage=stage)
        return self.usage[stage]


def build_context_budget_manager(
    *, models: dict[str, str], max_context_tokens: int = 0
) -> ContextBudgetManager:
    budgets: dict[str, ContextBudget] = {}
    for stage in ("planning", "research", "compression", "writing", "verifier"):
        model = str(
            models.get(stage) or models.get("research") or models.get("writing") or ""
        ).strip()
        if not model:
            continue
        policy = build_model_context_policy(stage=stage, model=model)
        usable = int(policy.usable_tokens)
        if max_context_tokens > 0:
            usable = min(usable, max_context_tokens)
        budgets[stage] = ContextBudget(
            stage=stage,
            max_tokens=usable,
            max_chars=max(0, usable * 4),
            reserved_tokens=int(policy.reserved_tokens),
        )
    return ContextBudgetManager(budgets)


def _cap_to_chars(text: str, max_chars: int, suffix: str = "\n[truncated]") -> str:
    if max_chars <= 0:
        return ""
    value = str(text or "")
    if len(value) <= max_chars:
        return value
    if max_chars <= len(suffix):
        return value[:max_chars]
    return value[: max_chars - len(suffix)] + suffix
