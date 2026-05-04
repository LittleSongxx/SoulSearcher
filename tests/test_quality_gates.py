from agent.workflows.quality_gates import (
    QualityGatePolicy,
    evaluate_quality_gates,
    missing_topics_from_gates,
    serialize_gate_results,
)


def test_quality_gates_recommend_gap_queries_for_low_coverage():
    gates = evaluate_quality_gates(
        {
            "query_coverage_score": 0.25,
            "query_coverage": {"missing_dimensions": ["cost", "risks"]},
            "freshness_summary": {"fresh_30_ratio": 1.0},
            "time_sensitive_query": False,
        },
        policy=QualityGatePolicy(min_query_coverage=0.6),
        epoch=2,
    )

    serialized = serialize_gate_results(gates)
    assert any(gate["name"] == "query_coverage" and gate["status"] == "fail" for gate in serialized)
    assert missing_topics_from_gates(gates) == ["cost", "risks"]


def test_quality_gates_fail_freshness_for_time_sensitive_query():
    gates = evaluate_quality_gates(
        {
            "query_coverage_score": 1.0,
            "freshness_summary": {"fresh_30_ratio": 0.1},
            "time_sensitive_query": True,
        },
        policy=QualityGatePolicy(min_freshness_ratio_30d=0.5),
        epoch=1,
    )

    freshness = [gate for gate in serialize_gate_results(gates) if gate["name"] == "freshness"][0]
    assert freshness["status"] == "fail"
    assert freshness["action"] == "prefer_fresh_provider_profile"


def test_quality_gates_check_final_citation_and_claims():
    gates = evaluate_quality_gates(
        {"query_coverage_score": 1.0, "freshness_summary": {}, "time_sensitive_query": False},
        quality_summary={
            "citation_coverage": 0.2,
            "claim_verifier_total": 2,
            "claim_verifier_unsupported": 1,
            "claim_verifier_contradicted": 0,
        },
        policy=QualityGatePolicy(min_citation_coverage=0.6, max_unsupported_claims=0),
        epoch=1,
    )

    serialized = serialize_gate_results(gates)
    assert any(gate["name"] == "citation_coverage" and gate["status"] == "fail" for gate in serialized)
    assert any(gate["name"] == "claim_verifier" and gate["status"] == "fail" for gate in serialized)
