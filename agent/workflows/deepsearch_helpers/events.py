"""Event emission, feature tracing, and data persistence for DeepSearch."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from agent.workflows.deepsearch_helpers.config_utils import _resolve_deepsearch_mode
from agent.workflows.source_url_utils import compact_unique_sources
from common.config import settings

logger = logging.getLogger(__name__)


def _build_feature_trace(
    state: dict[str, Any],
    config: dict[str, Any],
    *,
    executed_mode: str,
    reflexion_feedbacks: Optional[list[str]] = None,
    reflexion_focus: Optional[list[str]] = None,
    backtrack_events: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    cfg = config.get("configurable") if isinstance(config, dict) else {}
    runtime_cfg = cfg if isinstance(cfg, dict) else {}
    search_mode = runtime_cfg.get("search_mode") or {}
    route = ""
    if isinstance(search_mode, dict):
        route = str(search_mode.get("route") or "").strip().lower()
    if not route:
        route = (
            str(
                runtime_cfg.get("resolved_route")
                or state.get("route")
                or runtime_cfg.get("route")
                or ""
            )
            .strip()
            .lower()
        )

    feedbacks = list(reflexion_feedbacks or [])
    focus = list(reflexion_focus or [])
    backtracks = list(backtrack_events or [])

    return {
        "configured_mode": _resolve_deepsearch_mode(config),
        "executed_mode": str(executed_mode or "").strip() or "linear",
        "resolved_route": route,
        "tree_exploration_enabled": bool(
            getattr(settings, "tree_exploration_enabled", True)
        ),
        "observation_masking_enabled": bool(
            getattr(settings, "observation_masking", False)
        ),
        "context_offloading_enabled": bool(
            getattr(settings, "context_offloading", False)
        ),
        "agent_reflexion_enabled": bool(
            getattr(settings, "agent_reflexion_enabled", False)
        ),
        "dynamic_tool_pruning_enabled": bool(
            getattr(settings, "dynamic_tool_pruning", False)
        ),
        "tree_backtrack_enabled": bool(
            getattr(settings, "tree_backtrack_enabled", False)
        ),
        "reflexion_triggered": bool(feedbacks),
        "reflexion_rounds": len(feedbacks),
        "reflexion_focus_count": len(focus),
        "reflexion_focus_preview": focus[:6],
        "tree_backtrack_triggered": bool(backtracks),
        "tree_backtrack_events": len(backtracks),
    }


def _resolve_event_emitter(state: dict[str, Any], config: dict[str, Any]) -> Any:
    """Resolve thread-scoped emitter if available (best effort)."""
    cfg = config.get("configurable") if isinstance(config, dict) else {}
    thread_id = ""
    if isinstance(cfg, dict):
        thread_id = str(cfg.get("thread_id") or "").strip()
    if not thread_id:
        thread_id = str(state.get("cancel_token_id") or "").strip()
    if not thread_id:
        return None

    try:
        from agent.core.events import get_emitter_sync

        return get_emitter_sync(thread_id)
    except Exception:
        return None


def _emit_event(emitter: Any, event_type: str, data: dict[str, Any]) -> None:
    """Emit an event from sync context without interrupting deepsearch flow."""
    if emitter is None:
        return
    try:
        emitter.emit_sync(event_type, data or {})
    except Exception as e:
        logger.debug(f"[deepsearch] failed to emit event '{event_type}': {e}")


def _compact_search_results(
    results: list[dict[str, Any]], limit: int = 5
) -> list[dict[str, Any]]:
    return compact_unique_sources(results, limit=limit)


def _provider_breakdown(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in results or []:
        if not isinstance(item, dict):
            continue
        provider = str(item.get("provider") or "unknown").strip() or "unknown"
        counts[provider] = counts.get(provider, 0) + 1
    return counts


def _event_results_limit() -> int:
    return max(
        1, min(20, int(getattr(settings, "deepsearch_event_results_limit", 5) or 5))
    )


def _safe_filename(name: str) -> str:
    return re.sub(r'[\/\\:\*\?"<>\|]', "_", name)[:80]


def _save_deepsearch_data(
    topic: str,
    have_query: list[str],
    summary_notes: list[str],
    search_runs: list[dict[str, Any]],
    final_report: str,
    epoch: int,
) -> str:
    """Persist deepsearch run data if enabled."""
    if not settings.deepsearch_save_data:
        return ""

    try:
        save_dir = Path(settings.deepsearch_save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = f"{_safe_filename(topic)}_{ts}.json"
        path = save_dir / fname
        data = {
            "topic": topic,
            "queries": have_query,
            "summaries": summary_notes,
            "search_runs": search_runs,
            "final_report": final_report,
            "epoch": epoch,
            "mode": "deepsearch_optimized",
        }
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(f"[deepsearch] saved run data -> {path}")
        return str(path)
    except Exception as e:
        logger.warning(f"[deepsearch] failed to save data: {e}")
        return ""
