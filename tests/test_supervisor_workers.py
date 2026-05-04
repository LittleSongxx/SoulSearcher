from agent.workflows.research_brief import build_research_brief
from agent.workflows.model_context_policy import (
    build_deepsearch_context_policy,
    is_token_limit_error,
    resolve_model_context_window,
)
from agent.workflows.deepsearch_model_profile import build_deepsearch_model_profile
from agent.workflows.research_pipeline import build_supervisor_workers_pipeline_artifact
from agent.workflows.research_task_runtime import ResearchTaskRuntime
from agent.workflows.report_plan import build_sectioned_report_artifact, build_sectioned_report_plan
from agent.workflows.sectioned_report import (
    apply_sectioned_report_review,
    compile_sectioned_report,
    grade_section_content,
)
from agent.workflows.supervisor_workers import (
    build_intermediate_steps,
    build_worker_run,
    build_worker_tasks,
    decide_supervisor_next_step,
)


def test_build_worker_tasks_uses_brief_fields_and_missing_topics():
    brief = build_research_brief(
        {
            "input": "compare agent frameworks",
            "research_brief": {
                "original_query": "compare agent frameworks",
                "clarified_goal": "compare agent frameworks",
                "expected_fields": ["architecture", "benchmarks"],
            },
        },
        {},
    )

    tasks = build_worker_tasks(
        brief=brief,
        round_index=1,
        max_workers=2,
        queries_per_worker=2,
        missing_topics=["risk"],
    )

    assert len(tasks) == 2
    assert tasks[0].focus == "risk"
    assert tasks[0].context_id.startswith("ctx_worker_")
    assert len(tasks[0].queries) == 2


def test_build_worker_tasks_adds_rubric_aware_roles():
    brief = build_research_brief(
        {
            "input": "compare agent frameworks",
            "research_brief": {
                "original_query": "compare agent frameworks",
                "clarified_goal": "compare agent frameworks",
                "expected_fields": ["comparison_dimensions"],
                "constraints": {
                    "judge_rubric": {"requires_citations_for_claims": True},
                    "source_constraints": {"preferred": ["official"]},
                },
            },
        },
        {},
    )

    tasks = build_worker_tasks(
        brief=brief,
        round_index=1,
        max_workers=3,
        queries_per_worker=1,
    )

    assert [task.focus for task in tasks[:2]] == ["source_triage", "claim_verification"]
    assert "official primary sources" in tasks[0].topic


def test_supervisor_decides_continue_for_failed_missing_dimension_gate():
    decision = decide_supervisor_next_step(
        round_index=1,
        max_rounds=2,
        worker_runs=[{"worker_id": "w1", "result_count": 3}],
        gate_payload=[
            {
                "name": "query_coverage",
                "status": "fail",
                "details": {"missing_dimensions": ["freshness"]},
            }
        ],
        diagnostics={"query_coverage_score": 0.2},
    )

    assert decision.action == "continue"
    assert decision.next_worker_topics == ["freshness"]
    assert decision.failed_gates == ["query_coverage"]


def test_supervisor_decides_continue_for_claim_and_citation_gates():
    decision = decide_supervisor_next_step(
        round_index=1,
        max_rounds=2,
        worker_runs=[{"worker_id": "w1", "result_count": 3}],
        gate_payload=[
            {"name": "citation_coverage", "status": "fail"},
            {"name": "claim_verifier", "status": "fail"},
        ],
        diagnostics={"query_coverage_score": 1.0},
    )

    assert decision.action == "continue"
    assert decision.next_worker_topics == ["citation_evidence", "claim_verification"]


def test_worker_run_and_intermediate_steps_are_serializable():
    brief = build_research_brief({"input": "q"}, {})
    task = build_worker_tasks(
        brief=brief,
        round_index=1,
        max_workers=1,
        queries_per_worker=1,
    )[0]
    worker_run = build_worker_run(
        task=task,
        results=[{"provider": "web", "title": "A"}],
        evidence_items=[{"id": "ev1"}],
        summary="summary",
    ).to_dict()
    steps = build_intermediate_steps(
        worker_runs=[worker_run],
        supervisor_decisions=[{"round_index": 1, "action": "synthesize", "reason": "enough"}],
    )

    assert worker_run["round_index"] == 1
    assert worker_run["provider_breakdown"] == {"web": 1}
    assert [step["order"] for step in steps] == [1, 2]
    assert {step["type"] for step in steps} == {"worker_run", "supervisor_decision"}


