"""Backward-compatible re-exports for the shared task notification mixin."""

from knoa_platform.channels.task_mixin import (
    ChannelTaskMixin as FeishuTaskMixin,
    _compact_background_result,
)

__all__ = ["FeishuTaskMixin", "_compact_background_result"]
