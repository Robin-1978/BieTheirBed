"""Top-level lifecycle owner for independently mounted channel adapters."""
from __future__ import annotations

import logging
from typing import Protocol

from knoa_platform.config import AppConfig


logger = logging.getLogger(__name__)


class ChannelPort(Protocol):
    name: str

    async def start(self) -> None: ...
    async def stop(self) -> None: ...


class ChannelRuntime:
    """Start and stop configured adapters without exposing them to Core."""

    def __init__(self, channels: tuple[ChannelPort, ...] = ()) -> None:
        self._channels = channels
        self._started: list[ChannelPort] = []

    @classmethod
    def from_config(cls, config: AppConfig) -> ChannelRuntime:
        channels: list[ChannelPort] = []
        if config.feishu_enabled:
            from knoa_platform.channels import FeishuChannel

            channels.append(FeishuChannel(config))
        return cls(tuple(channels))

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(channel.name for channel in self._channels)

    async def start(self) -> None:
        if self._started:
            raise RuntimeError("ChannelRuntime is already started")
        try:
            for channel in self._channels:
                await channel.start()
                self._started.append(channel)
                logger.info("Channel mounted: %s", channel.name)
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        channels, self._started = list(reversed(self._started)), []
        for channel in channels:
            try:
                await channel.stop()
            except Exception:
                logger.exception("Channel stop failed: %s", channel.name)
