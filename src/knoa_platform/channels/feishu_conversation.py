"""Backward-compatible re-exports for the shared conversation mixin."""

from knoa_platform.channels.conversation_mixin import (
    ChannelConversationMixin as FeishuConversationMixin,
    _STREAM_PATCH_INTERVAL_SECONDS,
)

__all__ = ["FeishuConversationMixin", "_STREAM_PATCH_INTERVAL_SECONDS"]