def test_research_task_runtime_tracks_subtask_lifecycle():
    brief = build_research_brief({"input": "q"}, {})
    task = build_worker_tasks(
        brief=brief,
        round_index=1,
        max_workers=1,
        queries_per_worker=1,
    )[0]
    runtime = ResearchTaskRuntime(mode="supervisor_workers", parent_id="root")

    registered = runtime.register_task(task)
    started = runtime.start_task(task.worker_id)
    completed = runtime.complete_task(
        task.worker_id,
        result_count=2,
        evidence_count=1,
        compressed_summary="summary",
        raw_notes=["raw note"],
    )
    artifact = runtime.to_artifact()

    assert registered["status"] == "pending"
    assert started["status"] == "running"
    assert completed["status"] == "completed"
    assert completed["result_count"] == 2
    assert completed["evidence_count"] == 1
    assert artifact["subtask_count"] == 1
    assert artifact["status_counts"] == {"completed": 1}
    assert [event["type"] for event in artifact["events"]] == ["registered", "started", "completed"]


def test_build_supervisor_workers_pipeline_artifact_has_stage_boundaries():
    artifact = build_supervisor_workers_pipeline_artifact(
        research_brief={
            "original_query": "q",
            "clarified_goal": "q",
            "expected_fields": ["architecture"],
        },
        task_runtime={"subtask_count": 1, "status_counts": {"completed": 1}},
        worker_runs=[
            {
                "worker_id": "w1",
                "context_id": "ctx_w1",
                "round_index": 1,
                "focus": "architecture",
                "summary": "compressed finding",
            }
        ],
        supervisor_decisions=[{"round_index": 1, "action": "synthesize"}],
        decision_log=[{"type": "worker_reflection"}],
        summary_notes=["architecture: compressed finding"],
        evidence_items=[{"id": "ev1"}],
        claim_ledger=[{"claim": "c"}],
        quality_summary={"citation_coverage_score": 1.0, "claim_verifier_unsupported": 0},
        final_report="final",
    )

    stages = {stage["name"]: stage for stage in artifact["stages"]}

    assert artifact["mode"] == "supervisor_workers"
    assert list(stages) == [
        "research_brief",
        "supervisor",
        "researcher",
        "compression",
        "writer",
        "verifier",
    ]
    assert stages["researcher"]["status"] == "completed"
    assert stages["compression"]["outputs"]["compressed_research"][0]["summary"] == "compressed finding"
    assert stages["writer"]["outputs"]["report_length"] == len("final")


def test_model_context_policy_resolves_stage_budgets_and_token_limit_errors():
    policy = build_deepsearch_context_policy(
        {
            "planning": "openai:gpt-4.1-mini",
            "research": "anthropic:claude-sonnet-4-20250514",
            "writing": "unknown-model",
        }
    )

    stages = {stage["stage"]: stage for stage in policy["stages"]}

    assert resolve_model_context_window("openai:gpt-4.1-mini") == 1_047_576
    assert stages["planning"]["usable_tokens"] < stages["planning"]["context_window"]
    assert stages["research"]["context_window"] == 200_000
    assert stages["writing"]["context_window"] == 128_000
    assert is_token_limit_error(RuntimeError("maximum context length exceeded"))
    assert not is_token_limit_error(RuntimeError("temporary network error"))


