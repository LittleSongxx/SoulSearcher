"""Unified Middleware Layer — 4 essential cross-cutting concerns.

1. ToolErrorHandling  - Tool failures return error messages, never crash
2. LoopDetection       - Prevent infinite LLM loops
3. TokenUsage          - Track API costs + sub-agent usage attribution
4. Memory              - Async per-user memory updates

All other concerns are handled explicitly in graph nodes — not hidden in middleware.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Optional

from langchain_core.callbacks import BaseCallbackHandler
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
                content=f"Tool '{tool_name}' encountered an error: {str(e)}. Please try a different approach.",
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

    @staticmethod
    def estimate_cost(model_name: str, input_tokens: int, output_tokens: int) -> float:
        """Estimate API cost based on model pricing (approximate)."""
        pricing = {
            "gpt-4.1-mini": (0.15, 0.60),      # per 1M input, per 1M output
            "gpt-4.1": (2.00, 8.00),
            "gpt-4o": (2.50, 10.00),
            "gpt-4o-mini": (0.15, 0.60),
            "o3-mini": (1.10, 4.40),
            "o1": (15.00, 60.00),
        }
        input_price, output_price = pricing.get(model_name, (1.0, 4.0))
        return (input_tokens / 1_000_000) * input_price + (output_tokens / 1_000_000) * output_price


# =============================================================================
# 4. Memory Middleware (Phase 4 integration point)
# =============================================================================

class MemoryMiddleware:
    """Asynchronous per-user memory updates.

    Pattern from deer-flow (MemoryMiddleware), simplified for the unified design.
    Runs memory extraction as a background task so it doesn't block research.

    Phase 4 integrates with structured + embedding dual-mode memory.
    """

    async def update_memory(
        self,
        user_id: str,
        query: str,
        findings: str,
        facts: list[str],
    ) -> None:
        """Update user memory after research completes (fire-and-forget)."""
        try:
            from agent.runtime.memory import get_memory_system
            memory = get_memory_system()
            await memory.record_research(
                user_id=user_id,
                query=query,
                findings=findings[:5000],  # Store summarized findings
                facts=facts,
            )
        except ImportError:
            logger.debug("[Memory] Memory system not available (Phase 4)")
        except Exception as e:
            logger.warning(f"[Memory] Background memory update failed: {e}")


# =============================================================================
# Global Instances
# =============================================================================

_loop_detector: Optional[LoopDetector] = None
_token_tracker: Optional[TokenUsageTracker] = None
_memory_middleware: Optional[MemoryMiddleware] = None


def get_loop_detector() -> LoopDetector:
    global _loop_detector
    if _loop_detector is None:
        _loop_detector = LoopDetector()
    return _loop_detector


def get_token_tracker() -> TokenUsageTracker:
    global _token_tracker
    if _token_tracker is None:
        _token_tracker = TokenUsageTracker()
    return _token_tracker


def get_memory_middleware() -> MemoryMiddleware:
    global _memory_middleware
    if _memory_middleware is None:
        _memory_middleware = MemoryMiddleware()
    return _memory_middleware


