"""Unified Middleware Layer — essential cross-cutting concerns.

1. ToolErrorHandling  - Tool failures return error messages, never crash
2. LoopDetection       - Prevent infinite LLM loops
3. TokenUsage          - Track API costs + sub-agent usage attribution

All other concerns are handled explicitly in graph nodes — not hidden in middleware.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Optional

from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)


# =============================================================================
# 1. Tool Error Handling Middleware
# =============================================================================

class ToolErrorHandler:
    """Wraps tool execution to catch errors and return graceful error messages.

    Pattern from deer-flow: ToolErrorHandlingMiddleware.
    Prevents a single tool failure from crashing the entire research pipeline.
    """

    @staticmethod
    async def execute_with_error_handling(tool_call, tools_by_name: dict, config) -> ToolMessage:
        """Execute a tool call and return a ToolMessage even on error."""
        tool_name = tool_call.get("name", "unknown")
        tool_call_id = tool_call.get("id", "unknown")

        try:
            tool = tools_by_name.get(tool_name)
            if tool is None:
                return ToolMessage(
                    content=f"Error: Tool '{tool_name}' not found. Available: {list(tools_by_name.keys())}",
                    name=tool_name,
                    tool_call_id=tool_call_id,
                )

            result = await tool.ainvoke(tool_call.get("args", {}), config)
            return ToolMessage(
                content=str(result),
                name=tool_name,
                tool_call_id=tool_call_id,
            )

        except Exception as e:
            logger.warning(f"[ToolError] {tool_name} failed: {e}")
            return ToolMessage(
                content=f"Tool '{tool_name}' encountered an error: {e!s}. Please try a different approach.",
                name=tool_name,
                tool_call_id=tool_call_id,
            )


# =============================================================================
# 2. Loop Detection Middleware
# =============================================================================

class LoopDetector:
    """Detect infinite loops in LLM responses.

    Uses a dual-layer strategy from deer-flow:
    - Hash-based: Detect exact duplicate responses
    - Frequency-based: Detect repetitive patterns

    Critical for fast_llm which is more prone to getting stuck in loops.
    """

    def __init__(self, max_repetitions: int = 3):
        self.max_repetitions = max_repetitions
        self.response_hashes: list[str] = []
        self.response_prefixes: dict[str, int] = {}

    def check(self, response_content: str) -> bool:
        """Check if response indicates a loop. Returns True if loop detected."""
        if not response_content:
            return False

        # Hash-based detection
        content_hash = hashlib.md5(response_content.encode()).hexdigest()
        self.response_hashes.append(content_hash)

        if len(self.response_hashes) >= self.max_repetitions:
            recent = self.response_hashes[-self.max_repetitions:]
            if len(set(recent)) == 1:
                logger.warning("[LoopDetect] Exact duplicate responses detected")
                return True

        # Frequency-based detection (check first 100 chars)
        prefix = response_content[:100]
        self.response_prefixes[prefix] = self.response_prefixes.get(prefix, 0) + 1

        if self.response_prefixes[prefix] >= self.max_repetitions + 2:
            logger.warning("[LoopDetect] Repetitive response pattern detected")
            return True

        return False

    def get_hint(self) -> str:
        """Get a hint to inject into the conversation to break the loop."""
        return (
            "\n[System Notice: You appear to be repeating yourself. "
            "Please try a different approach or conclude your research if you're stuck.]"
        )


# =============================================================================
# 3. Token Usage Middleware
# =============================================================================

class TokenUsageTracker:
    """Track token usage across all LLM calls for cost attribution.

    Pattern from deer-flow: TokenUsageMiddleware.
    Tracks per-phase usage (input gateway, research, report) and
    sub-agent usage for cost analysis.
    """

    def __init__(self):
        self.usage_by_phase: dict[str, dict[str, int]] = {}
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.start_time = time.time()

    def record(self, phase: str, input_tokens: int, output_tokens: int) -> None:
        """Record token usage for a phase."""
        if phase not in self.usage_by_phase:
            self.usage_by_phase[phase] = {"input": 0, "output": 0}
        self.usage_by_phase[phase]["input"] += input_tokens
        self.usage_by_phase[phase]["output"] += output_tokens
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens

    def get_summary(self) -> dict[str, Any]:
        """Get a summary of token usage."""
        elapsed = time.time() - self.start_time
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "elapsed_seconds": round(elapsed, 1),
            "by_phase": dict(self.usage_by_phase),
        }

    # Per-1M-token pricing (input, output) in USD — approximate, update as needed.
    _MODEL_PRICING: dict[str, tuple[float, float]] = {
        # DashScope Qwen (Alibaba Cloud)
        "qwen3.6-flash": (0.10, 0.40),
        "qwen3.6-plus":  (0.40, 1.60),
        "qwen3.7-max":   (1.00, 4.00),
        # OpenAI
        "gpt-4.1-mini":  (0.15, 0.60),
        "gpt-4.1":       (2.00, 8.00),
        "gpt-4o":        (2.50, 10.00),
        "gpt-4o-mini":   (0.15, 0.60),
        "o3-mini":       (1.10, 4.40),
        "o1":            (15.00, 60.00),
        # DeepSeek
        "deepseek-v4-flash": (0.28, 1.10),
        "deepseek-v4-pro":   (0.55, 2.20),
    }

    @staticmethod
    def estimate_cost(model_name: str, input_tokens: int, output_tokens: int) -> float:
        """Estimate API cost based on model pricing (approximate).

        Pricing is per 1M tokens. Unknown models use a conservative default (1.0, 4.0).
        """
        input_price, output_price = TokenUsageTracker._MODEL_PRICING.get(
            model_name, (1.0, 4.0)
        )
        return (input_tokens / 1_000_000) * input_price + (output_tokens / 1_000_000) * output_price


# =============================================================================
# 4. Error Recovery Statistics — independent tracking for A/B comparison
# =============================================================================

class RecoveryTracker:
    """Track tool-call outcomes for error recovery rate measurement.

    Records every tool invocation as success / failure / recovered so the
    recovery rate can be computed independently of the main pipeline.
    Supports A/B comparison: run with and without error handling, compare.
    """

    def __init__(self):
        self.total_calls: int = 0
        self.successes: int = 0
        self.failures: int = 0
        self.recovered: int = 0
        self.unrecovered: int = 0
        self.events: list[dict[str, Any]] = []

    def record_attempt(self, tool_name: str, success: bool) -> None:
        self.total_calls += 1
        if success:
            self.successes += 1

    def record_failure(self, tool_name: str, recovered: bool, error: str = "") -> None:
        self.failures += 1
        if recovered:
            self.recovered += 1
        else:
            self.unrecovered += 1
        self.events.append({
            "tool": tool_name,
            "recovered": recovered,
            "error": error[:200],
        })

    @property
    def recovery_rate(self) -> float:
        if self.failures == 0:
            return 1.0
        return self.recovered / self.failures

    @property
    def success_rate(self) -> float:
        if self.total_calls == 0:
            return 1.0
        return self.successes / self.total_calls

    def get_summary(self) -> dict[str, Any]:
        return {
            "total_calls": self.total_calls,
            "successes": self.successes,
            "failures": self.failures,
            "recovered": self.recovered,
            "unrecovered": self.unrecovered,
            "recovery_rate": round(self.recovery_rate, 4),
            "success_rate": round(self.success_rate, 4),
        }

    def reset(self) -> None:
        self.total_calls = 0
        self.successes = 0
        self.failures = 0
        self.recovered = 0
        self.unrecovered = 0
        self.events.clear()


# =============================================================================
# A/B Comparison Runner for Error Recovery
# =============================================================================

async def run_recovery_ab_test(
    tasks: list[dict[str, Any]],
    graph,
) -> dict[str, Any]:
    """Compare error recovery with and without error-handling middleware.

    Runs each task twice:
      - Group A: with ToolErrorHandler active (default)
      - Group B: tool errors propagate as exceptions (no recovery)

    Returns comparative statistics on recovery rates.
    """
    tracker_a = RecoveryTracker()
    tracker_b = RecoveryTracker()

    for task in tasks:
        query = task.get("query") or task.get("question", "")
        task_id = task.get("id", task.get("task_id", "?"))

        # Group A: with error handling
        try:
            from agent.core.state import build_initial_state
            state_a = build_initial_state(input_text=query)
            await graph.ainvoke(state_a, {
                "configurable": {
                    "thread_id": f"ab_a_{task_id}",
                    "allow_clarification": False,
                    "max_researcher_iterations": 2,
                    "_recovery_tracker": tracker_a,
                }
            })
        except Exception:
            pass  # Group A should not crash

        # Group B: without error handling (track any unhandled exceptions)
        try:
            from agent.core.state import build_initial_state
            state_b = build_initial_state(input_text=query)
            await graph.ainvoke(state_b, {
                "configurable": {
                    "thread_id": f"ab_b_{task_id}",
                    "allow_clarification": False,
                    "max_researcher_iterations": 2,
                    "_recovery_tracker": tracker_b,
                    "_disable_error_handling": True,
                }
            })
        except Exception as e:
            tracker_b.record_failure("pipeline", recovered=False, error=str(e))

    return {
        "with_error_handling": tracker_a.get_summary(),
        "without_error_handling": tracker_b.get_summary(),
        "improvement": {
            "recovery_rate_delta": round(
                tracker_a.recovery_rate - tracker_b.recovery_rate, 4
            ),
            "success_rate_delta": round(
                tracker_a.success_rate - tracker_b.success_rate, 4
            ),
        },
    }


# =============================================================================
# Global Instances
# =============================================================================

_loop_detector: Optional[LoopDetector] = None
_token_tracker: Optional[TokenUsageTracker] = None
_recovery_tracker: Optional[RecoveryTracker] = None


def get_loop_detector() -> LoopDetector:
    global _loop_detector
    if _loop_detector is None:
        _loop_detector = LoopDetector()
    return _loop_detector


def get_token_tracker(config: Any | None = None) -> TokenUsageTracker:
    if config is not None:
        try:
            from agent.runtime.context import get_runtime_token_tracker

            tracker = get_runtime_token_tracker(config)
            if tracker is not None:
                return tracker
        except Exception:
            pass
    global _token_tracker
    if _token_tracker is None:
        _token_tracker = TokenUsageTracker()
    return _token_tracker


def get_recovery_tracker() -> RecoveryTracker:
    global _recovery_tracker
    if _recovery_tracker is None:
        _recovery_tracker = RecoveryTracker()
    return _recovery_tracker
