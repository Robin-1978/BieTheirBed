"""Connect to the Core daemon, starting it when necessary."""
from __future__ import annotations

import asyncio
import logging
import subprocess
import sys

from knoa_platform.config import AppConfig
from knoa_platform.runtime import RuntimePaths
from knoa_platform.service.core_client import ApprovalHandler, CoreClient
from knoa_platform.service.credentials import resolve_local_service_token


logger = logging.getLogger(__name__)


async def get_core_client(
    config: AppConfig,
    *,
    approval_handler: ApprovalHandler | None = None,
) -> CoreClient:
    paths = RuntimePaths.from_root(config.runtime_root)
    client = await _connect_existing(
        config,
        paths,
        approval_handler=approval_handler,
    )
    if client is not None:
        return client
    _start_core_daemon(config)
    for _ in range(40):
        await asyncio.sleep(0.2)
        client = await _connect_existing(
            config,
            paths,
            approval_handler=approval_handler,
        )
        if client is not None:
            return client
    raise ConnectionError("Core service did not become ready")


async def _connect_existing(
    config: AppConfig,
    paths: RuntimePaths,
    *,
    approval_handler: ApprovalHandler | None,
) -> CoreClient | None:
    if config.service_port <= 0:
        raise ValueError("Core WebSocket service requires a configured TCP port")
    try:
        return await asyncio.wait_for(
            CoreClient.connect(
                f"ws://{config.service_host}:{config.service_port}",
                resolve_local_service_token(paths),
                approval_handler=approval_handler,
            ),
            timeout=2.0,
        )
    except Exception as exc:
        logger.debug("Core WebSocket connection failed: %s", type(exc).__name__)
    return None


def _start_core_daemon(config: AppConfig) -> None:
    command = [
        sys.executable,
        "-m",
        "knoa_platform.service.core_daemon",
    ]
    creationflags = 0
    if sys.platform == "win32":
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        command.append("--daemon")
    if config.source_config_path:
        command.extend(["--config", config.source_config_path])
    try:
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=sys.platform != "win32",
            creationflags=creationflags,
        )
    except OSError as exc:
        raise ConnectionError("Core service could not be started") from exc
