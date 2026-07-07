from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from agent.runtime.options import sanitize_research_runtime_options

StreamFactory = Callable[..., AsyncIterator[str]]
ResumeFactory = Callable[..., AsyncIterator[str]]


@dataclass(frozen=True, slots=True)
class ResearchExecutionService:
    """Single callable facade for DeepResearch execution entrypoints.

    HTTP SSE, background runs, and A2A all depend on this facade so their public
    protocols cannot accidentally fork into separate research execution paths.
    The underlying graph streaming implementation can keep moving behind this
    boundary without changing routers or protocol adapters.
    """

    stream_factory: StreamFactory
    resume_factory: ResumeFactory | None = None

    def stream(self, input_text: str, **kwargs: Any) -> AsyncIterator[str]:
        return self._call_factory(self.stream_factory, input_text, **kwargs)

    def resume(self, resume_payload: Any, **kwargs: Any) -> AsyncIterator[str]:
        if self.resume_factory is None:
            raise RuntimeError("Research resume is not configured")
        return self._call_factory(self.resume_factory, resume_payload, **kwargs)

    @staticmethod
    def _call_factory(
        factory: Callable[..., AsyncIterator[str]],
        first_arg: Any,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        try:
            signature = inspect.signature(factory)
            params = signature.parameters
            accepts_var_kwargs = any(
                param.kind == inspect.Parameter.VAR_KEYWORD
                for param in params.values()
            )
            if not accepts_var_kwargs:
                kwargs = {key: value for key, value in kwargs.items() if key in params}
        except (TypeError, ValueError):
            pass
        return factory(first_arg, **kwargs)


async def format_stream_event(event_type: str, data: Any) -> str:
    payload = {"type": event_type, "data": data}
    return f"0:{json.dumps(payload)}\n"


def safe_research_deepsearch_config(value: Any) -> dict[str, Any]:
    return sanitize_research_runtime_options(value)


def serialize_interrupts(interrupts: Any) -> list[Any]:
    if not interrupts:
        return []
    result: list[Any] = []
    for item in interrupts:
        if hasattr(item, "value"):
            result.append(item.value)
        elif isinstance(item, dict):
            result.append(item)
        else:
            result.append(str(item))
    return result


def normalize_interrupt_resume_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload

    decisions = payload.get("decisions")
    if isinstance(decisions, list):
        return payload

    if "tool_approved" in payload:
        tool_calls = payload.get("tool_calls")
        if not isinstance(tool_calls, list) or not tool_calls:
            raise ValueError(
                "Resume payload requires non-empty 'tool_calls' when 'tool_approved' is present"
            )

        approved = bool(payload.get("tool_approved"))
        if approved:
            normalized_decisions: list[dict[str, Any]] = []
            for call in tool_calls:
                if not isinstance(call, dict):
                    raise ValueError("tool_calls entries must be objects")
                name = call.get("name")
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("tool_calls[].name is required")
                args = call.get("args") or {}
                if not isinstance(args, dict):
                    args = {}
                normalized_decisions.append(
                    {
                        "type": "edit",
                        "edited_action": {"name": name, "args": args},
                    }
                )
            return {"decisions": normalized_decisions}

        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            message = "User rejected tool execution."
        return {
            "decisions": [{"type": "reject", "message": message} for _ in tool_calls],
        }

    return payload


def normalize_search_mode(search_mode: Any) -> dict[str, Any]:
    if hasattr(search_mode, "useWebSearch") or hasattr(search_mode, "useDeepSearch"):
        use_web = bool(getattr(search_mode, "useWebSearch", False))
        use_deep = bool(getattr(search_mode, "useDeepSearch", False))
        use_agent = bool(use_deep)
        use_deep_prompt = use_deep
    elif isinstance(search_mode, dict):
        use_web = bool(
            search_mode.get("useWebSearch", search_mode.get("use_web", False))
        )
        use_deep = bool(
            search_mode.get("useDeepSearch", search_mode.get("use_deep", False))
        )
        use_agent = bool(use_deep)
        use_deep_prompt = bool(
            search_mode.get(
                "useDeepPrompt", search_mode.get("use_deep_prompt", use_deep)
            )
        )

        if not (use_web or use_agent or use_deep) and isinstance(
            search_mode.get("mode"), str
        ):
            mode_lower = search_mode["mode"].strip().lower()
            if mode_lower == "deep":
                use_agent = True
                use_deep = True
                use_deep_prompt = use_deep
            elif mode_lower not in {"", "direct"}:
                raise ValueError("search_mode.mode must be direct or deep")
    elif isinstance(search_mode, str):
        lowered = search_mode.lower().strip()

        if lowered in {"direct", ""}:
            use_web = False
            use_agent = False
            use_deep = False
            use_deep_prompt = False
        elif lowered == "deep":
            use_web = True
            use_agent = True
            use_deep = True
            use_deep_prompt = True
        else:
            use_web = False
            use_agent = False
            use_deep = False
            use_deep_prompt = False
    else:
        use_web = False
        use_agent = False
        use_deep = False
        use_deep_prompt = False

    if use_deep:
        use_web = True
        use_agent = True
        use_deep_prompt = True

    mode = "deep" if use_deep else "direct"

    return {
        "use_web": use_web,
        "use_agent": use_agent,
        "use_deep": use_deep,
        "mode": mode,
        "use_deep_prompt": use_deep_prompt,
    }
