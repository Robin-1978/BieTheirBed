"""Application service composition above independent Core and Channel layers."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from knoa_platform.config import AppConfig, load_config
from knoa_platform.log_rotation import compressed_rotating_file_handler
from knoa_platform.network_tls import ensure_default_ca_bundle
from knoa_platform.runtime import RuntimePaths, load_service_environment
from knoa_platform.service.channel_runtime import ChannelRuntime
from knoa_platform.service.core_daemon import (
    CoreDaemon,
    _prepare_private_file,
    daemonize,
    resolve_core_log,
)
from knoa_platform.service.shutdown import wait_for_shutdown


logger = logging.getLogger(__name__)


class ApplicationDaemon:
    """Own service processes without allowing channels to enter Core."""

    def __init__(self, config: AppConfig, *, log_path: Path) -> None:
        self._paths = RuntimePaths.from_root(config.runtime_root)
        self._core = CoreDaemon(config, log_path=log_path)
        self._channels = ChannelRuntime.from_config(config)
        self._webhooks = None
        if config.webhook_enabled:
            from knoa_platform.adapters import WebhookAdapter

            self._webhooks = WebhookAdapter(config)
        self._gateway = None
        if config.gateway_enabled:
            from knoa_platform.gateway import SecureGatewayAdapter

            self._gateway = SecureGatewayAdapter(
                config,
                channel_controller=self._channels,
            )
        self._started = False

    async def start(self) -> None:
        if self._started:
            raise RuntimeError("ApplicationDaemon is already started")
        await self._core.start()
        try:
            if self._webhooks is not None:
                await self._webhooks.start()
            if self._gateway is not None:
                await self._gateway.start()
            await self._channels.start()
        except BaseException:
            if self._gateway is not None:
                await self._gateway.stop()
            if self._webhooks is not None:
                await self._webhooks.stop()
            await self._core.stop()
            raise
        self._started = True
        logger.info(
            "Application service ready (pid %d, channels=%s, webhook=%s, gateway=%s)",
            os.getpid(),
            ",".join(self._channels.names) or "none",
            "enabled" if self._webhooks is not None else "disabled",
            "enabled" if self._gateway is not None else "disabled",
        )

    async def stop(self) -> None:
        if not self._started:
            await self._core.stop()
            return
        self._started = False
        await self._channels.stop()
        if self._gateway is not None:
            try:
                await self._gateway.stop()
            except Exception:
                logger.exception("Secure Gateway stop failed")
        if self._webhooks is not None:
            try:
                await self._webhooks.stop()
            except Exception:
                logger.exception("Webhook adapter stop failed")
        await self._core.stop()
        logger.info("Application service stopped")

    async def serve_forever(self) -> None:
        if not self._started:
            raise RuntimeError("ApplicationDaemon is not started")
        await wait_for_shutdown(self._paths.stop_request)
        await self.stop()


def run_service(
    config_path: str | None = None,
    *,
    daemon: bool = False,
    log_path: Path | None = None,
) -> int:
    ensure_default_ca_bundle()
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
    load_service_environment()
    config = load_config(config_path) if config_path else load_config()
    _prepare_private_file(log_path)
    handlers: list[logging.Handler] = [
        compressed_rotating_file_handler(log_path),
    ]
    if not daemon:
        handlers.insert(0, logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=handlers,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("mcp.server.streamable_http").setLevel(logging.WARNING)
    service = ApplicationDaemon(config, log_path=log_path)
    await service.start()
    await service.serve_forever()
