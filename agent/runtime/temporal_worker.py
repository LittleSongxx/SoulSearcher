"""Temporal worker entrypoint for SoulSearcher background research."""

from __future__ import annotations

import asyncio
import logging

from common.config import settings

from .temporal_runs import (
    DeepResearchBackgroundWorkflow,
    temporal_build_brief_activity,
    temporal_complete_callbacks_activity,
    temporal_execute_plan_tasks_activity,
    temporal_initialize_run_activity,
    temporal_plan_or_wait_hitl_activity,
    temporal_publish_artifacts_activity,
    temporal_quality_check_activity,
    temporal_run_deep_research_activity,
    temporal_write_report_activity,
)

logger = logging.getLogger(__name__)


async def main() -> None:
    try:
        from temporalio.client import Client
        from temporalio.worker import Worker
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("temporalio is required to run the SoulSearcher Temporal worker") from exc

    activities = [
        temporal_run_deep_research_activity,
        temporal_initialize_run_activity,
        temporal_build_brief_activity,
        temporal_plan_or_wait_hitl_activity,
        temporal_execute_plan_tasks_activity,
        temporal_write_report_activity,
        temporal_quality_check_activity,
        temporal_publish_artifacts_activity,
        temporal_complete_callbacks_activity,
    ]
    if DeepResearchBackgroundWorkflow is None or any(item is None for item in activities):
        raise RuntimeError("Temporal workflow/activity definitions are unavailable")

    client = await Client.connect(
        str(getattr(settings, "temporal_address", "localhost:7233") or "localhost:7233"),
        namespace=str(getattr(settings, "temporal_namespace", "default") or "default"),
    )
    task_queue = str(getattr(settings, "temporal_task_queue", "soulsearcher-deep-research") or "soulsearcher-deep-research")
    logger.info("Starting SoulSearcher Temporal worker on task queue %s", task_queue)
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[DeepResearchBackgroundWorkflow],
        activities=activities,
    )
    await worker.run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
