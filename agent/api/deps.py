"""Shared dependencies for API router modules.

Provides access to main.py globals (checkpointer, settings) without circular imports.
Router modules should import from here rather than from main.py directly.

Usage in main.py (after checkpointer creation):
    from agent.api import deps
    deps.checkpointer = checkpointer
"""

from __future__ import annotations

from fastapi import HTTPException, Request

# Set by main.py after initialization
checkpointer = None


def require_thread_owner(request: Request, thread_id: str) -> None:
    """Enforce per-user thread isolation when internal auth is enabled.

    Drop-in replacement for main._require_thread_owner that can be imported
    from router modules without circular import issues.
    """
    from common.config import settings
    from common.thread_ownership import get_thread_owner

    internal_key = (getattr(settings, "internal_api_key", "") or "").strip()
    if not internal_key:
        return

    principal_id = (getattr(request.state, "principal_id", "") or "").strip()
    if not principal_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    owner_id = (get_thread_owner(thread_id) or "").strip()
    if owner_id and owner_id != principal_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    if not checkpointer:
        return

    try:
        from common.session_manager import get_session_manager

        manager = get_session_manager(checkpointer)
        session_state = manager.get_session_state(thread_id)
        if not session_state or not isinstance(session_state.state, dict):
            return
        persisted_owner = session_state.state.get("user_id")
        if (
            isinstance(persisted_owner, str)
            and persisted_owner.strip()
            and persisted_owner.strip() != principal_id
        ):
            raise HTTPException(status_code=403, detail="Forbidden")
    except HTTPException:
        raise
    except Exception:
        return
