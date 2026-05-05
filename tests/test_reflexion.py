"""Tests for the agent reflexion module."""

from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent.core.reflexion import (
    build_reflexion_message,
    extract_reflexion_focus,
    merge_reflexion_context,
    should_reflect,
)


class TestShouldReflect:
    """Test the should_reflect gating function."""

    def test_disabled(self):
        with patch("agent.core.reflexion.settings") as mock_s:
            mock_s.agent_reflexion_enabled = False
            assert should_reflect(1, 10) is False

    def test_round_zero(self):
        with patch("agent.core.reflexion.settings") as mock_s:
            mock_s.agent_reflexion_enabled = True
            mock_s.agent_reflexion_max_rounds = 2
            assert should_reflect(0, 10) is False

    def test_round_within_limit(self):
        with patch("agent.core.reflexion.settings") as mock_s:
            mock_s.agent_reflexion_enabled = True
            mock_s.agent_reflexion_max_rounds = 2
            assert should_reflect(1, 10) is True
            assert should_reflect(2, 10) is True

    def test_round_exceeds_limit(self):
        with patch("agent.core.reflexion.settings") as mock_s:
            mock_s.agent_reflexion_enabled = True
            mock_s.agent_reflexion_max_rounds = 2
            assert should_reflect(3, 10) is False

    def test_too_few_messages(self):
        with patch("agent.core.reflexion.settings") as mock_s:
            mock_s.agent_reflexion_enabled = True
            mock_s.agent_reflexion_max_rounds = 2
            assert should_reflect(1, 2) is False


class TestBuildReflexionMessage:
    """Test the reflexion message builder."""

    def test_disabled_returns_none(self):
        with patch("agent.core.reflexion.settings") as mock_s:
            mock_s.agent_reflexion_enabled = False
            result = build_reflexion_message("goal", [], MagicMock())
        assert result is None

    def test_empty_messages_returns_none(self):
        with patch("agent.core.reflexion.settings") as mock_s:
            mock_s.agent_reflexion_enabled = True
            result = build_reflexion_message("goal", [], MagicMock())
        assert result is None

    def test_generates_feedback(self):
        """When LLM returns feedback, a SystemMessage is produced."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(
            content="The search found partial results. Need to search for pricing data next."
        )
        messages = [
            HumanMessage(content="Find pricing"),
            AIMessage(content="I'll search for pricing data"),
        ]
        with patch("agent.core.reflexion.settings") as mock_s:
            mock_s.agent_reflexion_enabled = True
            result = build_reflexion_message("Find pricing", messages, mock_llm)

        assert result is not None
        assert isinstance(result, SystemMessage)
        assert "[Self-Reflection]" in result.content

    def test_goal_achieved_returns_none(self):
        """When reflection says 'Goal achieved', returns None."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(
            content="Goal achieved. The pricing data has been fully retrieved."
        )
        messages = [AIMessage(content="Here is the data")]
        with patch("agent.core.reflexion.settings") as mock_s:
            mock_s.agent_reflexion_enabled = True
            result = build_reflexion_message("Find pricing", messages, mock_llm)

        assert result is None

    def test_llm_failure_returns_none(self):
        """When LLM fails, returns None gracefully."""
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = Exception("API error")
        messages = [AIMessage(content="test")]
        with patch("agent.core.reflexion.settings") as mock_s:
            mock_s.agent_reflexion_enabled = True
            result = build_reflexion_message("goal", messages, mock_llm)

        assert result is None


class TestReflexionHelpers:
    def test_extract_focus_prefers_gap_and_next_action_lines(self):
        feedback = """ACHIEVED: gathered initial evidence.
GAPS: official source coverage is missing; need fresher updates.
NEXT_ACTION: search regulator filing and latest vendor announcement.
"""

        focus = extract_reflexion_focus(feedback)

        assert any("official source coverage" in item for item in focus)
        assert any(
            "latest vendor announcement" in item or "regulator filing" in item
            for item in focus
        )

    def test_merge_reflexion_context_deduplicates_overlapping_messages(self):
        base_messages = [
            SystemMessage(content="system"),
            HumanMessage(content="goal"),
            AIMessage(content="I'll search"),
        ]
        new_messages = [
            AIMessage(content="I'll search"),
            AIMessage(content="Search results summarized"),
        ]

        merged = merge_reflexion_context(base_messages, new_messages)

        assert [msg.content for msg in merged] == [
            "system",
            "goal",
            "I'll search",
            "Search results summarized",
        ]
