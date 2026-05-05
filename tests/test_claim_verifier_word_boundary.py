"""Regression tests for ClaimVerifier word-boundary matching in negation/trend detection."""

from agent.workflows.claim_verifier import ClaimVerifier


def test_has_negation_ignores_substrings():
    """'no' should not match inside 'now', 'know', 'another', etc."""
    v = ClaimVerifier()
    assert v._has_negation("This is not true") is True
    assert v._has_negation("No evidence found") is True
    assert v._has_negation("没有数据") is True

    assert v._has_negation("capacity now exceeds 1000 GW") is False
    assert v._has_negation("We know that growth continued") is False
    assert v._has_negation("Another report showed gains") is False
    assert v._has_negation("Innovation drives progress") is False
    assert v._has_negation("notable increase observed") is False
    assert v._has_negation("未来市场持续增长") is False


def test_trend_direction_ignores_substrings():
    """'up' should not match inside 'update', 'support', etc."""
    v = ClaimVerifier()
    assert v._trend_direction("prices rose sharply") == 1
    assert v._trend_direction("revenue increased by 20%") == 1
    assert v._trend_direction("costs fell by 10%") == -1
    assert v._trend_direction("a decrease of 5%") == -1

    assert v._trend_direction("the company updated its policy") == 0
    assert v._trend_direction("supported by new infrastructure") == 0
    assert v._trend_direction("setup completed successfully") == 0
    assert v._trend_direction("group discussion topic") == 0


def test_no_false_contradiction_from_now():
    """Regression: 'now exceeds' should not trigger negation via 'no' in 'now'."""
    v = ClaimVerifier()
    claim = "The global AI market was valued at $196.6 billion in 2023."
    evidence = "Total global wind capacity now exceeds 1,000 GW."
    assert v._is_contradiction(claim, evidence) is False
