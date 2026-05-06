"""Tests for observation masking in middleware."""

from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.core.middleware import mask_old_observations


class TestObservationMasking:
    """Test the hybrid observation masking logic."""

    def _make_tool_msg(self, content: str, call_id: str = "tc1") -> ToolMessage:
        return ToolMessage(content=content, tool_call_id=call_id)

    def test_masking_disabled(self):
        """When observation_masking=False, messages pass through unchanged."""
        msgs = [
            HumanMessage(content="hi"),
            self._make_tool_msg("result1", "tc1"),
            self._make_tool_msg("result2", "tc2"),
        ]
        with patch("agent.core.middleware.settings") as mock_settings:
            mock_settings.observation_masking = False
            result = mask_old_observations(msgs)
        assert result == msgs

    def test_masking_keeps_recent_window(self):
        """Recent tool messages within the window stay intact."""
        msgs = [
            HumanMessage(content="q"),
            self._make_tool_msg("old result", "tc1"),
            AIMessage(content="thinking"),
            self._make_tool_msg("recent result", "tc2"),
        ]
        with patch("agent.core.middleware.settings") as mock_settings:
            mock_settings.observation_masking = True
            mock_settings.observation_masking_window = 2
            result = mask_old_observations(msgs)
        # Both tool messages are within window of 2
        assert result[1].content == "old result"
        assert result[3].content == "recent result"

    def test_masking_replaces_old_observations(self):
        """Old tool messages beyond the window get masked."""
        msgs = [
            self._make_tool_msg("very old", "tc1"),
            self._make_tool_msg("old", "tc2"),
            AIMessage(content="think"),
            self._make_tool_msg("recent", "tc3"),
        ]
        with patch("agent.core.middleware.settings") as mock_settings:
            mock_settings.observation_masking = True
            mock_settings.observation_masking_window = 1
            result = mask_old_observations(msgs)
        # Only the last tool message (tc3) stays intact
        assert "[Observation masked:" in result[0].content
        assert "[Observation masked:" in result[1].content
        assert result[3].content == "recent"

    def test_masking_preserves_non_tool_messages(self):
        """Human and AI messages are never masked."""
        msgs = [
            HumanMessage(content="question"),
            AIMessage(content="answer"),
            self._make_tool_msg("tool result", "tc1"),
        ]
        with patch("agent.core.middleware.settings") as mock_settings:
            mock_settings.observation_masking = True
            mock_settings.observation_masking_window = 1
            result = mask_old_observations(msgs)
        assert result[0].content == "question"
        assert result[1].content == "answer"
        assert result[2].content == "tool result"

    def test_masking_truncates_long_content(self):
        """Masked content snippet is truncated to 120 chars."""
        long_content = "x" * 500
        msgs = [
            self._make_tool_msg(long_content, "tc1"),
            self._make_tool_msg("recent", "tc2"),
        ]
        with patch("agent.core.middleware.settings") as mock_settings:
            mock_settings.observation_masking = True
            mock_settings.observation_masking_window = 1
            result = mask_old_observations(msgs)
        assert "[Observation masked:" in result[0].content
        assert "…" in result[0].content
        assert len(result[0].content) < 200

    def test_no_tool_messages_passthrough(self):
        """If there are no ToolMessages, pass through."""
        msgs = [HumanMessage(content="hi"), AIMessage(content="hello")]
        with patch("agent.core.middleware.settings") as mock_settings:
            mock_settings.observation_masking = True
            mock_settings.observation_masking_window = 5
            result = mask_old_observations(msgs)
        assert result == msgs

    def test_window_zero_masks_all(self):
        """Window=0 masks all tool messages."""
        msgs = [
            self._make_tool_msg("a", "tc1"),
            self._make_tool_msg("b", "tc2"),
        ]
        with patch("agent.core.middleware.settings") as mock_settings:
            mock_settings.observation_masking = True
            mock_settings.observation_masking_window = 0
            result = mask_old_observations(msgs)
        assert "[Observation masked:" in result[0].content
        assert "[Observation masked:" in result[1].content
