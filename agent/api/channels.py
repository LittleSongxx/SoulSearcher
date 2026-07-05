from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class ChannelStatusResponse(BaseModel):
    service_running: bool
    channels: dict[str, Any] = {}


class ChannelRestartResponse(BaseModel):
    success: bool
    message: str


def build_channels_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/channels", response_model=ChannelStatusResponse)
    async def channels_status():
        """Get the status of all IM channels."""
        try:
            from channels.service import get_channel_service

            svc = get_channel_service()
            if svc is None:
                return ChannelStatusResponse(service_running=False, channels={})
            status = svc.get_status()
            return ChannelStatusResponse(**status)
        except Exception:
            return ChannelStatusResponse(service_running=False, channels={})

    @router.post("/api/channels/{name}/restart", response_model=ChannelRestartResponse)
    async def channels_restart(name: str):
        """Restart a specific IM channel."""
        try:
            from channels.service import get_channel_service

            svc = get_channel_service()
            if svc is None:
                raise HTTPException(status_code=503, detail="Channel service not running")
            success = await svc.restart_channel(name)
            if success:
                return ChannelRestartResponse(success=True, message=f"Channel {name} restarted")
            raise HTTPException(status_code=404, detail=f"Channel {name} not found")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    return router
