import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.responses import StreamingResponse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.deep_research_benchmark.cli import build_parser
from eval.deep_research_benchmark.dataset import load_tasks
from eval.deep_research_benchmark.judges.rubric import extract_json_object
from eval.deep_research_benchmark.metrics import percentile, summarize_results
from eval.deep_research_benchmark.runner import build_research_payload, run_case
from eval.deep_research_benchmark.schemas import DEFAULT_SUPERVISOR_DEEPSEARCH_CONFIG, RunConfig
from eval.deep_research_benchmark.sse_client import parse_sse_frame


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text or "")


def test_seed_dataset_loads_tasks():
    tasks = load_tasks(ROOT / "eval" / "deep_research_benchmark" / "datasets" / "seed_tasks.jsonl")

    assert len(tasks) == 10
    assert tasks[0].id == "web_dr_001"
    assert tasks[0].expected_dimensions
    assert tasks[0].must_cite is True
    assert sum(1 for task in tasks if _contains_cjk(task.query)) == 5
    assert sum(1 for task in tasks if not _contains_cjk(task.query)) == 5
    assert len({task.domain for task in tasks}) >= 8


def test_parse_sse_frame_unwraps_legacy_envelope():
    event = parse_sse_frame('id: 1\nevent: text\ndata: {"type":"text","data":{"content":"hi"}}\n')

    assert event is not None
    assert event.event == "text"
    assert event.event_id == "1"
    assert event.data == {"content": "hi"}


def test_extract_json_object_handles_fenced_json():
    payload = extract_json_object('```json\n{"overall": 8, "rubric_pass": true}\n```')

    assert payload == {"overall": 8, "rubric_pass": True}


def test_percentile_and_summary_metrics():
    results = [
        {
            "case_id": "a",
            "status": "completed",
            "duration_ms": 1000,
            "final_report_chars": 1000,
            "evidence": {
                "sources": [{"url": "https://example.com"}],
                "evidence_items": [{"id": "ev1"}],
                "passages": [{"text": "passage"}],
                "claims": [{"status": "verified"}, {"status": "unsupported"}],
            },
        },
        {
            "case_id": "b",
            "status": "completed",
            "duration_ms": 3000,
            "final_report_chars": 2000,
            "evidence": {
                "sources": [{"url": "https://example.org"}],
                "evidence_items": [{"id": "ev2"}, {"id": "ev3"}],
                "passages": [{"text": "passage"}, {"text": "passage 2"}],
                "claims": [{"status": "verified"}],
            },
        },
        {"case_id": "c", "status": "timeout", "duration_ms": 5000},
    ]
    scores = [
        {
            "case_id": "a",
            "judge_type": "report",
            "status": "scored",
            "passed": True,
            "details": {"dimensions": {"evidence_quality": 8, "depth": 7}},
        },
        {
            "case_id": "b",
            "judge_type": "report",
            "status": "scored",
            "passed": False,
            "details": {"dimensions": {"evidence_quality": 6, "depth": 5}},
        },
        {"case_id": "a", "judge_type": "citation", "status": "scored", "score": 0.9, "details": {"effective_unique_sources": 10}},
        {"case_id": "b", "judge_type": "citation", "status": "scored", "score": 0.8, "details": {"effective_unique_sources": 20}},
        {"case_id": "a", "judge_type": "claim", "status": "scored", "score": 0.05},
        {"case_id": "b", "judge_type": "claim", "status": "scored", "score": 0.15},
    ]

    assert percentile([1, 3], 0.5) == 2.0
    tasks = [
        {"id": "a", "domain": "policy", "query": "English query", "metadata": {"language": "en"}},
        {"id": "b", "domain": "policy", "query": "中文问题", "metadata": {"language": "zh"}},
    ]
    summary = summarize_results(results, scores, tasks)
    assert summary.total_cases == 3
    assert summary.completed_cases == 2
    assert summary.timeout_cases == 1
    assert summary.stable_completion_rate == pytest.approx(2 / 3, rel=1e-3)
    assert summary.rubric_pass_rate == 0.5
    assert summary.citation_accuracy == 0.85
    assert summary.unsupported_claim_rate == 0.1
    assert summary.avg_effective_citations == 15.0
    assert summary.judge_scores_total == 6
    assert summary.avg_report_evidence_quality == 7.0
    assert summary.avg_sources == 1.0
    assert summary.avg_evidence_items == 1.5
    assert summary.avg_passages == 1.5
    assert summary.avg_claims_checked == 1.5
    assert summary.avg_claims_supported == 1.0
    assert summary.avg_claims_unsupported == 0.5
    assert summary.by_language["en"]["rubric_pass_rate"] == 1.0
    assert summary.by_language["zh"]["rubric_pass_rate"] == 0.0
    assert summary.by_domain["policy"]["total_cases"] == 2


def test_cli_parser_accepts_run_command(tmp_path):
    parser = build_parser()
    args = parser.parse_args(
        [
            "run",
            "--dataset",
            "eval/deep_research_benchmark/datasets/seed_tasks.jsonl",
            "--output",
            str(tmp_path / "run"),
            "--max-cases",
            "1",
        ]
    )

    assert args.command == "run"
    assert args.max_cases == 1


