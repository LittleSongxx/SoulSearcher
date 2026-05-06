from agent.workflows.claim_verifier import ClaimCheck, ClaimStatus
from agent.workflows.deepsearch_stage_runtime import DeepSearchStageRuntime, build_fallback_artifact
from agent.workflows.quality_gates import QualityGatePolicy, evaluate_quality_gates, serialize_gate_results
from agent.workflows.research_brief_review import build_research_brief_review_artifact
from agent.workflows.semantic_claim_verifier import enrich_claim_checks_with_semantics
from agent.workflows.source_quality import build_source_quality_artifact, quality_summary_from_source_quality
from agent.workflows.supervisor_workers import (
    build_branch_diagnostics_artifact,
    build_worker_orchestration_artifact,
)


def test_source_quality_scores_diversity_and_primary_ratio():
    artifact = build_source_quality_artifact(
        sources=[
            {"url": "https://sec.gov/filing/abc", "title": "Annual report filing"},
            {"url": "https://example.edu/paper", "title": "University benchmark"},
            {"url": "https://medium.com/post", "title": "Opinion post"},
        ],
        evidence_items=[],
        search_runs=[],
    )

    assert artifact["source_count"] == 3
    assert artifact["primary_source_ratio"] >= 0.6
    assert artifact["low_value_source_ratio"] > 0
    summary = quality_summary_from_source_quality(artifact)
    assert summary["source_diversity_score"] == artifact["source_diversity_score"]
    assert "source_credibility_score" in summary


def test_quality_gates_include_source_quality_dimensions():
    gates = evaluate_quality_gates(
        {"query_coverage_score": 1.0, "freshness_summary": {}, "time_sensitive_query": False},
        quality_summary={
            "source_diversity_score": 0.1,
            "primary_source_ratio": 0.1,
            "low_value_source_ratio": 0.8,
        },
        policy=QualityGatePolicy(
            min_source_diversity=0.4,
            min_primary_source_ratio=0.3,
            max_low_value_source_ratio=0.5,
        ),
    )

    serialized = serialize_gate_results(gates)
    assert any(g["name"] == "source_diversity" and g["status"] == "fail" for g in serialized)
    assert any(g["name"] == "primary_source_ratio" and g["status"] == "fail" for g in serialized)
    assert any(g["name"] == "low_value_sources" and g["status"] == "fail" for g in serialized)


def test_semantic_claim_verifier_can_upgrade_supported_by_overlap():
    checks = [
        ClaimCheck(
            claim="The company revenue increased in 2024 according to annual report",
            status=ClaimStatus.UNSUPPORTED,
            score=0.0,
            notes="no deterministic match",
        )
    ]

    enriched, summary = enrich_claim_checks_with_semantics(
        checks,
        evidence_items=[
            {
                "url": "https://example.com/report",
                "text": "The company revenue increased in 2024 according to its annual report and earnings release.",
            }
        ],
        config={"deepsearch_semantic_claim_verifier_enabled": True},
    )

    assert summary["semantic_claim_verifier_enabled"] is True
    assert enriched[0].deterministic_status == "unsupported"
    assert enriched[0].status == "verified"
    assert summary["semantic_claim_verifier_changed"] == 1


def test_stage_runtime_records_resumable_stage_artifact():
    runtime = DeepSearchStageRuntime(mode="supervisor_workers", run_id="thread_1")
    handle = runtime.start("worker_dispatch", worker_count=2)
    runtime.finish(handle, result_count=4)
    failed = runtime.start("verifier")
    runtime.fail(failed, "boom")

    artifact = runtime.artifact()
    assert artifact["mode"] == "supervisor_workers"
    assert artifact["resumable"] is True
    assert artifact["failed_stage"] == "verifier"
    assert artifact["status_counts"]["completed"] == 1
    assert artifact["status_counts"]["failed"] == 1


def test_brief_review_artifact_respects_required_approval():
    artifact = build_research_brief_review_artifact(
        research_brief={"original_query": "q", "clarified_goal": "q", "source_policy": "web"},
        config={"deepsearch_brief_review_required": True},
    )

    assert artifact["approval_required"] is True
    assert artifact["status"] == "pending_approval"
    assert artifact["next_action"] == "await_user_review"
    assert "expected_fields" in artifact["missing_fields"]


def test_worker_orchestration_artifact_exposes_partial_results():
    artifact = build_worker_orchestration_artifact(
        worker_runs=[
            {"worker_id": "w1", "round_index": 1, "status": "completed", "result_count": 2},
            {"worker_id": "w2", "round_index": 1, "status": "failed", "errors": ["timeout"]},
        ],
        supervisor_decisions=[{"round_index": 1, "action": "continue"}],
        parallel_workers=2,
    )

    assert artifact["dispatch_model"] == "parallel_batch"
    assert artifact["partial_result_count"] == 2
    assert artifact["status_counts"]["failed"] == 1
    assert artifact["failed_workers"][0]["worker_id"] == "w2"


def test_branch_diagnostics_artifact_tracks_duplicates_and_conflicts():
    artifact = build_branch_diagnostics_artifact(
        worker_runs=[
            {"worker_id": "w1", "focus": "market", "evidence_count": 2},
            {"worker_id": "w2", "focus": "market", "errors": ["conflict"], "evidence_count": 1},
        ],
        evidence_items=[
            {"url": "https://example.com/a"},
            {"url": "https://example.com/a"},
        ],
    )

    assert artifact["branch_count"] == 2
    assert artifact["duplicate_focus_count"] == 1
    assert artifact["conflict_hint_count"] >= 2
    assert artifact["merge_strategy"] == "deduplicate_by_source_and_focus"


def test_fallback_artifact_is_explicit():
    artifact = build_fallback_artifact(
        source_strategy="supervisor_workers",
        fallback_strategy="linear",
        error=RuntimeError("model timeout"),
    )

    assert artifact["source_strategy"] == "supervisor_workers"
    assert artifact["fallback_strategy"] == "linear"
    assert "model timeout" in artifact["reason"]
