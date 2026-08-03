"""PC Assistant service daemon.

Hosts the Agent, Scheduler, and Channels on a Unix-domain WebSocket
so that the TUI, CLI, and future clients can connect as thin frontends.

Usage:
    pc-assistant serve              # foreground
    pc-assistant serve --daemon     # background fork
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import uuid
from pathlib import Path
from typing import Any

import websockets
from websockets.asyncio.server import ServerConnection

from pc_assistant.agent import Agent, AgentEvent
from pc_assistant.config import AppConfig, load_config
from pc_assistant.service.protocol import (
    SOCKET_PATH,
    PID_PATH,
    LOG_PATH,
    ClientMessage,
    ServerMessage,
    deserialize_client,
    serialize,
)

logger = logging.getLogger(__name__)


class ServiceServer:
    """WebSocket server that owns the Agent and exposes it to clients."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._agent: Agent | None = None
        self._ws_server: Any = None
        self._tcp_server: Any = None
        self._clients: dict[str, ServerConnection] = {}
        self._confirm_futures: dict[str, asyncio.Future[bool]] = {}
        self._running = False

    # ── Lifecycle ─────────────────────────────────────────────

    async def start(self) -> None:
        SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)

        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()

        self._agent = Agent(
            config=self._config,
            confirm_callback=self._confirm_via_client,
        )

        scheduler = self._agent.registry.get("scheduler")
        if scheduler is not None:
            scheduler.set_agent(self._agent)
            scheduler.set_notification_callback(self._on_timer_notify)
            if scheduler._tasks:
                await scheduler.execute(action="start")
                logger.info("Scheduler started with %d tasks", len(scheduler._tasks))

        healthy = await self._agent.health_check()
        if not healthy:
            logger.warning("LLM server at %s is not healthy", self._config.llm_server_url)

        if self._config.feishu_enabled:
            try:
                from pc_assistant.channels import create_channels_from_config
                channel_manager = create_channels_from_config(self._config)
                if channel_manager.active_channels:
                    await channel_manager.start_all(self._agent)
                    logger.info("Channels started: %s", channel_manager.active_channels)
            except Exception as e:
                logger.error("Failed to start channels: %s", e)

        self._ws_server = await websockets.unix_serve(
            self._handle_client,
            str(SOCKET_PATH),
        )
        logger.info("Unix socket listening on %s", SOCKET_PATH)

        if self._config.service_port > 0:
            self._tcp_server = await websockets.serve(
                self._handle_client,
                self._config.service_host,
                self._config.service_port,
            )
            logger.info(
                "TCP listening on %s:%d",
                self._config.service_host,
                self._config.service_port,
            )

        self._running = True
        _write_pid()
        logger.info("Service ready (pid %d)", os.getpid())

    async def stop(self) -> None:
        self._running = False

        for client_id, ws in list(self._clients.items()):
            try:
                await ws.close()
            except Exception:
                pass
        self._clients.clear()

        if self._ws_server is not None:
            self._ws_server.close()
            await self._ws_server.wait_closed()

        if self._tcp_server is not None:
            self._tcp_server.close()
            await self._tcp_server.wait_closed()

        if self._agent is not None:
            scheduler = self._agent.registry.get("scheduler")
            if scheduler is not None:
                await scheduler.execute(action="stop")

        _cleanup_files()
        logger.info("Service stopped")

    async def serve_forever(self) -> None:
        """Block until shutdown signal."""
        stop_event = asyncio.Event()

        def _on_signal() -> None:
            logger.info("Received shutdown signal")
            stop_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _on_signal)

        await stop_event.wait()
        await self.stop()

    # ── Per-connection handler ────────────────────────────────

    async def _handle_client(self, ws: ServerConnection) -> None:
        client_id = f"ws:{uuid.uuid4().hex[:12]}"

        is_tcp = not _is_unix_connection(ws)
        if is_tcp and self._config.service_token:
            if not await self._authenticate(ws):
                logger.warning("Client %s failed auth, closing", client_id)
                await ws.close(4001, "Unauthorized")
                return

        self._clients[client_id] = ws
        logger.info("Client connected: %s (%s)", client_id, "tcp" if is_tcp else "unix")

        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                try:
                    msg = deserialize_client(raw)
                except Exception as e:
                    await ws.send(serialize(ServerMessage.error(0, f"Invalid message: {e}")))
                    continue
                await self._dispatch(ws, client_id, msg)
        except websockets.ConnectionClosed:
            pass
        except Exception as e:
            logger.error("Client %s error: %s", client_id, e)
        finally:
            self._clients.pop(client_id, None)
            logger.info("Client disconnected: %s", client_id)

    async def _authenticate(self, ws: ServerConnection) -> bool:
        """Verify bearer token on the first message for TCP connections."""
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            msg = deserialize_client(raw)
            if msg.method != "auth":
                return False
            token = msg.params.get("token", "")
            return token == self._config.service_token
        except Exception:
            return False

    async def _dispatch(
        self,
        ws: ServerConnection,
        client_id: str,
        msg: ClientMessage,
    ) -> None:
        if msg.method == "run":
            await self._handle_run(ws, client_id, msg)
        elif msg.method == "cancel":
            self._handle_cancel(msg)
        elif msg.method == "confirm":
            self._handle_confirm(msg)
        elif msg.method == "status":
            await self._handle_status(ws, msg)
        elif msg.method == "health":
            await self._handle_health(ws, msg)
        elif msg.method == "command":
            await self._handle_command(ws, msg)
        else:
            await ws.send(serialize(
                ServerMessage.error(msg.id, f"Unknown method: {msg.method}")
            ))

    # ── Method handlers ───────────────────────────────────────

    async def _handle_run(
        self,
        ws: ServerConnection,
        client_id: str,
        msg: ClientMessage,
    ) -> None:
        if self._agent is None:
            await ws.send(serialize(ServerMessage.error(msg.id, "Agent not initialized")))
            return

        session_id = msg.session_id or client_id
        input_text = msg.input_text
        if not input_text:
            await ws.send(serialize(ServerMessage.error(msg.id, "Empty input")))
            return

        try:
            async for event in self._agent.run(input_text, session_id=session_id):
                frame = ServerMessage.event(msg.id, event.model_dump())
                await ws.send(serialize(frame))
        except Exception as e:
            logger.error("Run error for %s: %s", client_id, e)
            await ws.send(serialize(ServerMessage.error(msg.id, str(e))))

        await ws.send(serialize(ServerMessage.result(msg.id, {"done": True})))

    def _handle_cancel(self, msg: ClientMessage) -> None:
        if self._agent is None:
            return
        session_id = msg.session_id
        if session_id:
            self._agent.cancel(session_id)
        else:
            self._agent.cancel()

    def _handle_confirm(self, msg: ClientMessage) -> None:
        code = msg.params.get("code", "")
        approved = msg.params.get("approved", False)
        future = self._confirm_futures.pop(code, None)
        if future is not None and not future.done():
            future.set_result(approved)

    async def _handle_status(self, ws: ServerConnection, msg: ClientMessage) -> None:
        if self._agent is None:
            await ws.send(serialize(ServerMessage.error(msg.id, "Agent not initialized")))
            return
        status = self._agent.get_status()
        status["sessions"] = self._agent.session_stats()
        status["connected_clients"] = len(self._clients)
        await ws.send(serialize(ServerMessage.result(msg.id, status)))

    async def _handle_health(self, ws: ServerConnection, msg: ClientMessage) -> None:
        if self._agent is None:
            await ws.send(serialize(ServerMessage.result(msg.id, {"healthy": False})))
            return
        healthy = await self._agent.health_check()
        await ws.send(serialize(ServerMessage.result(msg.id, {"healthy": healthy})))

    async def _handle_command(self, ws: ServerConnection, msg: ClientMessage) -> None:
        cmd = msg.params.get("cmd", "")
        if self._agent is None:
            await ws.send(serialize(ServerMessage.error(msg.id, "Agent not initialized")))
            return

        if cmd == "/clear":
            self._agent.conversation.clear()
            await ws.send(serialize(ServerMessage.result(msg.id, {"cleared": True})))
        elif cmd == "/compact":
            self._agent.conversation.clear()
            await ws.send(serialize(ServerMessage.result(msg.id, {"compacted": True})))
        elif cmd == "/tools":
            tools = self._agent.registry.list_tools()
            await ws.send(serialize(ServerMessage.result(msg.id, {"tools": tools})))
        else:
            await ws.send(serialize(ServerMessage.error(msg.id, f"Unknown command: {cmd}")))

    # ── Confirm callback (IPC round-trip) ─────────────────────

    async def _confirm_via_client(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> bool:
        if not self._clients:
            return False

        code = uuid.uuid4().hex[:8]
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._confirm_futures[code] = future

        frame = ServerMessage.confirm_request(tool_name, tool_args, code)
        msg = serialize(frame)
        for ws in self._clients.values():
            try:
                await ws.send(msg)
            except Exception:
                pass

        try:
            return await asyncio.wait_for(future, timeout=120.0)
        except asyncio.TimeoutError:
            self._confirm_futures.pop(code, None)
            return False

    # ── Timer / scheduler notifications ───────────────────────

    def _on_timer_notify(self, task_id: str, message: str) -> None:
        frame = serialize(ServerMessage.notify(task_id, message))
        for ws in self._clients.values():
            try:
                asyncio.create_task(ws.send(frame))
            except Exception:
                pass


def _is_unix_connection(ws: ServerConnection) -> bool:
    """Check if a WebSocket connection is from a Unix socket."""
    try:
        sock = ws.transport.get_extra_info("socket")
        if sock is not None:
            import socket
            return sock.family == socket.AF_UNIX
    except Exception:
        pass
    return False


# ── PID / socket helpers ──────────────────────────────────────────────

def _write_pid() -> None:
    PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    PID_PATH.write_text(str(os.getpid()))


def _cleanup_files() -> None:
    for path in (PID_PATH, SOCKET_PATH):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def is_running() -> bool:
    """Check if the service is running (PID file + process alive)."""
    if not PID_PATH.exists():
        return False
    try:
        pid = int(PID_PATH.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, OSError):
        _cleanup_files()
        return False


# ── Entry point ───────────────────────────────────────────────────────

async def run_server(config_path: str | None = None, daemon: bool = False) -> None:
    """Start the service server."""
    if daemon:
        _daemonize()

    cfg = load_config(config_path) if config_path else load_config()

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler(str(LOG_PATH), mode="a"),
        ] if not daemon else [
            logging.FileHandler(str(LOG_PATH), mode="a"),
        ],
    )

    server = ServiceServer(cfg)
    await server.start()
    await server.serve_forever()


def _daemonize() -> None:
    """Double-fork to detach from the terminal."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    if os.fork() > 0:
        sys.exit(0)

    os.setsid()

    if os.fork() > 0:
        sys.exit(0)

    sys.stdin.close()
    sys.stdout = open(str(LOG_PATH), "a")  # noqa: SIM115
    sys.stderr = sys.stdout
