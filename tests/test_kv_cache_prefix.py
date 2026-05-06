"""Tests for KV-cache friendly prefix stability in capped_add_messages."""

from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


class TestKVCachePrefixStability:
    """Verify that capped_add_messages preserves prefix stability for KV-cache."""

    def test_system_message_preserved_as_prefix(self):
        """System messages at the start are never modified or reordered."""
        from agent.core.state import capped_add_messages

        sys_msg = SystemMessage(content="You are a helpful assistant")
        human_msg = HumanMessage(content="Hello")

        with patch("agent.core.state.settings") as mock_s:
            mock_s.trim_messages = False
            mock_s.strip_tool_messages = False
            mock_s.observation_masking = False
            result = capped_add_messages([], [sys_msg, human_msg])

        assert result[0] is sys_msg
        assert result[0].content == "You are a helpful assistant"

    def test_head_unchanged_after_trimming(self):
        """When trimming is active, head (prefix) messages remain identical."""
        from agent.core.state import capped_add_messages

        sys_msg = SystemMessage(content="SYSTEM_PREFIX")
        first_human = HumanMessage(content="FIRST_HUMAN")
        filler = [AIMessage(content=f"msg_{i}") for i in range(20)]
        recent = AIMessage(content="RECENT")

        all_msgs = [sys_msg, first_human] + filler + [recent]

        with patch("agent.core.state.settings") as mock_s:
            mock_s.trim_messages = True
            mock_s.trim_messages_keep_first = 2
            mock_s.trim_messages_keep_last = 2
            mock_s.strip_tool_messages = False
            mock_s.observation_masking = False
            mock_s.summary_messages = False
            result = capped_add_messages([], all_msgs)

        # First 2 messages (head) must be exactly the originals
        assert result[0].content == "SYSTEM_PREFIX"
        assert result[1].content == "FIRST_HUMAN"
        # Total should be keep_first + keep_last = 4
        assert len(result) == 4

    def test_masking_does_not_modify_prefix(self):
        """Observation masking never touches the prefix (head) messages."""
        from agent.core.state import capped_add_messages

        sys_msg = SystemMessage(content="PREFIX")
        tool_msg = ToolMessage(content="old tool output", tool_call_id="tc0")
        recent_tool = ToolMessage(content="recent tool", tool_call_id="tc1")

        all_msgs = [sys_msg, tool_msg, recent_tool]

        with patch("agent.core.state.settings") as mock_s, patch(
            "agent.core.middleware.settings"
        ) as mock_mw:
            for s in (mock_s, mock_mw):
                s.trim_messages = False
                s.strip_tool_messages = False
                s.observation_masking = True
                s.observation_masking_window = 1
            result = capped_add_messages([], all_msgs)

        # System prefix is untouched
        assert result[0].content == "PREFIX"
        # Recent tool message (within window) is untouched
        assert result[2].content == "recent tool"
        # Old tool message is masked
        assert "[Observation masked:" in result[1].content

    def test_summary_appended_not_replacing_prefix(self):
        """When summarization runs, it's placed after the prefix, not replacing it."""
        from agent.core.state import capped_add_messages

        sys_msg = SystemMessage(content="PREFIX")
        filler = [AIMessage(content=f"fill_{i}") for i in range(15)]
        recent = AIMessage(content="recent")

        all_msgs = [sys_msg] + filler + [recent]

        mock_summary = SystemMessage(content="[Summary of middle history]")
        with patch("agent.core.state.settings") as mock_s, patch(
            "agent.core.state.summarize_messages", return_value=mock_summary
        ):
            mock_s.trim_messages = True
            mock_s.trim_messages_keep_first = 1
            mock_s.trim_messages_keep_last = 1
            mock_s.strip_tool_messages = False
            mock_s.observation_masking = False
            mock_s.summary_messages = True
            mock_s.summary_messages_trigger = 5
            result = capped_add_messages([], all_msgs)

        # Prefix is still first
        assert result[0].content == "PREFIX"
        # Summary is after prefix
        assert result[1].content == "[Summary of middle history]"
        # Recent is last
        assert result[-1].content == "recent"