def test_cli_parser_defaults_to_supervisor_benchmark(tmp_path):
    parser = build_parser()
    args = parser.parse_args(
        [
            "run",
            "--dataset",
            "eval/deep_research_benchmark/datasets/seed_tasks.jsonl",
            "--output",
            str(tmp_path / "run"),
        ]
    )

    assert args.strategy == "supervisor_workers"
    assert args.max_cases == 10
    assert args.timeout_s == 900.0


def test_run_config_defaults_to_heavy_supervisor():
    config = RunConfig(
        dataset_path=ROOT / "eval" / "deep_research_benchmark" / "datasets" / "seed_tasks.jsonl",
        output_dir=ROOT / "eval" / "deep_research_benchmark" / "results" / "test",
    )

    assert config.strategy == "supervisor_workers"
    assert config.max_cases == 10
    assert config.timeout_s == 900.0
    assert config.deepsearch_config["deepsearch_supervisor_rounds"] == 3
    assert config.deepsearch_config["deepsearch_supervisor_max_workers"] == 4
    assert config.deepsearch_config["deepsearch_supervisor_queries_per_worker"] == 3
    assert config.deepsearch_config["deepsearch_supervisor_parallel_workers"] == 2
    assert config.deepsearch_config["deepsearch_results_per_query"] == 8
    assert config.deepsearch_config["deepsearch_max_seconds"] == 840
    assert config.deepsearch_config["deepsearch_supervisor_fetch_passages"] is True
    assert config.deepsearch_config["deepsearch_enable_claim_ledger"] is True
    assert config.deepsearch_config["deepsearch_claim_grounding_gate_enabled"] is True
    assert config.deepsearch_config["deepsearch_final_verifier_revise"] is True
    assert config.deepsearch_config["deepsearch_citation_repair_enabled"] is True
    assert config.deepsearch_config["deepsearch_evidence_item_cap"] == 160
    assert config.deepsearch_config["deepsearch_passage_cap"] == 40
    assert config.deepsearch_config["deepsearch_sectioned_report_adaptive"] is True
    assert config.deepsearch_config["deepsearch_source_curator_enabled"] is True
    assert config.deepsearch_config["deepsearch_fact_card_cap"] == 80
    assert config.deepsearch_config["deepsearch_prewrite_gap_followup_enabled"] is True
    assert config.deepsearch_config == DEFAULT_SUPERVISOR_DEEPSEARCH_CONFIG


def test_build_research_payload_forces_supervisor_strategy():
    task = load_tasks(
        ROOT / "eval" / "deep_research_benchmark" / "datasets" / "seed_tasks.jsonl",
        max_cases=1,
    )[0]
    config = RunConfig(
        dataset_path=ROOT / "eval" / "deep_research_benchmark" / "datasets" / "seed_tasks.jsonl",
        output_dir=ROOT / "eval" / "deep_research_benchmark" / "results" / "test",
        strategy="supervisor",
        deepsearch_config={"deepsearch_supervisor_rounds": 2},
    )

    payload = build_research_payload(task, config)

    assert payload["search_mode"]["useAgent"] is True
    assert payload["search_mode"]["useDeepSearch"] is True
    assert payload["deepsearch_config"]["deepsearch_strategy"] == "supervisor_workers"
    assert payload["deepsearch_config"]["deepsearch_mode"] == "supervisor_workers"
    assert payload["deepsearch_config"]["deepsearch_supervisor_rounds"] == 2
    assert payload["deepsearch_config"]["source_policy"] == "web"
    assert payload["research_brief"]["expected_fields"] == task.expected_dimensions
    assert payload["research_brief"]["constraints"]["domain"] == task.domain


def test_build_research_payload_rejects_non_supervisor_strategy():
    task = load_tasks(
        ROOT / "eval" / "deep_research_benchmark" / "datasets" / "seed_tasks.jsonl",
        max_cases=1,
    )[0]
    config = RunConfig(
        dataset_path=ROOT / "eval" / "deep_research_benchmark" / "datasets" / "seed_tasks.jsonl",
        output_dir=ROOT / "eval" / "deep_research_benchmark" / "results" / "test",
        strategy="tree",
    )

    with pytest.raises(ValueError):
        build_research_payload(task, config)


