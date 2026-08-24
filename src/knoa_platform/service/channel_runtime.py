"""Top-level lifecycle owner for independently mounted channel adapters."""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from knoa_platform.config import AppConfig
from knoa_platform.channels.settings import ChannelSettingsStore


logger = logging.getLogger(__name__)


class ChannelPort(Protocol):
    name: str

    async def start(self) -> None: ...
    async def stop(self) -> None: ...


class ChannelRuntime:
    """Start and stop configured adapters without exposing them to Core."""

    def __init__(
        self,
        channels: tuple[ChannelPort, ...] = (),
        *,
        base_config: AppConfig | None = None,
        settings: ChannelSettingsStore | None = None,
    ) -> None:
        self._channels = channels
        self._started: list[ChannelPort] = []
        self._running = False
        self._base_config = base_config
        self._settings = settings
        self._reconfigure_lock = asyncio.Lock()

    @classmethod
    def from_config(cls, config: AppConfig) -> ChannelRuntime:
        settings = ChannelSettingsStore(config.runtime_root)
        effective = settings.apply(config)
        return cls(
            cls._build_channels(effective),
            base_config=config,
            settings=settings,
        )

    @staticmethod
    def _build_channels(config: AppConfig) -> tuple[ChannelPort, ...]:
        channels: list[ChannelPort] = []
        if config.feishu_enabled:
            from knoa_platform.channels import FeishuChannel

            channels.append(FeishuChannel(config))
        if config.dingtalk_enabled:
            from knoa_platform.channels import DingTalkChannel

            channels.append(DingTalkChannel(config))
        return tuple(channels)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(channel.name for channel in self._channels)

    async def start(self) -> None:
        if self._running:
            raise RuntimeError("ChannelRuntime is already started")
        self._running = True
        try:
            for channel in self._channels:
                await channel.start()
                self._started.append(channel)
                logger.info("Channel mounted: %s", channel.name)
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        self._running = False
        channels, self._started = list(reversed(self._started)), []
        for channel in channels:
            try:
                await channel.stop()
            except Exception:
                logger.exception("Channel stop failed: %s", channel.name)

    def dingtalk_status(self) -> dict[str, object]:
        if self._base_config is None or self._settings is None:
            raise RuntimeError("Channel settings are unavailable")
        return self._settings.status(
            self._base_config,
            running=any(channel.name == "dingtalk" for channel in self._started),
        )

    async def configure_dingtalk(self, **values: object) -> dict[str, object]:
        if self._base_config is None or self._settings is None:
            raise RuntimeError("Channel settings are unavailable")
        async with self._reconfigure_lock:
            effective = self._settings.configure_dingtalk(
                self._base_config,
                enabled=bool(values.get("enabled", False)),
                client_id=str(values.get("client_id", "")),
                client_secret=str(values.get("client_secret", "")),
                robot_code=str(values.get("robot_code", "")),
                receive_id=str(values.get("receive_id", "")),
            )
            was_running = self._running
            if was_running:
                await self.stop()
            self._channels = self._build_channels(effective)
            if was_running:
                await self.start()
            return self.dingtalk_status()
