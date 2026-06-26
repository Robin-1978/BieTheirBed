from __future__ import annotations

import logging
from typing import Any

from pc_assistant.channels.base import ChannelBase

logger = logging.getLogger(__name__)


class ChannelManager:
    def __init__(self) -> None:
        self._channels: dict[str, ChannelBase] = {}
        self._agent: Any = None

    def add(self, channel: ChannelBase) -> None:
        if not channel.name:
            raise ValueError("Channel must have a non-empty name")
        self._channels[channel.name] = channel
        logger.info("Channel registered: %s", channel.name)

    def remove(self, name: str) -> None:
        ch = self._channels.pop(name, None)
        if ch is not None:
            logger.info("Channel removed: %s", name)

    def get(self, name: str) -> ChannelBase | None:
        return self._channels.get(name)

    @property
    def active_channels(self) -> list[str]:
        return list(self._channels.keys())

    async def start_all(self, agent: Any) -> None:
        self._agent = agent
        for name, channel in self._channels.items():
            try:
                await channel.start(agent)
                logger.info("Channel started: %s", name)
            except Exception as e:
                logger.error("Failed to start channel %s: %s", name, e, exc_info=True)

    async def stop_all(self) -> None:
        for name, channel in self._channels.items():
            try:
                await channel.stop()
                logger.info("Channel stopped: %s", name)
            except Exception as e:
                logger.error("Failed to stop channel %s: %s", name, e, exc_info=True)
        self._channels.clear()

    def broadcast(self, text: str) -> dict[str, bool]:
        results: dict[str, bool] = {}
        for name, channel in self._channels.items():
            try:
                results[name] = channel.send_message("", text)
            except Exception as e:
                logger.error("Broadcast failed on %s: %s", name, e)
                results[name] = False
        return results


def create_channels_from_config(config: Any) -> ChannelManager:
    manager = ChannelManager()

    if getattr(config, "feishu_enabled", False):
        try:
            from pc_assistant.channels.feishu import FeishuChannel

            ch = FeishuChannel(
                app_id=config.feishu_app_id,
                app_secret=config.feishu_app_secret,
                receive_id=config.feishu_receive_id,
                receive_id_type=config.feishu_receive_id_type,
            )
            manager.add(ch)
        except ImportError:
            logger.warning(
                "lark-oapi not installed. Install with: pip install pc_assistant[feishu]"
            )
        except Exception as e:
            logger.error("Failed to create FeishuChannel: %s", e, exc_info=True)

    return manager
