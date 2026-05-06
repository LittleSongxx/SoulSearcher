from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Optional

_DEFAULT_CONTEXT_WINDOW = 128_000
_TOKEN_LIMIT_ERROR_MARKERS = (
    "context length",
    "context_length_exceeded",
    "maximum context",
    "max context",
    "token limit",
    "too many tokens",
    "input is too long",
    "prompt is too long",
    "exceeds the context",
    "exceeded token",
)
_MODEL_CONTEXT_WINDOWS = {
    "gpt-4.1": 1_047_576,
    "gpt-4.1-mini": 1_047_576,
    "gpt-4.1-nano": 1_047_576,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "claude-3-5": 200_000,
    "claude-3-7": 200_000,
    "claude-sonnet-4": 200_000,
    "claude-opus-4": 200_000,
    "gemini-1.5": 1_000_000,
    "gemini-2.0": 1_000_000,
    "gemini-2.5": 1_000_000,
    "deepseek": 64_000,
    "qwen": 131_000,
    "llama": 128_000,
}
_STAGE_RESERVE_RATIOS = {
    "planning": 0.20,
    "research": 0.25,
    "compression": 0.35,
    "writing": 0.35,
    "verifier": 0.25,
}


@dataclass
class ModelContextPolicy:
    stage: str
    model: str
    context_window: int
    reserved_tokens: int
    usable_tokens: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_model_context_window(model: str, *, default: int = _DEFAULT_CONTEXT_WINDOW) -> int:
    normalized = _normalize_model_name(model)
    for marker, window in _MODEL_CONTEXT_WINDOWS.items():
        if marker in normalized:
            return window
    return default


def build_model_context_policy(
    *,
    stage: str,
    model: str,
    reserve_ratio: Optional[float] = None,
    default_context_window: int = _DEFAULT_CONTEXT_WINDOW,
) -> ModelContextPolicy:
    context_window = resolve_model_context_window(model, default=default_context_window)
    ratio = reserve_ratio if reserve_ratio is not None else _STAGE_RESERVE_RATIOS.get(stage, 0.25)
    ratio = min(0.8, max(0.0, float(ratio)))
    reserved_tokens = int(context_window * ratio)
    usable_tokens = max(1, context_window - reserved_tokens)
    return ModelContextPolicy(
        stage=stage,
        model=model,
        context_window=context_window,
        reserved_tokens=reserved_tokens,
        usable_tokens=usable_tokens,
    )


def build_deepsearch_context_policy(models: Mapping[str, str]) -> dict[str, Any]:
    stages = []
    for stage in ("planning", "research", "compression", "writing", "verifier"):
        model = str(models.get(stage) or models.get("research") or models.get("writing") or "").strip()
        if not model:
            continue
        stages.append(build_model_context_policy(stage=stage, model=model).to_dict())
    return {
        "schema_version": 1,
        "stages": stages,
        "stage_count": len(stages),
    }


def is_token_limit_error(error: BaseException | str) -> bool:
    message = str(error or "").lower()
    return any(marker in message for marker in _TOKEN_LIMIT_ERROR_MARKERS)


def _normalize_model_name(model: str) -> str:
    value = str(model or "").strip().lower()
    if ":" in value:
        value = value.split(":", 1)[1]
    return value
