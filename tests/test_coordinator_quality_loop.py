import pytest

from agent.workflows import nodes
from agent.workflows.agents.coordinator import CoordinatorAction, ResearchCoordinator


class _NeverInvokeLLM:
    def invoke(self, _messages, config=None):
        raise AssertionError(
            "LLM should not be called when quality guardrails decide action"
        )


# ── Quality-driven decision tests (report already exists) ──


def test_coordinator_prefers_research_for_low_quality_signals():
    coordinator = ResearchCoordinator(_NeverInvokeLLM())

    decision = coordinator.decide_next_action(
        topic="AI chips",
        num_queries=4,
        num_sources=8,
        num_summaries=2,
        current_epoch=1,
        max_epochs=4,
        quality_score=0.42,
        quality_gap_count=3,
        citation_accuracy=0.3,
        has_report=True,
    )

    assert decision.action == CoordinatorAction.RESEARCH


def test_coordinator_allows_complete_for_high_quality_signals():
    coordinator = ResearchCoordinator(_NeverInvokeLLM())

    decision = coordinator.decide_next_action(
        topic="AI chips",
        num_queries=4,
        num_sources=12,
        num_summaries=3,
        current_epoch=1,
        max_epochs=4,
        quality_score=0.91,
        quality_gap_count=0,
        citation_accuracy=0.86,
        has_report=True,
    )

    assert decision.action == CoordinatorAction.COMPLETE


# ── Rule 0: No queries → must plan ──


def test_coordinator_plans_when_no_queries():
    coordinator = ResearchCoordinator(_NeverInvokeLLM())

    decision = coordinator.decide_next_action(
        topic="AI chips",
        num_queries=0,
        num_sources=0,
        num_summaries=0,
        current_epoch=1,
        max_epochs=4,
    )

    assert decision.action == CoordinatorAction.PLAN


# ── Rule 1: Max epochs → synthesize if no report, else complete ──


def test_coordinator_synthesizes_at_max_epochs_without_report():
    coordinator = ResearchCoordinator(_NeverInvokeLLM())

    decision = coordinator.decide_next_action(
        topic="AI chips",
        num_queries=4,
        num_sources=8,
        num_summaries=2,
        current_epoch=4,
        max_epochs=4,
        has_report=False,
    )

    assert decision.action == CoordinatorAction.SYNTHESIZE


def test_coordinator_completes_at_max_epochs_with_report():
    coordinator = ResearchCoordinator(_NeverInvokeLLM())

    decision = coordinator.decide_next_action(
        topic="AI chips",
        num_queries=4,
        num_sources=8,
        num_summaries=2,
        current_epoch=4,
        max_epochs=4,
        has_report=True,
    )

    assert decision.action == CoordinatorAction.COMPLETE


# ── Rule 2: Have sources, no report → synthesize ──


def test_coordinator_synthesizes_when_sources_but_no_report():
    coordinator = ResearchCoordinator(_NeverInvokeLLM())

    decision = coordinator.decide_next_action(
        topic="AI chips",
        num_queries=4,
        num_sources=8,
        num_summaries=0,
        current_epoch=1,
        max_epochs=4,
        has_report=False,
    )

    assert decision.action == CoordinatorAction.SYNTHESIZE


# ── coordinator_node guard tests ──


def test_coordinator_node_routes_low_quality_to_research_without_llm(monkeypatch):
    monkeypatch.setattr(nodes, "_chat_model", lambda *args, **kwargs: _NeverInvokeLLM())

    state = {
        "input": "Summarize AI chip market",
        "research_plan": ["q1"],
        "scraped_content": [{"query": "q1", "results": []}],
        "summary_notes": ["draft summary"],
        "revision_count": 0,
        "max_revisions": 2,
        "quality_overall_score": 0.4,
        "quality_gap_count": 2,
        "eval_dimensions": {"citation_coverage": 0.35, "coverage": 0.7},
        "missing_topics": ["pricing"],
        "final_report": "Some existing report text here",
    }

    result = nodes.coordinator_node(state, config={})

    assert result["coordinator_action"] == "research"


