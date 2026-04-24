"""Tests for LATS-style tree backtracking."""

import pytest
from unittest.mock import patch

from agent.workflows.research_tree import NodeStatus, ResearchTreeNode
from agent.workflows.tree_evaluator import score_branch, should_backtrack


class TestScoreBranch:
    """Test the heuristic branch scoring."""

    def test_empty_branch(self):
        """Empty branch should score 0 (plus relevance/4)."""
        node = ResearchTreeNode(topic="test")
        node.relevance_score = 0.0
        score = score_branch(node)
        assert score == 0.0

    def test_perfect_branch(self):
        """Branch with many findings, long summary, many sources scores high."""
        node = ResearchTreeNode(topic="test")
        node.findings = [{"result": {}} for _ in range(10)]
        node.summary = "x" * 500
        node.sources = [f"http://s{i}.com" for i in range(5)]
        node.relevance_score = 1.0
        score = score_branch(node)
        assert score == 1.0

    def test_partial_branch(self):
        """Branch with some findings scores between 0 and 1."""
        node = ResearchTreeNode(topic="test")
        node.findings = [{"result": {}} for _ in range(3)]
        node.summary = "x" * 200
        node.sources = ["http://s1.com"]
        node.relevance_score = 0.8
        score = score_branch(node)
        assert 0.0 < score < 1.0

    def test_score_caps_at_one(self):
        """Even with excessive data, score doesn't exceed 1.0."""
        node = ResearchTreeNode(topic="test")
        node.findings = [{"result": {}} for _ in range(100)]
        node.summary = "x" * 10000
        node.sources = [f"http://s{i}.com" for i in range(50)]
        node.relevance_score = 1.0
        score = score_branch(node)
        assert score <= 1.0


class TestShouldBacktrack:
    """Test the backtracking decision logic."""

    def test_disabled(self):
        node = ResearchTreeNode(topic="test")
        with patch("agent.workflows.tree_evaluator.settings") as mock_s:
            mock_s.tree_backtrack_enabled = False
            assert should_backtrack(node) is False

    def test_low_score_triggers_backtrack(self):
        """Branch with low score should trigger backtrack."""
        node = ResearchTreeNode(topic="test")
        node.findings = []
        node.summary = ""
        node.sources = []
        node.relevance_score = 0.3
        with patch("agent.workflows.tree_evaluator.settings") as mock_s:
            mock_s.tree_backtrack_enabled = True
            mock_s.tree_backtrack_score_threshold = 0.4
            assert should_backtrack(node) is True

    def test_high_score_no_backtrack(self):
        """Branch with high score should not trigger backtrack."""
        node = ResearchTreeNode(topic="test")
        node.findings = [{"result": {}} for _ in range(8)]
        node.summary = "x" * 400
        node.sources = ["http://s1.com", "http://s2.com", "http://s3.com"]
        node.relevance_score = 0.9
        with patch("agent.workflows.tree_evaluator.settings") as mock_s:
            mock_s.tree_backtrack_enabled = True
            mock_s.tree_backtrack_score_threshold = 0.4
            assert should_backtrack(node) is False


class TestNodeStatusRetry:
    """Test the new RETRY status."""

    def test_retry_status_exists(self):
        assert NodeStatus.RETRY == "retry"

    def test_node_retry_count(self):
        node = ResearchTreeNode(topic="test")
        assert node.retry_count == 0
        assert node.score == 0.0
        node.retry_count = 1
        node.score = 0.3
        assert node.retry_count == 1
