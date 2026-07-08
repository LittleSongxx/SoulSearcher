from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage


def test_build_initial_state_preserves_user_query_with_system_messages():
    from agent.core.state import build_initial_state

    state = build_initial_state(
        input_text="分析中国低空经济产业机会",
        messages=[SystemMessage(content="memory context")],
    )

    assert state["input"] == "分析中国低空经济产业机会"
    assert isinstance(state["messages"][0], SystemMessage)
    assert isinstance(state["messages"][-1], HumanMessage)
    assert state["vertical_profile"] == {}
    assert state["research_tasks"] == []
    assert state["datapoints"] == []
    assert state["claim_checks"] == []
    assert state["agent_trace"] == []


def test_fixed_role_sequence_is_the_only_graph_mainline():
    from agent.workflows.vertical_research import ROLE_SEQUENCE

    assert ROLE_SEQUENCE == [
        "DomainRouter",
        "ResearchArchitect",
        "SourceScout",
        "EvidenceCurator",
        "DataAnalyst",
        "ClaimVerifier",
        "CriticReviewer",
        "LeadWriter",
        "QualityGate",
        "FinalReport",
    ]


def test_domain_router_creates_single_vertical_profile():
    from agent.workflows.vertical_research import DOMAIN_PROFILE_ID, domain_router

    result = domain_router({"input": "分析新能源汽车产业政策和市场格局"})

    assert result["vertical_profile"]["profile_id"] == DOMAIN_PROFILE_ID
    assert result["vertical_profile"]["domain_type"] == "policy"
    assert "industry" in result["vertical_profile"]["dimensions"]
    assert result["vertical_brief"]["profile_id"] == DOMAIN_PROFILE_ID


def test_research_architect_emits_vertical_task_schema():
    from agent.workflows.vertical_research import research_architect

    result = research_architect({"research_brief": "分析AI眼镜产业链"})
    tasks = result["research_tasks"]["value"]

    assert len(tasks) == 5
    required = {
        "agent_role",
        "section_id",
        "research_dimension",
        "required_evidence_types",
        "required_metrics",
        "source_priority",
        "freshness_requirement",
        "requires_data",
        "requires_chart",
    }
    assert all(required.issubset(task) for task in tasks)
    assert {task["research_dimension"] for task in tasks} >= {"industry", "company", "policy"}


def test_source_scout_and_evidence_curator_write_vertical_metadata():
    from agent.workflows.vertical_research import evidence_curator, research_architect, source_scout

    state = {"research_brief": "分析商业航天市场"}
    state.update(research_architect(state))
    state["research_tasks"] = state["research_tasks"]["value"]
    scout = source_scout(state)
    state.update(scout)
    curated = state["curated_sources"]

    assert curated
    assert {"domain", "source_type", "authority_score", "freshness_score", "corroboration_key"}.issubset(curated[0]["metadata"])

    curated_state = evidence_curator(state)
    ledger = curated_state["evidence_items"]["value"]

    assert ledger
    first = ledger[0]
    assert first["source_type"]
    assert first["authority_score"] >= 0.7
    assert first["freshness_score"] > 0
    assert first["corroboration_key"]


def test_data_analyst_extracts_metric_value_unit_period():
    from agent.workflows.vertical_research import extract_vertical_datapoints

    evidence = [
        {
            "url": "https://example.com/report",
            "content": "2025年AI眼镜市场规模达到1280亿元，增长率为18%。",
            "metadata": {"section_id": "market_landscape", "domain": "industry", "authority_score": 0.9, "freshness_score": 0.95},
        }
    ]

    points = extract_vertical_datapoints(evidence)

    assert any(p["metric_name"] == "market_size" and p["metric_value"] == "1280" and p["unit"] == "亿元" for p in points)
    assert any(p["metric_name"] == "growth_rate" and p["metric_value"] == "18" for p in points)
    assert all(p["period"].startswith("2025") for p in points)


def test_claim_verifier_detects_numeric_mismatch_and_low_confidence_policy_source():
    from agent.workflows.claim_verifier import ClaimStatus, ClaimVerifier

    verifier = ClaimVerifier(min_overlap_tokens=1)
    mismatch = verifier.verify_claim(
        "2025年市场规模达到1280亿元。",
        [{"url": "https://example.com/a", "text": "2025年市场规模达到900亿元。", "source_type": "industry_report", "authority_score": 0.8}],
    )
    policy = verifier.verify_claim(
        "2025年发布AI产业监管政策。",
        [{"url": "https://blog.example.com/p", "text": "2025年发布AI产业监管政策。", "source_type": "web_source", "authority_score": 0.4}],
    )

    assert mismatch.status == ClaimStatus.CONTRADICTED
    assert mismatch.evidence_passages[0]["verification_issue"] == "numeric_mismatch"
    assert policy.status == ClaimStatus.VERIFIED
    assert "low confidence" in policy.notes


def test_vertical_evaluation_attributes_failures_to_fixed_roles():
    from agent.workflows.evaluation import run_vertical_evaluation
    from agent.workflows.vertical_research import research_architect

    tasks = research_architect({"research_brief": "分析机器人产业"})["research_tasks"]["value"]
    result = run_vertical_evaluation(
        report="# 分析机器人产业产业研究报告\n\n## 摘要\n缺少引用。",
        research_tasks=tasks,
        evidence_items=[],
        datapoints=[],
        claim_checks=[{"claim": "市场规模为100亿元", "status": "unsupported"}],
        critic_feedback=[{"responsible_agent": "DataAnalyst", "issue": "missing_required_datapoint"}],
    )

    assert result.overall_passed is False
    assert "SourceScout" in result.metadata["responsible_agents"]
    assert "DataAnalyst" in result.metadata["responsible_agents"]
    assert "ClaimVerifier" in result.metadata["responsible_agents"]


def test_fixed_workflow_nodes_can_run_offline_end_to_end():
    from agent.workflows.vertical_research import (
        claim_verifier_node,
        critic_reviewer,
        data_analyst,
        domain_router,
        evidence_curator,
        final_report_node,
        lead_writer,
        quality_gate,
        research_architect,
        source_scout,
    )

    state = {
        "input": "分析低空经济产业机会",
        "deepsearch_artifacts": {},
        "agent_trace": [],
        "evidence_items": [],
        "sources": [],
        "curated_sources": [],
    }
    for node in [
        domain_router,
        research_architect,
        source_scout,
        evidence_curator,
        data_analyst,
        claim_verifier_node,
        critic_reviewer,
        lead_writer,
        quality_gate,
        final_report_node,
    ]:
        patch = node(state)
        for key, value in patch.items():
            if isinstance(value, dict) and value.get("type") == "override":
                state[key] = value["value"]
            else:
                state[key] = value

    assert "产业研究报告" in state["final_report"]
    assert state["deepsearch_artifacts"]["final_report"] == state["final_report"]
    assert state["quality_summary"]["metadata"]["rubric"] == "industry_market_policy_research"
    assert [row["agent_role"] for row in state["agent_trace"]][-1] == "FinalReport"