def test_build_deepsearch_model_profile_supports_runtime_aliases():
    profile = build_deepsearch_model_profile(
        {
            "configurable": {
                "deepsearch_model_profile": "cost_split",
                "supervisor_model": "planner-x",
                "query_model": "query-x",
                "search_summary_model": "summary-x",
                "worker_model": "worker-x",
                "compression_model": "compress-x",
                "writer_model": "writer-x",
                "verifier_model": "verify-x",
            }
        },
        lambda task_type, config: f"router-{task_type}",
    )

    stages = {stage["stage"]: stage for stage in profile["stages"]}

    assert profile["profile_name"] == "cost_split"
    assert profile["stage_count"] == 7
    assert profile["models"]["worker_model"] == "worker-x"
    assert stages["query_model"]["task_type"] == "query_gen"
    assert stages["compression_model"]["override_key"] == "compression_model"
    assert all(stage["source"] == "runtime" for stage in profile["stages"])


def test_build_sectioned_report_plan_from_brief_and_workers():
    plan = build_sectioned_report_plan(
        research_brief={
            "original_query": "compare agent frameworks",
            "clarified_goal": "compare agent frameworks",
            "expected_fields": ["architecture", "benchmarks"],
        },
        worker_runs=[
            {"worker_id": "w1", "focus": "architecture"},
            {"worker_id": "w2", "focus": "benchmarks"},
        ],
        evidence_items=[{"id": "ev1"}, {"id": "ev2"}],
    )

    sections = {section["focus"]: section for section in plan["sections"]}

    assert plan["status"] == "planned"
    assert plan["section_count"] >= 5
    assert sections["architecture"]["related_worker_ids"] == ["w1"]
    assert sections["benchmarks"]["related_worker_ids"] == ["w2"]
    assert sections["evidence"]["evidence_item_count"] == 2
    assert sections["synthesis"]["research_required"] is False


def test_build_sectioned_report_artifact_is_feature_flagged():
    report_plan = build_sectioned_report_plan(
        research_brief={"original_query": "q", "expected_fields": ["architecture"]},
        worker_runs=[{"worker_id": "w1", "focus": "architecture"}],
        evidence_items=[{"id": "ev1"}],
    )
    disabled = build_sectioned_report_artifact(report_plan, {"configurable": {}})
    enabled = build_sectioned_report_artifact(
        report_plan,
        {
            "configurable": {
                "deepsearch_sectioned_report": True,
                "deepsearch_sectioned_report_requires_approval": True,
            }
        },
    )

    assert disabled["enabled"] is False
    assert disabled["section_count"] == 0
    assert enabled["enabled"] is True
    assert enabled["review_required"] is True
    assert enabled["section_count"] == report_plan["section_count"]


def test_sectioned_report_review_edit_grade_and_compile():
    report_plan = build_sectioned_report_plan(
        research_brief={"original_query": "q", "expected_fields": ["architecture"]},
        worker_runs=[{"worker_id": "w1", "focus": "architecture"}],
        evidence_items=[{"id": "ev1"}],
    )
    edited = apply_sectioned_report_review(
        report_plan,
        {
            "action": "edit",
            "sections": [
                {
                    "section_id": report_plan["sections"][1]["section_id"],
                    "title": "Architecture Deep Dive",
                    "focus": "architecture",
                }
            ],
        },
        approval_required=True,
    )
    pending = apply_sectioned_report_review(report_plan, None, approval_required=True)
    grade = grade_section_content(
        edited["sections"][0],
        "This section explains the architecture with enough supporting details.",
        [{"id": "ev1"}],
        min_chars=20,
        min_evidence=1,
    )
    failed_grade = grade_section_content(
        edited["sections"][0],
        "short",
        [],
        min_chars=20,
        min_evidence=1,
    )
    compiled = compile_sectioned_report(
        [{"title": "Architecture Deep Dive", "content": "Architecture body."}]
    )

    assert pending["review_status"] == "pending_approval"
    assert pending["should_execute"] is False
    assert edited["review_status"] == "edited"
    assert edited["should_execute"] is True
    assert edited["sections"][0]["title"] == "Architecture Deep Dive"
    assert grade["status"] == "pass"
    assert failed_grade["status"] == "fail"
    assert failed_grade["follow_up_queries"]
    assert "## Architecture Deep Dive" in compiled
