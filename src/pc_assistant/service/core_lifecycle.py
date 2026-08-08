"""Connect to the Core daemon, starting it when necessary."""
from __future__ import annotations

import asyncio
import logging
import subprocess
import sys

from pc_assistant.config import AppConfig
from pc_assistant.runtime import RuntimePaths
from pc_assistant.service.core_client import ConfirmationHandler, CoreClient


logger = logging.getLogger(__name__)


async def get_core_client(
    config: AppConfig,
    *,
    confirmation_handler: ConfirmationHandler | None = None,
) -> CoreClient:
    paths = RuntimePaths.from_root(config.runtime_root)
    client = await _connect_existing(
        config,
        paths,
        confirmation_handler=confirmation_handler,
    )
    if client is not None:
        return client
    _start_core_daemon(config)
    for _ in range(40):
        await asyncio.sleep(0.2)
        client = await _connect_existing(
            config,
            paths,
            confirmation_handler=confirmation_handler,
        )
        if client is not None:
            return client
    raise ConnectionError("Core service did not become ready")


async def _connect_existing(
    config: AppConfig,
    paths: RuntimePaths,
    *,
    confirmation_handler: ConfirmationHandler | None,
) -> CoreClient | None:
    if paths.socket.exists():
        try:
            return await asyncio.wait_for(
                CoreClient.connect_unix(
                    str(paths.socket),
                    confirmation_handler=confirmation_handler,
                ),
                timeout=2.0,
            )
        except Exception as exc:
            logger.debug("Core Unix connection failed: %s", type(exc).__name__)
    if config.service_port > 0 and config.service_token:
        try:
            return await asyncio.wait_for(
                CoreClient.connect(
                    f"ws://{config.service_host}:{config.service_port}",
                    config.service_token,
                    confirmation_handler=confirmation_handler,
                ),
                timeout=2.0,
            )
        except Exception as exc:
            logger.debug("Core TCP connection failed: %s", type(exc).__name__)
    return None


def _start_core_daemon(config: AppConfig) -> None:
    command = [
        sys.executable,
        "-m",
        "pc_assistant.service.core_daemon",
        "--daemon",
    ]
    if config.source_config_path:
        command.extend(["--config", config.source_config_path])
    try:
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise ConnectionError("Core service could not be started") from exc
