from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from langgraph.types import Command
from pydantic import BaseModel


class ResearchMessageResponse(BaseModel):
    id: str
    content: str
    role: str = "assistant"
    timestamp: str


@dataclass(frozen=True, slots=True)
class InterruptRouterDeps:
    settings: Any
    checkpointer: Any
    research_graph: Any
    logger: Any
    normalize_search_mode: Callable[[Any], dict[str, Any]]
    coerce_search_mode_input: Callable[[Any], Any]
    normalize_interrupt_resume_payload: Callable[[Any], Any]
    serialize_interrupts: Callable[[Any], list[Any]]
    require_thread_owner: Callable[[Request, str], None]


class GraphInterruptResumeRequest(BaseModel):
    thread_id: str
    payload: Any
    model: Optional[str] = None
    search_mode: Any = None


class InterruptResumeRequest(BaseModel):
    """Request to resume from an interrupt point."""

    action: str = "approve"
    modifications: Optional[dict[str, Any]] = None
    feedback: Optional[str] = None

def build_interrupt_router(deps: InterruptRouterDeps) -> APIRouter:
    router = APIRouter(tags=["interrupt"])

    @router.post("/api/interrupt/resume")
    async def resume_interrupt(request: Request, payload: GraphInterruptResumeRequest):
        """Resume a LangGraph execution after an interrupt."""
        if not deps.checkpointer:
            raise HTTPException(
                status_code=400, detail="Interrupts require a checkpointer"
            )

        mode_info = deps.normalize_search_mode(payload.search_mode)
        model = (payload.model or deps.settings.primary_model).strip()
        if not payload.thread_id or not str(payload.thread_id).strip():
            raise HTTPException(status_code=400, detail="thread_id is required")
        deps.require_thread_owner(request, payload.thread_id)
        existing = deps.checkpointer.get_tuple(
            {"configurable": {"thread_id": payload.thread_id}}
        )
        if not existing:
            raise HTTPException(
                status_code=404, detail="No checkpoint found for this thread_id"
            )
        config = {
            "configurable": {
                "thread_id": payload.thread_id,
                "model": model,
                "search_mode": mode_info,
                "allow_interrupts": True,
                "tool_approval": deps.settings.tool_approval or False,
                "human_review": deps.settings.human_review or False,
                "max_revisions": deps.settings.max_revisions,
            },
            "recursion_limit": 50,
        }

        try:
            resume_payload = deps.normalize_interrupt_resume_payload(payload.payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        result = await deps.research_graph.ainvoke(
            Command(resume=resume_payload), config=config
        )
        interrupts = deps.serialize_interrupts(result.get("__interrupt__"))
        if interrupts:
            return {"status": "interrupted", "interrupts": interrupts}

        final_report = result.get("final_report", "")
        return ResearchMessageResponse(
            id=f"msg_{datetime.now().timestamp()}",
            content=final_report,
            timestamp=datetime.now().isoformat(),
        )

    @router.get("/api/interrupt/{thread_id}/status")
    async def get_interrupt_status(thread_id: str, request: Request):
        """
        Get the current interrupt status for a session.

        Returns information about whether the session is paused at an interrupt point.
        """
        if not deps.checkpointer:
            raise HTTPException(status_code=400, detail="No checkpointer configured")

        try:
            deps.require_thread_owner(request, thread_id)
            config = {"configurable": {"thread_id": thread_id}}
            checkpoint_tuple = deps.checkpointer.get_tuple(config)

            if not checkpoint_tuple:
                raise HTTPException(
                    status_code=404, detail=f"Session not found: {thread_id}"
                )

            pending_writes = getattr(checkpoint_tuple, "pending_writes", []) or []
            interrupt_items: list[Any] = []
            for entry in pending_writes:
                if not isinstance(entry, (list, tuple)) or len(entry) != 3:
                    continue
                _, key, value = entry
                if key != "__interrupt__":
                    continue
                if isinstance(value, (list, tuple)):
                    interrupt_items.extend(list(value))
                elif value is not None:
                    interrupt_items.append(value)

            prompts = deps.serialize_interrupts(interrupt_items)
            is_interrupted = bool(prompts)

            checkpoint_name: Optional[str] = None
            available_actions: list[str] = []
            first = prompts[0] if prompts else None
            if isinstance(first, dict):
                cp = first.get("checkpoint")
                if isinstance(cp, str) and cp.strip():
                    checkpoint_name = cp.strip()

                if "action_requests" in first and "review_configs" in first:
                    checkpoint_name = checkpoint_name or "tool_approval"
                    allowed: set[str] = set()
                    for cfg in first.get("review_configs", []) or []:
                        if not isinstance(cfg, dict):
                            continue
                        for decision in cfg.get("allowed_decisions", []) or []:
                            if isinstance(decision, str) and decision.strip():
                                allowed.add(decision.strip())
                    if allowed:
                        available_actions = sorted(allowed)

            if is_interrupted and not available_actions:
                available_actions = ["approve", "edit"]

            return {
                "thread_id": thread_id,
                "is_interrupted": is_interrupted,
                "interrupt_node": "",
                "checkpoint_name": checkpoint_name,
                "checkpoint_info": {},
                "available_actions": available_actions if is_interrupted else [],
                "prompts": prompts,
            }

        except HTTPException:
            raise
        except Exception as exc:
            deps.logger.error(
                "Get interrupt status error: %s", exc, exc_info=True
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/api/interrupt/{thread_id}/resume")
    async def resume_from_interrupt(
        thread_id: str, request: Request, payload: InterruptResumeRequest
    ):
        """
        Compatibility path for interrupt resume.

        The canonical implementation is /api/interrupt/resume; keep this route as
        a thin adapter so there is only one resume code path.
        """
        action = str(payload.action or "approve").strip().lower()
        resume_payload: dict[str, Any] = {"action": action}
        if payload.modifications:
            resume_payload["modifications"] = payload.modifications
            if action == "modify":
                resume_payload.update(payload.modifications)
        if payload.feedback:
            resume_payload["feedback"] = payload.feedback
        return await resume_interrupt(
            request,
            GraphInterruptResumeRequest(
                thread_id=thread_id,
                payload=resume_payload,
            ),
        )

    return router
