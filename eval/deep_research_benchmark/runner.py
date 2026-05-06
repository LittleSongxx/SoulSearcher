from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from eval.deep_research_benchmark.collectors import (
    append_text_delta,
    extract_final_report_from_event,
    fetch_evidence,
    fetch_run_metrics,
)
from eval.deep_research_benchmark.dataset import load_tasks
from eval.deep_research_benchmark.schemas import (
    BenchmarkTask,
    CaseRunResult,
    RunConfig,
    utc_now_iso,
)
from eval.deep_research_benchmark.sse_client import iter_sse_events

_SUPERVISOR_STRATEGY_ALIASES = {"supervisor", "workers", "supervisor_worker", "supervisor_workers"}


def _safe_case_dir_name(case_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in case_id)


def _json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _text_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text or "", encoding="utf-8")


def _supervisor_strategy(value: str) -> str:
    strategy = str(value or "supervisor_workers").strip().lower().replace("-", "_")
    if strategy not in _SUPERVISOR_STRATEGY_ALIASES:
        raise ValueError("Deep Research benchmark only supports supervisor_workers strategy")
    return "supervisor_workers"


def build_research_payload(task: BenchmarkTask, config: RunConfig) -> dict[str, Any]:
    strategy = _supervisor_strategy(config.strategy)
    deepsearch_config = dict(config.deepsearch_config or {})
    deepsearch_config["deepsearch_strategy"] = strategy
    deepsearch_config["deepsearch_mode"] = strategy
    deepsearch_config.setdefault("source_policy", "web")
    return {
        "query": task.query,
        "model": config.model or None,
        "user_id": config.user_id,
        "deepsearch_config": deepsearch_config,
        "research_brief": {
            "original_query": task.query,
            "clarified_goal": task.query,
            "expected_fields": task.expected_dimensions,
            "freshness_requirement": task.freshness_requirement,
            "constraints": {
                "judge_rubric": task.judge_rubric,
                "source_constraints": task.source_constraints,
                "domain": task.domain,
                "task_type": task.task_type,
                "difficulty": task.difficulty,
                "language": task.metadata.get("language") if isinstance(task.metadata, dict) else "",
            },
            "success_criteria": [f"cover:{field}" for field in task.expected_dimensions],
            "source_policy": deepsearch_config.get("source_policy", "web"),
        },
        "search_mode": {
            "useWebSearch": False,
            "useAgent": True,
            "useDeepSearch": True,
        },
    }


async def _client_for_config(config: RunConfig) -> httpx.AsyncClient:
    if config.base_url.strip().lower() == "asgi":
        os.environ["DEEPSEARCH_MODE"] = _supervisor_strategy(config.strategy)
        import main

        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app),
            base_url="http://test",
            timeout=None,
        )
    return httpx.AsyncClient(base_url=config.base_url.rstrip("/"), timeout=None)


async def run_case(task: BenchmarkTask, config: RunConfig) -> CaseRunResult:
    case_dir = config.output_dir / "cases" / _safe_case_dir_name(task.id)
    events_path = case_dir / "events.jsonl"
    report_path = case_dir / "report.md"
    evidence_path = case_dir / "evidence.json"
    run_metrics_path = case_dir / "run_metrics.json"
    result_path = case_dir / "case_result.json"

    started_monotonic = time.monotonic()
    result = CaseRunResult(
        case_id=task.id,
        query=task.query,
        status="running",
        events_path=str(events_path),
        report_path=str(report_path),
        evidence_path=str(evidence_path),
        run_metrics_path=str(run_metrics_path),
    )
    _json_write(case_dir / "task.json", task.to_dict())

    thread_id: Optional[str] = None
    final_report = ""
    event_count = 0
    error_message = ""

    async with await _client_for_config(config) as client:
        try:
            async def _stream() -> None:
                nonlocal thread_id, final_report, event_count, error_message
                headers = {"Accept": "text/event-stream", "Content-Type": "application/json"}
                async with client.stream(
                    "POST",
                    "/api/research/sse",
                    json=build_research_payload(task, config),
                    headers=headers,
                    timeout=None,
                ) as response:
                    thread_id = response.headers.get("X-Thread-ID") or response.headers.get(
                        "x-thread-id"
                    )
                    if response.status_code < 200 or response.status_code >= 300:
                        body = await response.aread()
                        error_message = body.decode("utf-8", errors="ignore")[:1000]
                        return

                    events_path.parent.mkdir(parents=True, exist_ok=True)
                    with events_path.open("w", encoding="utf-8") as event_file:
                        async for event in iter_sse_events(response.aiter_text()):
                            event_count += 1
                            event_file.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
                            event_file.flush()

                            final_report = append_text_delta(final_report, event.event, event.data)
                            replacement = extract_final_report_from_event(event.event, event.data)
                            if replacement is not None:
                                final_report = replacement

                            if event.event == "error":
                                if isinstance(event.data, dict):
                                    error_message = str(event.data.get("message") or "")
                                elif isinstance(event.data, str):
                                    error_message = event.data
                            if event.event in {"done", "cancelled"}:
                                break
                            if event.event == "stream_timeout":
                                error_message = "stream timeout"
                                break

            await asyncio.wait_for(_stream(), timeout=max(1.0, float(config.timeout_s)))
        except TimeoutError:
            result.status = "timeout"
            error_message = f"timeout after {config.timeout_s}s"
            try:
                if thread_id:
                    await client.post(f"/api/chat/cancel/{thread_id}", timeout=5.0)
            except Exception:
                pass
        except Exception as exc:
            result.status = "failed"
            error_message = str(exc)

        evidence = await fetch_evidence(client, thread_id)
        run_metrics = await fetch_run_metrics(client, thread_id)

    ended_at = utc_now_iso()
    duration_ms = round((time.monotonic() - started_monotonic) * 1000, 2)

    if result.status == "running":
        result.status = "failed" if error_message else "completed"
    result.thread_id = thread_id
    result.ended_at = ended_at
    result.duration_ms = duration_ms
    result.final_report = final_report
    result.final_report_chars = len(final_report)
    result.error = error_message
    result.event_count = event_count
    result.evidence = evidence
    result.run_metrics = run_metrics

    _text_write(report_path, final_report)
    _json_write(evidence_path, evidence)
    _json_write(run_metrics_path, run_metrics)
    _json_write(result_path, result.to_dict())
    return result


async def run_cases(config: RunConfig) -> list[CaseRunResult]:
    tasks = load_tasks(config.dataset_path, max_cases=config.max_cases)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    _json_write(
        config.output_dir / "run_config.json",
        {
            **config.to_dict(),
            "created_at": utc_now_iso(),
            "task_count": len(tasks),
        },
    )

    results: list[CaseRunResult] = []
    # Keep the first implementation serial by default for reproducibility and API-rate safety.
    for task in tasks:
        result = await run_case(task, config)
        results.append(result)
        _json_write(config.output_dir / "results.json", [item.to_dict() for item in results])
    return results


def run_benchmark(config: RunConfig) -> list[CaseRunResult]:
    return asyncio.run(run_cases(config))
