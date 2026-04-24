import pytest

import main
from scripts.benchmark_deep_research import (
    _execute_research_case,
    _parse_sse_frame,
    run_benchmark,
)


def test_parse_sse_frame_extracts_event_and_json():
    parsed = _parse_sse_frame('event: status\ndata: {"text":"hi"}\n')
    assert parsed == ("status", {"text": "hi"})


def test_parse_sse_frame_unwraps_legacy_envelope():
    parsed = _parse_sse_frame(
        'event: text\ndata: {"type":"text","data":{"content":"hi"}}\n'
    )
    assert parsed == ("text", {"content": "hi"})


def test_parse_sse_frame_ignores_empty_and_comments():
    assert _parse_sse_frame("") is None
    assert _parse_sse_frame(": keepalive\n\n") is None
    assert _parse_sse_frame("data: x\n") is None


@pytest.mark.asyncio
async def test_execute_case_returns_failed_when_no_openai_key(monkeypatch):
    monkeypatch.setattr(main.settings, "openai_api_key", "")
    result = await _execute_research_case(
        "hello",
        mode="auto",
        base_url="asgi",
        model="deepseek-v4-flash",
        timeout_s=20.0,
    )
    assert result["status"] == "failed"
    assert isinstance(result.get("thread_id"), str) and result["thread_id"].startswith(
        "thread_"
    )
    assert "OPENAI_API_KEY" in str(result.get("error") or "")


def test_run_benchmark_execute_mode_writes_report(tmp_path, monkeypatch):
    monkeypatch.setattr(main.settings, "openai_api_key", "")
    output = tmp_path / "bench.json"
    report = run_benchmark(
        max_cases=1,
        mode="auto",
        output=output,
        bench_file="eval/benchmarks/sample_tasks.jsonl",
        min_query_coverage=0.6,
        min_freshness_ratio=0.4,
        execute=True,
        base_url="asgi",
        model="deepseek-v4-flash",
        timeout_s=20.0,
    )
    assert output.exists()
    assert report["execute"] is True
    assert report["metrics"]["available"] is True
    assert report["summary"]["executed_cases"] == 1
    assert report["cases"][0]["status"] in {"failed", "timeout"}
