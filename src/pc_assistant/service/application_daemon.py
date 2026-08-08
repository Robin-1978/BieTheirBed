"""Application service composition above independent Core and Channel layers."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from pc_assistant.config import AppConfig, load_config
from pc_assistant.service.channel_runtime import ChannelRuntime
from pc_assistant.service.core_daemon import (
    CoreDaemon,
    _prepare_private_file,
    daemonize,
    resolve_core_log,
)


logger = logging.getLogger(__name__)


class ApplicationDaemon:
    """Own service processes without allowing channels to enter Core."""

    def __init__(self, config: AppConfig, *, log_path: Path) -> None:
        self._core = CoreDaemon(config, log_path=log_path)
        self._channels = ChannelRuntime.from_config(config)
        self._started = False

    async def start(self) -> None:
        if self._started:
            raise RuntimeError("ApplicationDaemon is already started")
        await self._core.start()
        try:
            await self._channels.start()
        except BaseException:
            await self._core.stop()
            raise
        self._started = True
        logger.info(
            "Application service ready (pid %d, channels=%s)",
            os.getpid(),
            ",".join(self._channels.names) or "none",
        )

    async def stop(self) -> None:
        if not self._started:
            await self._core.stop()
            return
        self._started = False
        await self._channels.stop()
        await self._core.stop()
        logger.info("Application service stopped")

    async def serve_forever(self) -> None:
        if not self._started:
            raise RuntimeError("ApplicationDaemon is not started")
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except NotImplementedError:
                pass
        await stop_event.wait()
        await self.stop()


def run_service(
    config_path: str | None = None,
    *,
    daemon: bool = False,
    log_path: Path | None = None,
) -> int:
    resolved_log = log_path or resolve_core_log(None, config_path)
    if daemon:
        daemonize(resolved_log)
    asyncio.run(_serve(config_path, daemon, resolved_log))
    return 0


async def _serve(
    config_path: str | None,
    daemon: bool,
    log_path: Path,
) -> None:
    config = load_config(config_path) if config_path else load_config()
    _prepare_private_file(log_path)
    handlers: list[logging.Handler] = [
        logging.FileHandler(str(log_path), mode="a"),
    ]
    if not daemon:
        handlers.insert(0, logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=handlers,
    )
    service = ApplicationDaemon(config, log_path=log_path)
    await service.start()
    await service.serve_forever()
