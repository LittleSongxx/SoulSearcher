"""Tests for skill prompt injection into deep search writer LLM calls."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from langchain_core.messages import AIMessage, SystemMessage

from agent.workflows.deepsearch_optimized import (
    _final_report,
    _revise_report_for_claim_failures,
    _write_section_content,
)
from agent.workflows.skill_context import (
    SelectedSkillContext,
    format_skill_context,
)


SKILL_BLOCK_MARKER = "<skill id="
SKILL_PROMPT_MARKER = "[[SKILL_TEST_MARKER]]"


def _make_llm(response_text: str = "OK") -> MagicMock:
    """Build a mock LLM whose invoke records calls and returns ``response_text``."""

    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content=response_text)
    return llm


def _captured_messages(llm: MagicMock) -> list[Any]:
    assert llm.invoke.called, "llm.invoke must be called"
    args, _kwargs = llm.invoke.call_args
    assert args, "llm.invoke must receive positional messages"
    return list(args[0])


# --------------------------------------------------------------------------- #
# _final_report
# --------------------------------------------------------------------------- #


def test_final_report_without_skill_does_not_prepend_system_message():
    llm = _make_llm("draft")
    out = _final_report(
        llm,
        topic="topic-1",
        summary_notes=["fact A", "fact B"],
        config={},
        sources="[1] http://example.com",
    )
    assert out == "draft"
    msgs = _captured_messages(llm)
    assert all(not isinstance(m, SystemMessage) for m in msgs), (
        "no SystemMessage should be present when skill_system_prompt is missing"
    )


def test_final_report_injects_skill_system_prompt_at_head():
    llm = _make_llm("draft")
    skill_prompt = f"You are deep researcher. {SKILL_PROMPT_MARKER}"
    _final_report(
        llm,
        topic="topic-2",
        summary_notes=["fact A"],
        config={},
        sources="[1] http://example.com",
        skill_system_prompt=skill_prompt,
    )
    msgs = _captured_messages(llm)
    assert isinstance(msgs[0], SystemMessage), "first message must be SystemMessage"
    assert msgs[0].content == skill_prompt
    assert SKILL_PROMPT_MARKER in msgs[0].content


@pytest.mark.parametrize("blank_value", [None, "", "   ", "\n\t  "])
def test_final_report_ignores_blank_skill_system_prompt(blank_value):
    llm = _make_llm("draft")
    _final_report(
        llm,
        topic="topic-3",
        summary_notes=["fact"],
        config={},
        sources="[1] http://example.com",
        skill_system_prompt=blank_value,
    )
    msgs = _captured_messages(llm)
    assert all(not isinstance(m, SystemMessage) for m in msgs)


# --------------------------------------------------------------------------- #
# _revise_report_for_claim_failures
# --------------------------------------------------------------------------- #


def _failing_claims() -> list[dict[str, Any]]:
    return [
        {"status": "unsupported", "claim": "claim X is unsupported"},
        {"status": "contradicted", "claim": "claim Y is contradicted"},
    ]


def test_revise_report_short_circuits_when_no_failing_claims_even_with_skill():
    llm = _make_llm("revised")
    original = "Report body"
    out = _revise_report_for_claim_failures(
        llm,
        topic="topic",
        report=original,
        claims=[{"status": "verified", "claim": "ok"}],
        sources="[1] http://example.com",
        config={},
        skill_system_prompt=f"deep researcher {SKILL_PROMPT_MARKER}",
    )
    # No failing claims => function returns original report and never calls LLM.
    assert out == original
    assert not llm.invoke.called


def test_revise_report_injects_skill_system_prompt_at_head():
    llm = _make_llm("revised")
    skill_prompt = f"deep researcher persona. {SKILL_PROMPT_MARKER}"
    out = _revise_report_for_claim_failures(
        llm,
        topic="topic",
        report="Original report.",
        claims=_failing_claims(),
        sources="[1] http://example.com",
        config={},
        skill_system_prompt=skill_prompt,
    )
    assert out == "revised"
    msgs = _captured_messages(llm)
    assert isinstance(msgs[0], SystemMessage)
    assert msgs[0].content == skill_prompt


def test_revise_report_without_skill_does_not_prepend_system_message():
    llm = _make_llm("revised")
    _revise_report_for_claim_failures(
        llm,
        topic="topic",
        report="Original report.",
        claims=_failing_claims(),
        sources="[1] http://example.com",
        config={},
    )
    msgs = _captured_messages(llm)
    assert all(not isinstance(m, SystemMessage) for m in msgs)


# --------------------------------------------------------------------------- #
# _write_section_content
# --------------------------------------------------------------------------- #


def _section_payload() -> dict[str, Any]:
    return {
        "section_id": "s1",
        "title": "Background",
        "focus": "context and history",
    }


def test_write_section_content_without_skill_does_not_prepend_system_message():
    llm = _make_llm("section body")
    out = _write_section_content(
        llm,
        topic="topic",
        section=_section_payload(),
        summary_notes=["a"],
        section_results=[{"title": "t", "snippet": "s", "url": "u"}],
        section_evidence=[],
        report_sources=[],
        sources="[1] http://example.com",
        config={},
    )
    assert out == "section body"
    msgs = _captured_messages(llm)
    assert all(not isinstance(m, SystemMessage) for m in msgs)


def test_write_section_content_injects_skill_system_prompt_at_head():
    llm = _make_llm("section body")
    skill_prompt = f"deep researcher persona. {SKILL_PROMPT_MARKER}"
    _write_section_content(
        llm,
        topic="topic",
        section=_section_payload(),
        summary_notes=["a"],
        section_results=[{"title": "t", "snippet": "s", "url": "u"}],
        section_evidence=[],
        report_sources=[],
        sources="[1] http://example.com",
        config={},
        skill_system_prompt=skill_prompt,
    )
    msgs = _captured_messages(llm)
    assert isinstance(msgs[0], SystemMessage)
    assert msgs[0].content == skill_prompt


# --------------------------------------------------------------------------- #
# format_skill_context end-to-end produces a non-empty SystemMessage candidate
# --------------------------------------------------------------------------- #


def test_format_skill_context_emits_xml_blocks_for_selected_skills():
    selected = [
        SelectedSkillContext(
            skill_id="deep-researcher",
            name="Deep Researcher",
            reason="explicit skill id",
            category="research",
            mode="deep",
            tags=["research"],
            tools=["web_search"],
            system_prompt="# Role\n\nYou are a deep researcher.\n## Methodology\nDecompose, search, synthesize.",
        ),
        SelectedSkillContext(
            skill_id="empty-skill",
            name="Empty",
            reason="ignored",
            system_prompt="   ",  # whitespace-only body should be skipped
        ),
    ]
    text = format_skill_context(selected)
    assert SKILL_BLOCK_MARKER in text
    assert 'id="deep-researcher"' in text
    assert "</skill>" in text
    assert "Methodology" in text
    # Empty-body skill is omitted.
    assert 'id="empty-skill"' not in text


def test_format_skill_context_returns_empty_string_for_empty_input():
    assert format_skill_context([]) == ""
    assert format_skill_context(None) == ""  # type: ignore[arg-type]


def test_format_skill_context_truncates_to_max_chars():
    big_body = "x" * 5000
    selected = [
        SelectedSkillContext(
            skill_id=f"skill-{i}",
            name=f"Skill {i}",
            reason="bulk",
            system_prompt=big_body,
        )
        for i in range(5)
    ]
    out = format_skill_context(selected, max_chars=2000)
    assert len(out) <= 2000 + 64  # generous headroom for last block tag
    # at least the first block opens
    assert 'id="skill-0"' in out
