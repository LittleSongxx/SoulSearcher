from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel


class ShareRequest(BaseModel):
    """Request to create a share link."""

    permissions: str = "view"
    expires_hours: Optional[int] = 72


class CommentRequest(BaseModel):
    """Request to add a comment."""

    content: str
    author: str = "anonymous"
    message_id: Optional[str] = None


class SessionComment(BaseModel):
    id: str
    thread_id: str
    message_id: Optional[str] = None
    author: str
    content: str
    created_at: str
    updated_at: str


class CommentsResponse(BaseModel):
    comments: list[SessionComment]
    count: int


class SessionVersion(BaseModel):
    id: str
    thread_id: str
    version_number: int
    label: str
    created_at: str
    snapshot_size: int


class VersionsResponse(BaseModel):
    versions: list[SessionVersion]
    count: int


@dataclass(frozen=True, slots=True)
class CollaborationRouterDeps:
    checkpointer: Any
    logger: Any
    require_thread_owner: Callable[[Request, str], None]


def build_collaboration_router(deps: CollaborationRouterDeps) -> APIRouter:
    router = APIRouter(tags=["collaboration"])

    @router.post("/api/sessions/{thread_id}/share")
    async def create_share(thread_id: str, request: Request, req: ShareRequest):
        """Create a share link for a session."""
        try:
            deps.require_thread_owner(request, thread_id)
            from common.collaboration import create_share_link

            link = create_share_link(
                thread_id=thread_id,
                permissions=req.permissions,
                expires_hours=req.expires_hours,
            )
            return {
                "success": True,
                "share": link,
                "url": f"/share/{link['id']}",
            }
        except Exception as exc:
            deps.logger.error("Create share error: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/api/share/{share_id}")
    async def get_share(share_id: str):
        """Get shared session content."""
        try:
            from common.collaboration import get_share_link

            link = get_share_link(share_id)
            if not link:
                raise HTTPException(
                    status_code=404, detail="Share link not found or expired"
                )

            session_data = None
            if deps.checkpointer:
                from common.session_manager import get_session_manager

                manager = get_session_manager(deps.checkpointer)
                session_state = manager.get_session_state(link["thread_id"])
                if session_state and isinstance(session_state.state, dict):
                    state = session_state.state
                    raw_messages = state.get("messages", [])
                    messages = []
                    if isinstance(raw_messages, list) and raw_messages:
                        for message in raw_messages[-50:]:
                            try:
                                role = None
                                content = None

                                if isinstance(message, dict):
                                    role = (
                                        message.get("role")
                                        or message.get("type")
                                        or message.get("name")
                                    )
                                    content = message.get("content")
                                else:
                                    role = getattr(message, "role", None) or getattr(
                                        message, "type", None
                                    )
                                    content = getattr(message, "content", None)

                                if content is None:
                                    content = str(message)

                                role_norm = str(role or "unknown").strip().lower()
                                if role_norm in {"human", "user"}:
                                    role_norm = "user"
                                elif role_norm in {"ai", "assistant"}:
                                    role_norm = "assistant"
                                elif role_norm in {"system"}:
                                    role_norm = "system"

                                messages.append(
                                    {
                                        "role": role_norm,
                                        "content": str(content),
                                    }
                                )
                            except Exception:
                                continue

                    title = (
                        state.get("title")
                        or state.get("topic")
                        or state.get("input")
                        or link["thread_id"]
                    )
                    if not isinstance(title, str) or not title.strip():
                        title = link["thread_id"]

                    session_data = {
                        "id": link["thread_id"],
                        "title": title.strip(),
                        "messages": messages,
                    }

            return {
                "success": True,
                "share": link,
                "session": session_data,
            }
        except HTTPException:
            raise
        except Exception as exc:
            deps.logger.error("Get share error: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.delete("/api/share/{share_id}")
    async def delete_share(share_id: str):
        """Delete a share link."""
        try:
            from common.collaboration import delete_share_link

            success = delete_share_link(share_id)
            if not success:
                raise HTTPException(status_code=404, detail="Share link not found")
            return {"success": True, "id": share_id}
        except HTTPException:
            raise
        except Exception as exc:
            deps.logger.error("Delete share error: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/api/sessions/{thread_id}/comments")
    async def add_comment(thread_id: str, request: Request, req: CommentRequest):
        """Add a comment to a session."""
        try:
            deps.require_thread_owner(request, thread_id)
            from common.collaboration import add_comment

            comment = add_comment(
                thread_id=thread_id,
                content=req.content,
                author=req.author,
                message_id=req.message_id,
            )
            return {"success": True, "comment": comment}
        except Exception as exc:
            deps.logger.error("Add comment error: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/api/sessions/{thread_id}/comments", response_model=CommentsResponse)
    async def get_comments(
        thread_id: str, request: Request, message_id: Optional[str] = None
    ):
        """Get comments for a session."""
        try:
            deps.require_thread_owner(request, thread_id)
            from common.collaboration import get_comments

            comments = get_comments(thread_id, message_id)
            return {"comments": comments, "count": len(comments)}
        except Exception as exc:
            deps.logger.error("Get comments error: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/api/sessions/{thread_id}/versions", response_model=VersionsResponse)
    async def get_versions(thread_id: str, request: Request):
        """Get version history for a session."""
        try:
            deps.require_thread_owner(request, thread_id)
            from common.collaboration import list_versions

            versions = list_versions(thread_id)
            return {"versions": versions, "count": len(versions)}
        except Exception as exc:
            deps.logger.error("Get versions error: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/api/sessions/{thread_id}/versions")
    async def create_version(
        thread_id: str, request: Request, label: Optional[str] = None
    ):
        """Create a version snapshot of a session."""
        try:
            if not deps.checkpointer:
                raise HTTPException(
                    status_code=400, detail="No checkpointer configured"
                )

            deps.require_thread_owner(request, thread_id)

            from common.collaboration import save_version
            from common.session_manager import get_session_manager

            manager = get_session_manager(deps.checkpointer)
            session = manager.get_session(thread_id)
            if not session:
                raise HTTPException(status_code=404, detail="Session not found")

            state_snapshot = {
                "thread_id": session.thread_id,
                "title": session.title,
                "messages": session.messages,
                "metadata": getattr(session, "metadata", {}),
            }

            version = save_version(thread_id, state_snapshot, label)
            return {"success": True, "version": version}
        except HTTPException:
            raise
        except Exception as exc:
            deps.logger.error("Create version error: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/api/sessions/{thread_id}/restore/{version_id}")
    async def restore_version(thread_id: str, version_id: str, request: Request):
        """Restore a session from a version snapshot."""
        try:
            deps.require_thread_owner(request, thread_id)
            from common.collaboration import get_version_snapshot

            snapshot = get_version_snapshot(version_id)
            if not snapshot:
                raise HTTPException(
                    status_code=404, detail="Version snapshot not found"
                )

            return {
                "success": True,
                "snapshot": snapshot,
                "message": "Version snapshot retrieved. Client should handle restoration.",
            }
        except HTTPException:
            raise
        except Exception as exc:
            deps.logger.error("Restore version error: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return router