def test_coordinator_node_never_completes_without_report(monkeypatch):
    """Even if decide_next_action returns COMPLETE, the node guard overrides to synthesize."""
    monkeypatch.setattr(nodes, "_chat_model", lambda *args, **kwargs: _NeverInvokeLLM())

    state = {
        "input": "AI chips",
        "research_plan": ["q1"],
        "scraped_content": [{"query": "q1", "results": []}],
        "summary_notes": ["summary"],
        "revision_count": 0,
        "max_revisions": 0,  # Forces max_epochs=1, coord_iters=1 >= max_epochs → COMPLETE
        "final_report": "",  # Empty report
        "draft_report": "",
    }

    result = nodes.coordinator_node(state, config={})

    # Should be overridden to synthesize (has sources, no report)
    assert result["coordinator_action"] == "synthesize"


def test_coordinator_node_never_synthesizes_without_sources(monkeypatch):
    """If no sources, override synthesize to plan."""
    monkeypatch.setattr(nodes, "_chat_model", lambda *args, **kwargs: _NeverInvokeLLM())

    state = {
        "input": "AI chips",
        "research_plan": [],
        "scraped_content": [],
        "summary_notes": [],
        "revision_count": 0,
        "max_revisions": 0,
        "final_report": "",
        "draft_report": "",
    }

    result = nodes.coordinator_node(state, config={})

    # No sources, no queries → plan
    assert result["coordinator_action"] == "plan"


def test_coordinator_node_tracks_iterations(monkeypatch):
    """coordinator_iterations increments on each call."""
    monkeypatch.setattr(nodes, "_chat_model", lambda *args, **kwargs: _NeverInvokeLLM())

    state = {
        "input": "AI chips",
        "research_plan": ["q1"],
        "scraped_content": [{"query": "q1", "results": []}],
        "summary_notes": [],
        "revision_count": 0,
        "max_revisions": 2,
        "coordinator_iterations": 0,
        "final_report": "",
    }

    result = nodes.coordinator_node(state, config={})
    assert result["coordinator_iterations"] == 1

    # Simulate second call
    state["coordinator_iterations"] = result["coordinator_iterations"]
    state["final_report"] = "A report"
    result2 = nodes.coordinator_node(state, config={})
    assert result2["coordinator_iterations"] == 2


def test_coordinator_node_forces_completion_after_max_iterations(monkeypatch):
    """After max_revisions+1 iterations, force complete."""
    monkeypatch.setattr(nodes, "_chat_model", lambda *args, **kwargs: _NeverInvokeLLM())

    state = {
        "input": "AI chips",
        "research_plan": ["q1"],
        "scraped_content": [{"query": "q1", "results": []}],
        "summary_notes": [],
        "revision_count": 0,
        "max_revisions": 2,
        "coordinator_iterations": 3,  # Will become 4, which > max_revisions+1=3
        "final_report": "Some report",
    }

    result = nodes.coordinator_node(state, config={})
    assert result["coordinator_action"] == "complete"
    assert "Forced completion" in result["coordinator_reasoning"]


def test_coordinator_node_forces_synthesize_when_over_limit_no_report(monkeypatch):
    """Over iteration limit but no report → force synthesize instead of complete."""
    monkeypatch.setattr(nodes, "_chat_model", lambda *args, **kwargs: _NeverInvokeLLM())

    state = {
        "input": "AI chips",
        "research_plan": ["q1"],
        "scraped_content": [{"query": "q1", "results": []}],
        "summary_notes": [],
        "revision_count": 0,
        "max_revisions": 2,
        "coordinator_iterations": 3,
        "final_report": "",
        "draft_report": "",
    }

    result = nodes.coordinator_node(state, config={})
    assert result["coordinator_action"] == "synthesize"
    assert "Forced synthesize" in result["coordinator_reasoning"]
