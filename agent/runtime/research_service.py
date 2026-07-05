from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from agent.retrieval.policy import reject_legacy_source_routing

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


_RESEARCH_DEEPSEARCH_CONFIG_KEYS = {
    "deepsearch_strategy",
    "strategy",
    "deepsearch_mode",
    "deepsearch_max_epochs",
    "deepsearch_max_seconds",
    "deepsearch_max_tokens",
    "deepsearch_max_research_units",
    "deepsearch_max_tool_calls_per_unit",
    "deepsearch_max_skills",
    "deepsearch_reflection_loops",
    "deepsearch_supervisor_rounds",
    "deepsearch_supervisor_max_workers",
    "deepsearch_supervisor_queries_per_worker",
    "deepsearch_supervisor_parallel_workers",
    "deepsearch_supervisor_think_enabled",
    "deepsearch_supervisor_max_depth",
    "deepsearch_supervisor_depth_confidence_threshold",
    "deepsearch_max_seconds_per_worker",
    "deepsearch_claim_verifier_use_passages",
    "deepsearch_claim_verifier_min_overlap_tokens",
    "deepsearch_claim_verifier_max_evidence_per_claim",
    "deepsearch_claim_verifier_max_claims",
    "deepsearch_guardrail_denied_tools",
    "deepsearch_guardrail_allowed_domains",
    "deepsearch_guardrail_denied_domains",
    "deepsearch_summary_trigger_tokens",
    "deepsearch_summary_trigger_messages",
    "deepsearch_summary_keep_recent",
    "deep_research_strict_citations",
    "tool_policy_strict",
    "legacy_citation_mode",
    "allow_sandbox_tools",
    "supervisor_model",
    "planner_model",
    "planning_model",
    "query_model",
    "query_gen_model",
    "search_summary_model",
    "summary_model",
    "synthesis_model",
    "worker_model",
    "researcher_model",
    "research_model",
    "compression_model",
    "writer_model",
    "final_report_model",
    "writing_model",
    "verifier_model",
    "evaluator_model",
    "evaluation_model",
    "reasoning_model",
    "retrieval_policy",
    "retrieval_allowed_origins",
    "retrieval_channels",
    "retrieval_methods",
    "retrieval_profiles",
    "allowed_domains",
    "denied_domains",
    "mcp_preset_ids",
    "mcp_results",
    "mcp_auth_required",
    "mcp_requires_auth",
    "mcp_tools_to_include",
    "mcp_tool_whitelist",
    "mcp_max_tools",
    "use_reflection_loop",
    "skill_ids",
    "deepsearch_skill_ids",
}

_RESEARCH_DEEPSEARCH_CONFIG_DICT_KEYS = {
    "retrieval_policy",
    "mcp_results",
    "research_brief_review",
}

_RESEARCH_DEEPSEARCH_CONFIG_OBJECT_LIST_KEYS = {
    "source_connectors",
    "user_injected_sources",
    "mcp_results",
}


async def format_stream_event(event_type: str, data: Any) -> str:
    payload = {"type": event_type, "data": data}
    return f"0:{json.dumps(payload)}\n"


def normalize_research_deepsearch_strategy(value: Any) -> str:
    mode = str(value or "").strip().lower().replace("-", "_")
    if mode in {"supervisor", "workers", "supervisor_worker"}:
        return "supervisor_workers"
    return "supervisor_workers"


def safe_research_deepsearch_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    reject_legacy_source_routing(value.get("source_routing"))
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key or "").strip()
        if key_text not in _RESEARCH_DEEPSEARCH_CONFIG_KEYS:
            continue
        if isinstance(item, (str, int, float, bool)) or item is None:
            cleaned[key_text] = item
        elif key_text in _RESEARCH_DEEPSEARCH_CONFIG_OBJECT_LIST_KEYS and isinstance(
            item, list
        ):
            cleaned[key_text] = [
                part for part in item if isinstance(part, (dict, str, int, float, bool))
            ]
        elif isinstance(item, list):
            cleaned[key_text] = [
                str(part).strip() for part in item if str(part).strip()
            ]
        elif key_text in _RESEARCH_DEEPSEARCH_CONFIG_DICT_KEYS and isinstance(
            item, dict
        ):
            if key_text == "retrieval_policy":
                reject_legacy_source_routing(item.get("source_routing"))
            cleaned[key_text] = item
    for strategy_key in ("deepsearch_strategy", "strategy", "deepsearch_mode"):
        if strategy_key in cleaned:
            cleaned[strategy_key] = normalize_research_deepsearch_strategy(
                cleaned[strategy_key]
            )
    return cleaned


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
