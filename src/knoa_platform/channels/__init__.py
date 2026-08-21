"""External channel adapters for the Core service."""

from knoa_platform.channels.feishu import FeishuChannel
from knoa_platform.channels.dingtalk import DingTalkChannel

__all__ = ["DingTalkChannel", "FeishuChannel"]
