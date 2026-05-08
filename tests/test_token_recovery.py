"""Tests for token-limit recovery helpers in agent.core.middleware.

These cover :func:`shrink_messages_for_retry`,
:func:`invoke_with_token_recovery`, and :func:`ainvoke_with_token_recovery`.
The helpers are designed to keep the original error semantics for non-token
errors and only shrink+retry when ``is_token_limit_error`` matches.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent.core.middleware import (
    ainvoke_with_token_recovery,
    invoke_with_token_recovery,
    shrink_messages_for_retry,
)


# --------------------------------------------------------------------------- #
# shrink_messages_for_retry
# --------------------------------------------------------------------------- #


def _build_messages(num_human: int = 4, num_system: int = 1) -> list:
    msgs: list = [SystemMessage(content=f"system-{i}") for i in range(num_system)]
    for i in range(num_human):
        msgs.append(HumanMessage(content=f"human-{i}"))
        msgs.append(AIMessage(content=f"ai-{i}"))
    return msgs


def test_shrink_preserves_all_system_messages():
    msgs = _build_messages(num_human=4, num_system=2)
    result = shrink_messages_for_retry(msgs, keep_ratio=0.5)

    system_in = [m for m in msgs if isinstance(m, SystemMessage)]
    system_out = [m for m in result if isinstance(m, SystemMessage)]
    assert system_out == system_in, "all SystemMessages must survive shrinking"


def test_shrink_keeps_trailing_non_system_by_ratio():
    msgs = _build_messages(num_human=5, num_system=1)  # 1 system + 10 non-system
    result = shrink_messages_for_retry(msgs, keep_ratio=0.5)

    non_system = [m for m in msgs if not isinstance(m, SystemMessage)]
    expected_tail = non_system[-5:]
    actual_non_system = [m for m in result if not isinstance(m, SystemMessage)]
    assert actual_non_system == expected_tail


def test_shrink_respects_keep_last_min_floor():
    msgs = _build_messages(num_human=5, num_system=1)
    result = shrink_messages_for_retry(msgs, keep_ratio=0.0, keep_last_min=3)

    non_system = [m for m in result if not isinstance(m, SystemMessage)]
    assert len(non_system) == 3, "keep_last_min should override a 0 ratio"


def test_shrink_returns_only_system_when_no_other_messages():
    msgs = [SystemMessage(content="only system")]
    result = shrink_messages_for_retry(msgs, keep_ratio=0.5)
    assert result == msgs


def test_shrink_handles_empty_input():
    assert shrink_messages_for_retry([], keep_ratio=0.5) == []


def test_shrink_clamps_ratio_above_one():
    msgs = _build_messages(num_human=3, num_system=1)
    result = shrink_messages_for_retry(msgs, keep_ratio=10.0)
    # Ratio clamped to 1.0 → keep everything
    assert len(result) == len(msgs)


# --------------------------------------------------------------------------- #
# Fake LLMs to drive invoke_with_token_recovery
# --------------------------------------------------------------------------- #


class _FakeResponse:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    """Records each call's message length and replays a scripted result list.

    Each scripted entry is either:
      * a ``_FakeResponse`` (returned successfully), or
      * an ``Exception`` instance (raised).
    """

    def __init__(self, scripted: list):
        self._scripted = list(scripted)
        self.calls: list[int] = []
        self.last_messages: list = []

    def invoke(self, messages, *, config=None):  # noqa: D401
        self.calls.append(len(messages))
        self.last_messages = list(messages)
        if not self._scripted:
            raise AssertionError("FakeLLM exhausted scripted responses")
        outcome = self._scripted.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def ainvoke(self, messages, *, config=None):  # noqa: D401
        return self.invoke(messages, config=config)


def _token_error(msg: str = "context length exceeded") -> RuntimeError:
    return RuntimeError(msg)


# --------------------------------------------------------------------------- #
# invoke_with_token_recovery (sync)
# --------------------------------------------------------------------------- #


def test_invoke_returns_immediately_on_success():
    llm = _FakeLLM([_FakeResponse("ok")])
    msgs = _build_messages(num_human=2)

    result = invoke_with_token_recovery(llm, msgs)

    assert result.content == "ok"
    assert llm.calls == [len(msgs)], "should not retry when the first call succeeds"


def test_invoke_propagates_non_token_errors_without_retry():
    llm = _FakeLLM([RuntimeError("auth: invalid api key")])
    msgs = _build_messages(num_human=2)

    with pytest.raises(RuntimeError, match="auth"):
        invoke_with_token_recovery(llm, msgs)

    assert len(llm.calls) == 1, "non-token errors must not trigger shrink+retry"


def test_invoke_recovers_after_token_limit_then_success():
    llm = _FakeLLM([_token_error(), _FakeResponse("recovered")])
    msgs = _build_messages(num_human=4, num_system=1)

    result = invoke_with_token_recovery(llm, msgs)

    assert result.content == "recovered"
    assert len(llm.calls) == 2, "expected one retry after token-limit"
    assert llm.calls[1] < llm.calls[0], "retry must use a shorter message list"
    # SystemMessage should still be present in the retry payload
    assert any(isinstance(m, SystemMessage) for m in llm.last_messages)


def test_invoke_reraises_after_exhausting_retries():
    # All three errors must contain a token-limit marker, otherwise the
    # helper short-circuits on the first non-token-limit failure.
    llm = _FakeLLM(
        [
            _token_error("context length exceeded — attempt 1"),
            _token_error("context length exceeded — attempt 2"),
            _token_error("context length exceeded — attempt 3"),
        ]
    )
    msgs = _build_messages(num_human=4, num_system=1)

    with pytest.raises(RuntimeError, match="attempt 3"):
        invoke_with_token_recovery(llm, msgs, max_retries=2)

    assert len(llm.calls) == 3, "1 initial + 2 retries"
    # Each retry should pass progressively shorter histories.
    assert llm.calls[0] > llm.calls[1] > llm.calls[2]


def test_invoke_stops_when_shrink_no_longer_makes_progress():
    """If shrinking yields the same length (e.g. only system messages remain),
    we must not loop forever — the loop should break and re-raise."""
    # Use real token-limit markers so the recovery path is actually entered;
    # otherwise the first error would short-circuit before we exercise the
    # "no progress" guard.
    llm = _FakeLLM(
        [
            _token_error("context length exceeded"),
            _token_error("context length exceeded"),
        ]
    )
    msgs = [SystemMessage(content="only system")]

    with pytest.raises(RuntimeError, match="context length"):
        invoke_with_token_recovery(llm, msgs)

    assert len(llm.calls) == 1, "no retries when shrink can't reduce the input"


def test_invoke_label_is_optional():
    llm = _FakeLLM([_token_error(), _FakeResponse("ok")])
    msgs = _build_messages(num_human=4, num_system=1)

    result = invoke_with_token_recovery(llm, msgs, label="writer")

    assert result.content == "ok"


def test_invoke_forwards_config_only_when_provided():
    captured: dict = {}

    class _ConfigCapturingLLM:
        def invoke(self, messages, **kwargs):
            captured["kwargs"] = kwargs
            return _FakeResponse("ok")

    msgs = _build_messages(num_human=2)
    invoke_with_token_recovery(_ConfigCapturingLLM(), msgs)
    assert captured["kwargs"] == {}, "no config keyword unless caller passes one"

    captured.clear()
    invoke_with_token_recovery(_ConfigCapturingLLM(), msgs, config={"tags": ["x"]})
    assert captured["kwargs"] == {"config": {"tags": ["x"]}}


# --------------------------------------------------------------------------- #
# ainvoke_with_token_recovery (async)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ainvoke_success_path():
    llm = _FakeLLM([_FakeResponse("ok")])
    msgs = _build_messages(num_human=2)
    result = await ainvoke_with_token_recovery(llm, msgs)
    assert result.content == "ok"


@pytest.mark.asyncio
async def test_ainvoke_recovers_after_token_limit():
    llm = _FakeLLM([_token_error(), _FakeResponse("recovered")])
    msgs = _build_messages(num_human=4, num_system=1)

    result = await ainvoke_with_token_recovery(llm, msgs)

    assert result.content == "recovered"
    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_ainvoke_propagates_non_token_errors():
    llm = _FakeLLM([RuntimeError("network down")])
    msgs = _build_messages(num_human=2)

    with pytest.raises(RuntimeError, match="network"):
        await ainvoke_with_token_recovery(llm, msgs)

    assert len(llm.calls) == 1
