"""Service lifecycle: detect, auto-start, and connect.

Usage::

    from pc_assistant.service.lifecycle import get_agent_or_client

    agent_or_client = await get_agent_or_client(config)
    # Returns Agent (in-process) or ServiceClient (connected to daemon)
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from typing import Any

from pc_assistant.config import AppConfig
from pc_assistant.service.protocol import SOCKET_PATH
from pc_assistant.service.server import is_running

logger = logging.getLogger(__name__)


async def get_agent_or_client(config: AppConfig, *, no_tools: bool = False) -> Any:
    """Get an Agent-like object: prefer connecting to the service daemon.

    1. Try connecting to an existing service (TCP first if configured, then Unix).
    2. If none running, auto-start the daemon and connect.
    3. If auto-start fails, fall back to in-process Agent.

    Returns either ``ServiceClient`` or ``Agent``, both supporting
    ``async for event in obj.run(text)``.
    """
    from pc_assistant.service.client import ServiceClient

    if config.service_port > 0:
        tcp_client = ServiceClient(
            host=config.service_host,
            port=config.service_port,
            token=config.service_token,
        )
        try:
            await asyncio.wait_for(tcp_client.connect(), timeout=2.0)
            if tcp_client.is_connected:
                logger.info("Connected to service via TCP %s:%d", config.service_host, config.service_port)
                return tcp_client
        except Exception as e:
            logger.debug("TCP connect failed: %s", e)

    client = ServiceClient()

    if is_running() and SOCKET_PATH.exists():
        try:
            await asyncio.wait_for(client.connect(), timeout=2.0)
            if client.is_connected:
                logger.info("Connected to existing service via Unix socket")
                return client
        except Exception as e:
            logger.debug("Failed to connect to existing service: %s", e)

    if _start_daemon(config):
        try:
            await asyncio.wait_for(_wait_for_socket(client), timeout=8.0)
            if client.is_connected:
                logger.info("Connected to auto-started service")
                return client
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning("Auto-started service not ready: %s", e)

    logger.info("Falling back to in-process Agent")
    return _create_inprocess_agent(config, no_tools=no_tools)


def _start_daemon(config: AppConfig) -> bool:
    """Fork a daemon process. Returns True if spawn succeeded."""
    try:
        cmd = [sys.executable, "-m", "pc_assistant.service", "--daemon"]
        if config.source_config_path:
            cmd.extend(["--config", config.source_config_path])

        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        logger.info("Spawned service daemon")
        return True
    except Exception as e:
        logger.error("Failed to spawn daemon: %s", e)
        return False


async def _wait_for_socket(client: Any) -> None:
    """Poll until the socket appears and we can connect."""
    for _ in range(40):
        await asyncio.sleep(0.2)
        if SOCKET_PATH.exists():
            try:
                await client.connect()
                if client.is_connected:
                    return
            except Exception:
                pass
    raise TimeoutError("Service socket did not become available")


def _create_inprocess_agent(config: AppConfig, *, no_tools: bool = False) -> Any:
    """Create an in-process Agent (current monolithic behavior)."""
    from pc_assistant.agent import Agent

    def _confirm_stdin(tool_name: str, args: dict) -> bool:
        try:
            answer = input(f"Allow '{tool_name}' with {args}? [y/N] ")
            return answer.strip().lower() in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    return Agent(config=config, confirm_callback=_confirm_stdin, disable_tools=no_tools)
