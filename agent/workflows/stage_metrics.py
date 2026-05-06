from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class StageMetric:
    name: str
    status: str = "running"
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None
    duration_s: Optional[float] = None
    tokens_input: int = 0
    tokens_output: int = 0
    tokens_total: int = 0
    estimated_cost_usd: float = 0.0
    model_name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def finish(self, status: str = "completed", **metadata: Any) -> None:
        self.status = status
        self.ended_at = time.time()
        self.duration_s = round(max(0.0, self.ended_at - self.started_at), 4)
        for key in ("tokens_input", "tokens_output", "tokens_total", "estimated_cost_usd", "model_name"):
            if key in metadata:
                setattr(self, key, metadata.pop(key))
        if self.tokens_input or self.tokens_output:
            self.tokens_total = self.tokens_input + self.tokens_output
        for key, value in metadata.items():
            if value not in (None, "", [], {}):
                self.metadata[key] = value

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {key: value for key, value in data.items() if value not in (None, "", [], {})}


class StageMetricsRecorder:
    def __init__(self) -> None:
        self._stages: list[StageMetric] = []

    def start(self, name: str, **metadata: Any) -> StageMetric:
        stage = StageMetric(name=name, metadata={key: value for key, value in metadata.items() if value not in (None, "", [], {})})
        self._stages.append(stage)
        return stage

    def finish(self, stage: StageMetric, status: str = "completed", **metadata: Any) -> None:
        stage.finish(status=status, **metadata)

    def artifact(self) -> dict[str, Any]:
        stages = [stage.to_dict() for stage in self._stages]
        total_duration = sum(float(stage.get("duration_s") or 0.0) for stage in stages)
        total_tokens_input = sum(int(stage.get("tokens_input") or 0) for stage in stages)
        total_tokens_output = sum(int(stage.get("tokens_output") or 0) for stage in stages)
        total_estimated_cost_usd = sum(float(stage.get("estimated_cost_usd") or 0.0) for stage in stages)
        return {
            "schema_version": 2,
            "stage_count": len(stages),
            "total_tracked_duration_s": round(total_duration, 4),
            "total_tokens_input": total_tokens_input,
            "total_tokens_output": total_tokens_output,
            "total_tokens": total_tokens_input + total_tokens_output,
            "total_estimated_cost_usd": round(total_estimated_cost_usd, 6),
            "stages": stages,
        }


def warn_slow_stages(artifact: dict[str, Any], *, warn_after_s: float) -> list[dict[str, Any]]:
    try:
        threshold = float(warn_after_s)
    except (TypeError, ValueError):
        threshold = 0.0
    if threshold <= 0:
        return []
    warnings: list[dict[str, Any]] = []
    for stage in artifact.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        duration = stage.get("duration_s")
        try:
            duration_f = float(duration)
        except (TypeError, ValueError):
            continue
        if duration_f >= threshold:
            warnings.append(
                {
                    "stage": stage.get("name"),
                    "duration_s": duration_f,
                    "threshold_s": threshold,
                    "status": "slow_stage",
                }
            )
    return warnings
