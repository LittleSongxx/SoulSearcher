from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Tuple


ModelResolver = Callable[[str, Dict[str, Any]], str]


@dataclass
class DeepSearchModelStage:
    stage: str
    task_type: str
    model: str
    source: str
    override_key: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value not in (None, "", [], {})}


_STAGE_TASKS: List[Tuple[str, str, List[str]]] = [
    ("supervisor_model", "planning", ["deepsearch_supervisor_model", "supervisor_model", "planner_model", "planning_model"]),
    ("query_model", "query_gen", ["deepsearch_query_model", "query_model", "query_gen_model"]),
    ("search_summary_model", "synthesis", ["deepsearch_search_summary_model", "search_summary_model", "summary_model", "synthesis_model"]),
    ("worker_model", "research", ["deepsearch_worker_model", "worker_model", "researcher_model", "research_model"]),
    ("compression_model", "synthesis", ["deepsearch_compression_model", "compression_model"]),
    ("writer_model", "writing", ["deepsearch_writer_model", "writer_model", "final_report_model", "writing_model"]),
    ("verifier_model", "evaluation", ["deepsearch_verifier_model", "verifier_model", "evaluator_model", "evaluation_model"]),
]


def build_deepsearch_model_profile(
    config: Dict[str, Any],
    model_resolver: ModelResolver,
) -> Dict[str, Any]:
    cfg = _configurable(config)
    stages: List[DeepSearchModelStage] = []
    models: Dict[str, str] = {}
    for stage, task_type, override_keys in _STAGE_TASKS:
        model, source, override_key = _resolve_stage_model(
            stage=stage,
            task_type=task_type,
            override_keys=override_keys,
            config=config,
            cfg=cfg,
            model_resolver=model_resolver,
        )
        models[stage] = model
        stages.append(
            DeepSearchModelStage(
                stage=stage,
                task_type=task_type,
                model=model,
                source=source,
                override_key=override_key,
            )
        )
    profile_name = str(cfg.get("deepsearch_model_profile") or "default").strip() or "default"
    return {
        "schema_version": 1,
        "profile_name": profile_name,
        "stage_count": len(stages),
        "models": models,
        "stages": [stage.to_dict() for stage in stages],
    }


def _configurable(config: Dict[str, Any]) -> Dict[str, Any]:
    cfg = config.get("configurable") if isinstance(config, dict) else {}
    return cfg if isinstance(cfg, dict) else {}


def _resolve_stage_model(
    *,
    stage: str,
    task_type: str,
    override_keys: List[str],
    config: Dict[str, Any],
    cfg: Dict[str, Any],
    model_resolver: ModelResolver,
) -> Tuple[str, str, str]:
    for key in override_keys:
        value = cfg.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip(), "runtime", key
    model = model_resolver(task_type, config)
    if stage == "compression_model":
        summary_model = cfg.get("search_summary_model") or cfg.get("deepsearch_search_summary_model")
        if isinstance(summary_model, str) and summary_model.strip():
            return summary_model.strip(), "runtime", "search_summary_model"
    return str(model or "").strip(), "router", ""
