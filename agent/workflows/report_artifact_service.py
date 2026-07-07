from __future__ import annotations

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig

from agent.core.artifacts import normalize_deepsearch_artifacts

logger = logging.getLogger(__name__)


def persist_workspace_artifacts(
    config: RunnableConfig,
    artifacts: dict[str, Any],
    *,
    report_content: str = "",
    report_format: str = "markdown",
) -> dict[str, Any]:
    """Persist key research artifacts into the per-thread workspace."""

    normalized = normalize_deepsearch_artifacts(artifacts)
    configurable = config.get("configurable") or {}
    thread_id = str(configurable.get("thread_id") or "default")
    try:
        from agent.runtime.workspace import get_research_workspace

        workspace = get_research_workspace(thread_id)
        normalized["workspace"] = workspace.artifact()
        workspace.write_json("artifacts.json", normalized)
        workspace.write_jsonl("evidence.jsonl", normalized.get("evidence_items", []) or [])
        workspace.write_jsonl("passages.jsonl", normalized.get("passages", []) or [])
        workspace.write_json("quality.json", {
            "summary": normalized.get("quality_summary", {}),
            "gates": normalized.get("quality_gates", []),
            "details": normalized.get("quality_details", {}),
        })
        workspace.write_json("sources.json", normalized.get("sources", []) or [])
        if report_content:
            filename = "report.html" if report_format == "html" else "report.md"
            workspace.write_text(filename, report_content)
    except Exception as e:
        logger.warning("[Workspace] Failed to persist report artifacts: %s", e)
    return normalized


async def ingest_memory_artifacts(
    config: RunnableConfig,
    *,
    research_brief: str,
    final_content: str,
    artifacts: dict[str, Any],
    notes: list[str],
) -> None:
    try:
        from common.config import settings

        if not getattr(settings, "memory_enabled", True):
            return
        from agent.memory import get_memory_service

        configurable = config.get("configurable") or {}
        runtime_context = configurable.get("runtime_context")
        thread_id = str(
            getattr(runtime_context, "thread_id", "")
            or configurable.get("thread_id")
            or "default"
        )
        run_id = str(
            getattr(runtime_context, "run_id", "")
            or configurable.get("run_id")
            or thread_id
        )
        user_id = str(configurable.get("user_id") or "default")
        await get_memory_service().ingest_research_run(
            user_id=user_id,
            thread_id=thread_id,
            run_id=run_id,
            research_brief=research_brief,
            final_report=final_content,
            artifacts=normalize_deepsearch_artifacts(artifacts),
            notes=notes,
        )
        logger.info("[Memory] Ingested research artifacts for thread %s", thread_id)
    except Exception as e:
        logger.warning("[Memory] Ingestion skipped: %s", e)
