from __future__ import annotations

import os
import signal
from typing import Any, Optional


def get_judge_llm(model: str = "", *, temperature: float = 0.0) -> Any:
    from langchain_openai import ChatOpenAI

    from common.config import settings

    resolved_model = str(model or getattr(settings, "evaluator_model", "") or "").strip()
    if not resolved_model:
        resolved_model = str(getattr(settings, "reasoning_model", "") or "").strip()
    if not resolved_model:
        resolved_model = str(getattr(settings, "primary_model", "") or "").strip()

    timeout_text = os.getenv("DEEP_RESEARCH_BENCHMARK_JUDGE_TIMEOUT_S", "120")
    try:
        timeout_s = float(timeout_text)
    except ValueError:
        timeout_s = 120.0

    kwargs: dict[str, Any] = {"model": resolved_model, "temperature": temperature}
    if timeout_s > 0:
        kwargs["timeout"] = timeout_s
        kwargs["max_retries"] = 0
    base_url = getattr(settings, "openai_base_url", "") or ""
    api_key = getattr(settings, "openai_api_key", "") or ""
    if base_url:
        kwargs["base_url"] = base_url
    if api_key:
        kwargs["api_key"] = api_key
    return ChatOpenAI(**kwargs)


def invoke_text(llm: Any, prompt: str) -> str:
    from langchain_core.messages import HumanMessage

    timeout_text = os.getenv("DEEP_RESEARCH_BENCHMARK_JUDGE_TIMEOUT_S", "120")
    try:
        timeout_s = int(float(timeout_text))
    except ValueError:
        timeout_s = 120

    def _raise_timeout(_signum: int, _frame: Any) -> None:
        raise TimeoutError(f"judge LLM call exceeded {timeout_s}s")

    previous_handler = signal.getsignal(signal.SIGALRM)
    if timeout_s > 0:
        signal.signal(signal.SIGALRM, _raise_timeout)
        signal.alarm(timeout_s)
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        return str(getattr(response, "content", "") or "")
    finally:
        if timeout_s > 0:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous_handler)


def env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except ValueError:
        return default


def maybe_number(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def clamp_score(value: Any) -> float:
    number = maybe_number(value)
    if number is None:
        return 0.0
    return max(0.0, min(10.0, number))