def test_research_deepsearch_config_whitelist():
    import main

    cleaned = main._safe_research_deepsearch_config(
        {
            "deepsearch_strategy": "supervisor_workers",
            "deepsearch_supervisor_rounds": 3,
            "deepsearch_max_research_units": 6,
            "deepsearch_max_search_queries": 12,
            "deepsearch_max_tool_calls_per_unit": 3,
            "deepsearch_enable_claim_ledger": True,
            "deepsearch_supervisor_fetch_passages": True,
            "deepsearch_claim_verifier_use_passages": True,
            "deepsearch_claim_verifier_min_overlap_tokens": 2,
            "deepsearch_claim_verifier_max_evidence_per_claim": 4,
            "deepsearch_claim_grounding_gate_enabled": True,
            "deepsearch_claim_grounding_max_passes": 3,
            "deepsearch_citation_repair_enabled": True,
            "deepsearch_loop_max_repeated_query": 1,
            "deepsearch_loop_max_empty_result_streak": 2,
            "mcp_max_tools": 2,
            "deepsearch_evidence_item_cap": 120,
            "deepsearch_fact_card_cap": 60,
            "deepsearch_prewrite_gap_followup_queries": 1,
            "deepsearch_stage_warn_after_s": 30.0,
            "deepsearch_section_evidence_cap": 6,
            "writer_model": "deepseek-v4-pro",
            "search_summary_model": "deepseek-v4-flash",
            "verifier_model": "deepseek-v4-flash",
            "deepsearch_sectioned_report_review": {"action": "approve"},
            "source_providers": ["web", "mcp"],
            "unknown_key": "drop",
            "agent_profile": {"unsafe": True},
        }
    )

    assert cleaned == {
        "deepsearch_strategy": "supervisor_workers",
        "deepsearch_supervisor_rounds": 3,
        "deepsearch_max_research_units": 6,
        "deepsearch_max_search_queries": 12,
        "deepsearch_max_tool_calls_per_unit": 3,
        "deepsearch_enable_claim_ledger": True,
        "deepsearch_supervisor_fetch_passages": True,
        "deepsearch_claim_verifier_use_passages": True,
        "deepsearch_claim_verifier_min_overlap_tokens": 2,
        "deepsearch_claim_verifier_max_evidence_per_claim": 4,
        "deepsearch_claim_grounding_gate_enabled": True,
        "deepsearch_claim_grounding_max_passes": 3,
        "deepsearch_citation_repair_enabled": True,
        "deepsearch_loop_max_repeated_query": 1,
        "deepsearch_loop_max_empty_result_streak": 2,
        "mcp_max_tools": 2,
        "deepsearch_evidence_item_cap": 120,
        "deepsearch_fact_card_cap": 60,
        "deepsearch_prewrite_gap_followup_queries": 1,
        "deepsearch_stage_warn_after_s": 30.0,
        "deepsearch_section_evidence_cap": 6,
        "writer_model": "deepseek-v4-pro",
        "search_summary_model": "deepseek-v4-flash",
        "verifier_model": "deepseek-v4-flash",
        "deepsearch_sectioned_report_review": {"action": "approve"},
        "source_providers": ["web", "mcp"],
    }


@pytest.mark.asyncio
async def test_research_sse_passes_deepsearch_config(monkeypatch):
    import main

    captured: dict[str, Any] = {}

    async def fake_stream(*_args, **kwargs):
        captured.update(kwargs)
        yield "0:{\"type\":\"done\",\"data\":{}}\n"

    class DummyState:
        principal_id = ""

    class DummyRequest:
        state = DummyState()

        async def is_disconnected(self):
            return False

    monkeypatch.setattr(main.settings, "openai_api_key", "test-key")
    monkeypatch.setattr(main, "_stream_agent_events_call", fake_stream)

    payload = main.ResearchRequest(
        query="test supervisor benchmark",
        search_mode={"useAgent": True, "useDeepSearch": True},
        deepsearch_config={
            "deepsearch_strategy": "supervisor_workers",
            "deepsearch_supervisor_rounds": 3,
            "unknown_key": "drop",
        },
        research_brief={"original_query": "test supervisor benchmark", "expected_fields": ["evidence"]},
    )
    response = await main.research_sse(DummyRequest(), payload)

    assert isinstance(response, StreamingResponse)
    async for _chunk in response.body_iterator:
        if captured:
            break

    assert captured["deepsearch_config"]["deepsearch_strategy"] == "supervisor_workers"
    assert captured["deepsearch_config"]["deepsearch_supervisor_rounds"] == 3
    assert "unknown_key" in captured["deepsearch_config"]
    assert captured["research_brief"]["expected_fields"] == ["evidence"]


@pytest.mark.asyncio
async def test_runner_records_failed_case_without_openai_key(tmp_path, monkeypatch):
    import main

    monkeypatch.setattr(main.settings, "openai_api_key", "")
    task = load_tasks(
        ROOT / "eval" / "deep_research_benchmark" / "datasets" / "seed_tasks.jsonl",
        max_cases=1,
    )[0]
    config = RunConfig(
        dataset_path=ROOT / "eval" / "deep_research_benchmark" / "datasets" / "seed_tasks.jsonl",
        output_dir=tmp_path / "run",
        base_url="asgi",
        timeout_s=20.0,
        model="deepseek-v4-flash",
    )

    result = await run_case(task, config)

    assert result.status == "failed"
    assert "OPENAI_API_KEY" in result.error
    assert Path(result.events_path).exists()
    assert Path(result.report_path).exists()
    case_result = Path(config.output_dir / "cases" / task.id / "case_result.json")
    assert json.loads(case_result.read_text(encoding="utf-8"))["case_id"] == task.id
