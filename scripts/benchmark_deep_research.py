"""Run a reproducible deep research benchmark and write a JSON report."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.benchmarks.deep_research_bench_loader import (
    load_benchmark_tasks,
)  # noqa: E402

DEFAULT_BENCH_FILE = ROOT / "eval" / "benchmarks" / "sample_tasks.jsonl"
DEFAULT_GOLDEN_FILE = ROOT / "eval" / "golden_queries.json"

_TIME_MARKERS = (
    "latest",
    "recent",
    "today",
    "current",
    "update",
    "news",
    "最新",
    "近期",
    "今天",
    "动态",
    "新闻",
)


def _is_time_sensitive_query(query: str) -> bool:
    text = str(query or "").strip().lower()
    if not text:
        return False
    if any(marker in text for marker in _TIME_MARKERS):
        return True
    return bool(re.search(r"\b20\d{2}\b", text))


def _case_quality_targets(
    query: str,
    constraints: dict[str, Any],
    expected_fields: list[str],
    base_query_coverage_target: float,
    base_freshness_target: float,
) -> dict[str, Any]:
    freshness_days = constraints.get("freshness_days")
    freshness_days = (
        int(freshness_days) if isinstance(freshness_days, (int, float)) else None
    )
    time_sensitive = _is_time_sensitive_query(query) or (
        freshness_days is not None and freshness_days <= 30
    )

    field_complexity = len(expected_fields or [])
    complexity_bonus = (
        0.1 if field_complexity >= 4 else 0.05 if field_complexity >= 2 else 0.0
    )

    query_coverage_target = min(
        1.0,
        max(0.0, float(base_query_coverage_target) + complexity_bonus),
    )
    freshness_target = min(
        1.0,
        max(
            0.0,
            float(base_freshness_target) + (0.15 if time_sensitive else 0.0),
        ),
    )

    return {
        "time_sensitive": time_sensitive,
        "freshness_days_constraint": freshness_days,
        "query_coverage_target": round(query_coverage_target, 3),
        "freshness_ratio_target": round(freshness_target, 3),
    }


def _load_golden_entries(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return {}

    by_id: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id") or "").strip()
        if key:
            by_id[key] = item
    return by_id


def _maybe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _maybe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_sse_frame(frame: str) -> Optional[tuple[str, Any]]:
    """
    Parse a single SSE frame (without the trailing blank line).

    Returns:
    - (event_name, data_payload)
    - None for keepalive/empty frames
    """
    if not isinstance(frame, str):
        return None

    event_name = ""
    data_lines: list[str] = []
    for raw_line in frame.split("\n"):
        line = raw_line.rstrip("\r")
        if not line:
            continue
        if line.startswith(":"):
            # comment/keepalive
            continue
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())
            continue

    if not event_name:
        return None

    raw_data = "\n".join(data_lines).strip()
    if not raw_data:
        return (event_name, None)

    try:
        payload = json.loads(raw_data)
    except json.JSONDecodeError:
        payload = raw_data
    else:
        # The backend emits SSE frames where `data` can be the legacy envelope:
        #   {"type": "<event>", "data": {...}}
        # Unwrap to keep the rest of the benchmark logic simple.
        if isinstance(payload, dict) and "type" in payload and "data" in payload:
            payload = payload.get("data")

    return (event_name, payload)


async def _iter_sse_events(
    text_stream: AsyncIterator[str],
    chunk_timeout: float = 120.0,
) -> AsyncIterator[tuple[str, Any]]:
    buffer = ""
    ait = text_stream.__aiter__()
    while True:
        try:
            chunk = await asyncio.wait_for(ait.__anext__(), timeout=chunk_timeout)
        except StopAsyncIteration:
            break
        except TimeoutError:
            # No data received for chunk_timeout seconds — stream stalled
            break
        if not chunk:
            continue
        buffer += chunk
        buffer = buffer.replace("\r\n", "\n")

        while "\n\n" in buffer:
            frame, buffer = buffer.split("\n\n", 1)
            parsed = _parse_sse_frame(frame)
            if parsed is not None:
                yield parsed


async def _execute_research_case(
    query: str,
    *,
    mode: str,
    base_url: str,
    model: str,
    timeout_s: float,
) -> dict[str, Any]:
    """
    Execute a real deep research run via POST /api/research/sse and return metrics.

    Notes:
    - When base_url is "asgi", we call the in-process FastAPI app (no server needed).
    - `mode` is applied only for in-process runs by setting env var DEEPSEARCH_MODE
      before importing `main`. Remote base_url must be configured externally.
    """
    from httpx import ASGITransport, AsyncClient

    query = str(query or "").strip()
    if not query:
        return {"status": "failed", "error": "empty query"}

    started_at = time.monotonic()
    thread_id: Optional[str] = None
    final_report: str = ""
    error_message: Optional[str] = None
    last_quality_update: Optional[dict[str, Any]] = None

    async def _run(client: AsyncClient) -> None:
        nonlocal thread_id, final_report, error_message, last_quality_update

        payload = {
            "query": query,
            "model": model,
            "search_mode": {
                "useWebSearch": False,
                "useAgent": True,
                "useDeepSearch": True,
            },
        }

        async with client.stream(
            "POST",
            "/api/research/sse",
            json=payload,
            headers={"Accept": "text/event-stream", "Content-Type": "application/json"},
            timeout=None,
        ) as resp:
            thread_id = resp.headers.get("X-Thread-ID") or resp.headers.get(
                "x-thread-id"
            )

            async for event_name, data in _iter_sse_events(resp.aiter_text()):
                if event_name == "text" and isinstance(data, dict):
                    content = data.get("content")
                    if isinstance(content, str):
                        final_report += content
                elif event_name in {"message", "completion"} and isinstance(data, dict):
                    content = data.get("content")
                    if isinstance(content, str) and content.strip():
                        final_report = content
                elif event_name == "quality_update" and isinstance(data, dict):
                    last_quality_update = data
                elif event_name == "error":
                    if isinstance(data, dict):
                        msg = data.get("message")
                        if isinstance(msg, str) and msg.strip():
                            error_message = msg.strip()
                    elif isinstance(data, str) and data.strip():
                        error_message = data.strip()
                elif event_name in {"done", "cancelled"}:
                    break

    using_asgi = base_url.strip().lower() == "asgi"
    if using_asgi:
        # Apply deepsearch selection (auto/tree/linear) locally.
        os.environ["DEEPSEARCH_MODE"] = str(mode or "auto").strip()

        import main  # local import after env override

        transport = ASGITransport(app=main.app)
        client_ctx = AsyncClient(transport=transport, base_url="http://test")
    else:
        client_ctx = AsyncClient(base_url=base_url.rstrip("/"))

    async def _execute_with_client(client: AsyncClient) -> dict[str, Any]:
        try:
            await asyncio.wait_for(_run(client), timeout=max(1.0, float(timeout_s)))
        except TimeoutError:
            # Best-effort cancellation (shared cancel token id == thread id).
            try:
                if thread_id:
                    await client.post(f"/api/chat/cancel/{thread_id}", timeout=5.0)
            except Exception:
                pass
            return {
                "status": "timeout",
                "thread_id": thread_id,
                "duration_ms": round((time.monotonic() - started_at) * 1000, 2),
                "error": f"timeout after {timeout_s}s",
            }

        duration_ms = round((time.monotonic() - started_at) * 1000, 2)

        run_metrics: Optional[dict[str, Any]] = None
        evidence_summary: Optional[dict[str, Any]] = None
        try:
            if thread_id:
                resp = await client.get(f"/api/runs/{thread_id}", timeout=10.0)
                if resp.status_code == 200:
                    run_metrics = resp.json()
                    evidence_summary = run_metrics.get("evidence_summary")
        except Exception:
            pass

        status = "completed" if not error_message else "failed"
        return {
            "status": status,
            "thread_id": thread_id,
            "duration_ms": duration_ms,
            "final_report_chars": len(final_report),
            "final_report": final_report if isinstance(final_report, str) else "",
            "final_report_preview": (
                final_report[:600] if isinstance(final_report, str) else ""
            ),
            "error": error_message,
            "last_quality_update": last_quality_update,
            "run_metrics": run_metrics,
            "evidence_summary": evidence_summary,
        }

    if str(os.getenv("BENCHMARK_SKIP_CLIENT_CLOSE", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return await _execute_with_client(client_ctx)

    async with client_ctx as client:
        return await _execute_with_client(client)


def _evaluate_case_quality(
    *,
    actual: dict[str, Any],
    targets: dict[str, Any],
    min_citation_coverage: float,
) -> dict[str, Any]:
    evidence = actual.get("evidence_summary") if isinstance(actual, dict) else None
    if not isinstance(evidence, dict):
        return {"quality_pass": False, "reason": "missing evidence_summary"}

    actual_query_coverage = _maybe_float(evidence.get("query_coverage_score"))
    actual_freshness = _maybe_float(evidence.get("freshness_ratio_30d"))
    actual_citation = _maybe_float(evidence.get("citation_coverage"))

    coverage_target = _maybe_float(targets.get("query_coverage_target")) or 0.0
    freshness_target = _maybe_float(targets.get("freshness_ratio_target")) or 0.0
    time_sensitive = bool(targets.get("time_sensitive"))

    checks: dict[str, Any] = {
        "query_coverage": {
            "actual": actual_query_coverage,
            "target": coverage_target,
            "pass": (
                actual_query_coverage is not None
                and actual_query_coverage >= coverage_target
            ),
        },
        "citation_coverage": {
            "actual": actual_citation,
            "target": round(float(min_citation_coverage), 3),
            "pass": (
                actual_citation is not None
                and actual_citation >= float(min_citation_coverage)
            ),
        },
    }

    if time_sensitive:
        checks["freshness_ratio_30d"] = {
            "actual": actual_freshness,
            "target": freshness_target,
            "pass": (
                actual_freshness is not None and actual_freshness >= freshness_target
            ),
        }

    passed = all(bool(v.get("pass")) for v in checks.values())
    return {"quality_pass": passed, "checks": checks}


def run_benchmark(
    max_cases: int,
    mode: str,
    output: Path,
    bench_file: Path,
    min_query_coverage: float,
    min_freshness_ratio: float,
    *,
    execute: bool = False,
    base_url: str = "asgi",
    model: str = "",
    timeout_s: float = 180.0,
) -> dict[str, Any]:
    tasks = load_benchmark_tasks(bench_file, max_cases=max_cases)
    golden = _load_golden_entries(DEFAULT_GOLDEN_FILE)

    cases: list[dict[str, Any]] = []
    coverage_targets: list[float] = []
    freshness_targets: list[float] = []
    time_sensitive_cases = 0

    for task in tasks:
        golden_entry = golden.get(task.task_id)
        quality_targets = _case_quality_targets(
            query=task.query,
            constraints=task.constraints if isinstance(task.constraints, dict) else {},
            expected_fields=(
                task.expected_fields if isinstance(task.expected_fields, list) else []
            ),
            base_query_coverage_target=min_query_coverage,
            base_freshness_target=min_freshness_ratio,
        )
        if quality_targets["time_sensitive"]:
            time_sensitive_cases += 1
        coverage_targets.append(float(quality_targets["query_coverage_target"]))
        freshness_targets.append(float(quality_targets["freshness_ratio_target"]))

        case = {
            "id": task.task_id,
            "query": task.query,
            "mode": mode,
            "constraints": task.constraints,
            "expected_fields": task.expected_fields,
            "golden_available": bool(golden_entry),
            "quality_targets": quality_targets,
            "status": "ready",
        }
        cases.append(case)

    executed_cases = 0
    executed_passed = 0
    evidence_summaries: list[dict[str, Any]] = []

    if execute and cases:
        # Best-effort: use the same defaults as the backend quality gates.
        try:
            from common.config import settings

            min_citation = float(
                getattr(settings, "citation_gate_min_coverage", 0.6) or 0.6
            )
        except Exception:
            min_citation = 0.6

        resolved_model = str(model or "").strip()
        if not resolved_model:
            try:
                from common.config import settings

                resolved_model = str(
                    getattr(settings, "primary_model", "") or ""
                ).strip()
            except Exception:
                resolved_model = ""

        for idx, case in enumerate(cases, 1):
            print(
                f"[{idx}/{len(cases)}] Running case {case['id']}: {case['query'][:60]}...",
                flush=True,
            )
            case_start = time.monotonic()
            result = asyncio.run(
                _execute_research_case(
                    case["query"],
                    mode=mode,
                    base_url=base_url,
                    model=resolved_model,
                    timeout_s=timeout_s,
                )
            )
            case["execution"] = result
            case["status"] = result.get("status", "failed")
            elapsed = time.monotonic() - case_start
            print(
                f"  -> {case['status']} in {elapsed:.0f}s, report={result.get('final_report_chars',0)} chars",
                flush=True,
            )

            if case["status"] in {"completed", "failed", "timeout"}:
                executed_cases += 1

            quality = _evaluate_case_quality(
                actual=result,
                targets=(
                    case.get("quality_targets")
                    if isinstance(case.get("quality_targets"), dict)
                    else {}
                ),
                min_citation_coverage=min_citation,
            )
            case.update(quality)
            if case.get("quality_pass"):
                executed_passed += 1

            evidence = result.get("evidence_summary")
            if isinstance(evidence, dict):
                evidence_summaries.append(evidence)

    def _avg(values: list[Optional[float]]) -> Optional[float]:
        cleaned = [v for v in values if isinstance(v, (int, float))]
        if not cleaned:
            return None
        return round(sum(float(v) for v in cleaned) / len(cleaned), 4)

    evidence_citation = _avg(
        [_maybe_float(e.get("citation_coverage")) for e in evidence_summaries]
    )
    evidence_freshness = _avg(
        [_maybe_float(e.get("freshness_ratio_30d")) for e in evidence_summaries]
    )
    evidence_query_cov = _avg(
        [_maybe_float(e.get("query_coverage_score")) for e in evidence_summaries]
    )

    unsupported_claims_total = (
        sum(
            int(_maybe_int(e.get("unsupported_claims_count")) or 0)
            for e in evidence_summaries
        )
        if evidence_summaries
        else 0
    )

    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "mode": mode,
        "execute": bool(execute),
        "base_url": base_url,
        "model": model,
        "timeout_s": timeout_s,
        "max_cases": max_cases,
        "benchmark_file": str(bench_file),
        "cases": cases,
        "summary": {
            "total_cases": len(cases),
            "executed_cases": executed_cases,
            "quality_passed_cases": executed_passed,
            "golden_covered": sum(1 for c in cases if c["golden_available"]),
            "time_sensitive_cases": time_sensitive_cases,
            "avg_query_coverage_target": (
                round(sum(coverage_targets) / len(coverage_targets), 3)
                if coverage_targets
                else 0.0
            ),
            "avg_freshness_ratio_target": (
                round(sum(freshness_targets) / len(freshness_targets), 3)
                if freshness_targets
                else 0.0
            ),
            "quality_gate_defaults": {
                "min_query_coverage": min_query_coverage,
                "min_freshness_ratio": min_freshness_ratio,
            },
        },
        "metrics": {
            "available": bool(execute),
            # Backwards-compatible fields (used by smoke tests and dashboards).
            "citation_coverage": float(evidence_citation or 0.0),
            "unsupported_claims": int(unsupported_claims_total),
            "avg_citation_coverage": evidence_citation,
            "avg_freshness_ratio_30d": evidence_freshness,
            "avg_query_coverage_score": evidence_query_cov,
            "unsupported_claims_total": int(unsupported_claims_total),
        },
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run deep research benchmark smoke runner"
    )
    parser.add_argument("--max-cases", type=int, default=5, help="Maximum cases to run")
    parser.add_argument("--mode", choices=["auto", "tree", "linear"], default="auto")
    parser.add_argument(
        "--output", type=Path, required=True, help="Output JSON report path"
    )
    parser.add_argument(
        "--min-query-coverage",
        type=float,
        default=0.6,
        help="Base minimum query coverage target (0-1) for benchmark policy",
    )
    parser.add_argument(
        "--min-freshness-ratio",
        type=float,
        default=0.4,
        help="Base minimum freshness ratio target (0-1) for benchmark policy",
    )
    parser.add_argument(
        "--bench-file",
        type=Path,
        default=DEFAULT_BENCH_FILE,
        help="Benchmark JSONL file",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually run /api/research/sse for each case and collect metrics (requires API keys).",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default="asgi",
        help="Backend base URL. Use 'asgi' (default) to call the in-process app without starting a server.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="",
        help="Override backend model for execution mode (defaults to PRIMARY_MODEL).",
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=180.0,
        help="Per-case wall-clock timeout for execution mode.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    max_cases = max(1, int(args.max_cases))
    min_query_coverage = min(1.0, max(0.0, float(args.min_query_coverage)))
    min_freshness_ratio = min(1.0, max(0.0, float(args.min_freshness_ratio)))

    # For in-process execution, apply deepsearch mode before importing the backend app.
    if bool(args.execute) and str(args.base_url).strip().lower() == "asgi":
        os.environ["DEEPSEARCH_MODE"] = str(args.mode or "auto").strip()

    report = run_benchmark(
        max_cases=max_cases,
        mode=args.mode,
        output=args.output,
        bench_file=args.bench_file,
        min_query_coverage=min_query_coverage,
        min_freshness_ratio=min_freshness_ratio,
        execute=bool(args.execute),
        base_url=str(args.base_url or "asgi").strip() or "asgi",
        model=str(args.model or "").strip(),
        timeout_s=max(1.0, float(args.timeout_s)),
    )
    print(
        f"Benchmark report written: {args.output} ({report['summary']['total_cases']} cases)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
