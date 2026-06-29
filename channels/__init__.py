"""IM channel integration for SoulSearcher."""

from .service import ChannelService, get_channel_service, start_channel_service, stop_channel_service

__all__ = [
    "ChannelService",
    "get_channel_service",
    "start_channel_service",
    "stop_channel_service",
]
