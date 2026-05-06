"""Tests for enhanced loop detection, sliding window, tool frequency, and guardrails."""

from __future__ import annotations

import pytest

from agent.workflows.research_loop_guard import (
    GuardrailDecision,
    LoopGuardDecision,
    LoopGuardPolicy,
    ResearchLoopGuard,
    ToolCallGuardrail,
    build_loop_guard_policy,
    build_tool_call_guardrail,
)


# ---------- LoopGuardPolicy defaults ----------


def test_policy_defaults() -> None:
    policy = LoopGuardPolicy()
    assert policy.warn_threshold == 3
    assert policy.hard_limit == 5
    assert policy.window_size == 20
    assert policy.tool_freq_warn == 30
    assert policy.tool_freq_hard_limit == 50


def test_build_policy_from_config() -> None:
    cfg = {
        "configurable": {
            "deepsearch_loop_warn_threshold": 2,
            "deepsearch_loop_hard_limit": 4,
            "deepsearch_loop_window_size": 10,
        }
    }
    policy = build_loop_guard_policy(cfg)
    assert policy.warn_threshold == 2
    assert policy.hard_limit == 4
    assert policy.window_size == 10


# ---------- Sliding window detection ----------


def test_sliding_window_warn() -> None:
    policy = LoopGuardPolicy(
        warn_threshold=2,
        hard_limit=4,
        window_size=10,
        max_repeated_tool_call=100,
    )
    guard = ResearchLoopGuard(policy)
    d1 = guard.before_tool_call("search", {"q": "test"})
    assert d1.allowed is True
    d2 = guard.before_tool_call("search", {"q": "test"})
    assert d2.allowed is True
    assert d2.warning
    assert "window" in d2.warning.lower()


def test_sliding_window_hard_stop() -> None:
    policy = LoopGuardPolicy(
        warn_threshold=2,
        hard_limit=3,
        window_size=10,
        max_repeated_tool_call=100,
    )
    guard = ResearchLoopGuard(policy)
    guard.before_tool_call("search", {"q": "test"})
    guard.before_tool_call("search", {"q": "test"})
    d3 = guard.before_tool_call("search", {"q": "test"})
    assert d3.allowed is False
    assert "forced stop" in d3.reason.lower()


def test_sliding_window_eviction() -> None:
    policy = LoopGuardPolicy(
        warn_threshold=3,
        hard_limit=4,
        window_size=4,
        max_repeated_tool_call=100,
    )
    guard = ResearchLoopGuard(policy)
    guard.before_tool_call("search", {"q": "a"})
    guard.before_tool_call("search", {"q": "b"})
    guard.before_tool_call("search", {"q": "c"})
    guard.before_tool_call("search", {"q": "d"})
    # "a" should be evicted from window
    d = guard.before_tool_call("search", {"q": "a"})
    assert d.allowed is True


# ---------- Tool frequency limits ----------


def test_tool_frequency_warn() -> None:
    policy = LoopGuardPolicy(
        tool_freq_warn=3,
        tool_freq_hard_limit=5,
        max_repeated_tool_call=100,
        warn_threshold=100,
        hard_limit=100,
    )
    guard = ResearchLoopGuard(policy)
    for i in range(2):
        guard.before_tool_call("search", {"q": f"q{i}"})
    d3 = guard.before_tool_call("search", {"q": "q3"})
    assert d3.allowed is True
    assert d3.warning
    assert "consider wrapping up" in d3.warning.lower()


def test_tool_frequency_hard_stop() -> None:
    policy = LoopGuardPolicy(
        tool_freq_warn=3,
        tool_freq_hard_limit=5,
        max_repeated_tool_call=100,
        warn_threshold=100,
        hard_limit=100,
    )
    guard = ResearchLoopGuard(policy)
    for i in range(4):
        guard.before_tool_call("search", {"q": f"unique{i}"})
    d5 = guard.before_tool_call("search", {"q": "unique5"})
    assert d5.allowed is False
    assert "exceeded" in d5.reason.lower()


# ---------- ToolCallGuardrail ----------


def test_guardrail_denied_tool() -> None:
    guardrail = ToolCallGuardrail(denied_tools=["dangerous_tool"])
    decision = guardrail.evaluate("dangerous_tool")
    assert decision.allowed is False
    assert "denied" in decision.reason.lower()
    assert decision.code == "guardrail.denied_tool"


def test_guardrail_allowed_tool() -> None:
    guardrail = ToolCallGuardrail(denied_tools=["dangerous_tool"])
    decision = guardrail.evaluate("safe_tool")
    assert decision.allowed is True


def test_guardrail_denied_domain() -> None:
    guardrail = ToolCallGuardrail(allowed_domains=["example.com", "trusted.org"])
    decision = guardrail.evaluate("fetch_url", {"url": "https://evil.com/page"})
    assert decision.allowed is False
    assert "evil.com" in decision.reason
    assert decision.code == "guardrail.denied_domain"


def test_guardrail_allowed_domain() -> None:
    guardrail = ToolCallGuardrail(allowed_domains=["example.com"])
    decision = guardrail.evaluate("fetch_url", {"url": "https://sub.example.com/page"})
    assert decision.allowed is True


def test_guardrail_no_domain_restriction() -> None:
    guardrail = ToolCallGuardrail(allowed_domains=[])
    decision = guardrail.evaluate("fetch_url", {"url": "https://anything.com"})
    assert decision.allowed is True


def test_guardrail_artifact() -> None:
    guardrail = ToolCallGuardrail(denied_tools=["bad"])
    guardrail.evaluate("good")
    guardrail.evaluate("bad")
    artifact = guardrail.to_artifact()
    assert artifact["total_evaluations"] == 2
    assert artifact["denied_count"] == 1


def test_build_guardrail_from_config() -> None:
    cfg = {
        "configurable": {
            "deepsearch_guardrail_denied_tools": "tool_a, tool_b",
            "deepsearch_guardrail_allowed_domains": "example.com",
        }
    }
    guardrail = build_tool_call_guardrail(cfg)
    assert "tool_a" in guardrail.denied_tools
    assert "tool_b" in guardrail.denied_tools
    assert "example.com" in guardrail.allowed_domains


# ---------- to_artifact schema ----------


def test_artifact_schema_version() -> None:
    policy = LoopGuardPolicy()
    guard = ResearchLoopGuard(policy)
    artifact = guard.to_artifact()
    assert artifact["schema_version"] == 2
    assert "warned_count" in artifact
    assert "window_size" in artifact
    assert "tool_freq" in artifact


def test_artifact_captures_warnings() -> None:
    policy = LoopGuardPolicy(
        warn_threshold=2,
        hard_limit=10,
        window_size=10,
        max_repeated_tool_call=100,
    )
    guard = ResearchLoopGuard(policy)
    guard.before_tool_call("search", {"q": "test"})
    guard.before_tool_call("search", {"q": "test"})
    artifact = guard.to_artifact()
    assert artifact["warned_count"] >= 1
    assert len(artifact["warnings"]) >= 1
