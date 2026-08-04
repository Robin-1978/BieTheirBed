"""PC Assistant service daemon.

Hosts the Agent, Scheduler, and Channels on a Unix-domain WebSocket
so that the TUI, CLI, and future clients can connect as thin frontends.

Usage:
    pca --serve              # foreground
    pca --serve --daemon     # background fork
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import uuid
from pathlib import Path
from typing import Any

import websockets
from websockets.asyncio.server import ServerConnection

from pc_assistant.agent import Agent
from pc_assistant.config import AppConfig, load_config
from pc_assistant.harness.confirm import CONFIRM_TIMEOUT
from pc_assistant.runtime import RuntimePaths
from pc_assistant.service.protocol import (
    SOCKET_PATH,
    PID_PATH,
    WS_MAX_SIZE,
    ClientMessage,
    ServerMessage,
    deserialize_client,
    serialize,
)

logger = logging.getLogger(__name__)


class ServiceServer:
    """WebSocket server that owns the Agent and exposes it to clients."""

    def __init__(self, config: AppConfig, log_path: Path | None = None) -> None:
        self._config = config
        self._log_path = log_path
        self._agent: Agent | None = None
        self._ws_server: Any = None
        self._tcp_server: Any = None
        self._clients: dict[str, ServerConnection] = {}
        self._confirm_futures: dict[tuple[str, str], tuple[str, asyncio.Future[bool]]] = {}
        self._run_tasks: dict[str, asyncio.Task] = {}
        self._client_sessions: dict[str, str] = {}
        self._channel_manager: Any = None
        self._attachment_cleanup_task: asyncio.Task | None = None
        self._running = False

    # ── Lifecycle ─────────────────────────────────────────────

    async def start(self) -> None:
        try:
            await self._start()
        except BaseException:
            await self.stop()
            raise

    async def _start(self) -> None:
        SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)

        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()

        self._agent = Agent(config=self._config)

        scheduler = self._agent.registry.get("scheduler")
        if scheduler is not None:
            scheduler.set_agent(self._agent)
            scheduler.set_notification_callback(self._on_timer_notify)
            if scheduler.has_tasks():
                await scheduler.execute(action="start")
                logger.info("Scheduler started with %d tasks", scheduler.task_count())

        healthy = await self._agent.health_check()
        if not healthy:
            logger.warning("LLM server at %s is not healthy", self._config.llm_server_url)

        if self._config.feishu_enabled:
            try:
                from pc_assistant.channels import create_channels_from_config
                self._channel_manager = create_channels_from_config(self._config)
                if self._channel_manager.active_channels:
                    await self._channel_manager.start_all(self._agent)
                    logger.info("Channels started: %s", self._channel_manager.active_channels)
            except Exception as e:
                logger.error("Failed to start channels: %s", e)

        self._ws_server = await websockets.unix_serve(
            self._handle_client,
            str(SOCKET_PATH),
            max_size=WS_MAX_SIZE,
        )
        logger.info("Unix socket listening on %s", SOCKET_PATH)

        if self._config.service_port > 0:
            self._tcp_server = await websockets.serve(
                self._handle_client,
                self._config.service_host,
                self._config.service_port,
                max_size=WS_MAX_SIZE,
            )
            logger.info(
                "TCP listening on %s:%d",
                self._config.service_host,
                self._config.service_port,
            )

        self._attachment_cleanup_task = asyncio.create_task(self._attachment_cleanup_loop())
        self._running = True
        _write_pid(
            self._log_path
            or RuntimePaths.from_root(self._config.runtime_root).logs / "service.log"
        )
        logger.info("Service ready (pid %d)", os.getpid())

    async def stop(self) -> None:
        self._running = False

        if self._attachment_cleanup_task is not None:
            self._attachment_cleanup_task.cancel()
            try:
                await self._attachment_cleanup_task
            except asyncio.CancelledError:
                pass
            self._attachment_cleanup_task = None

        if self._channel_manager is not None:
            try:
                self._channel_manager.broadcast("🔴 PC Assistant 服务正在关闭...")
            except Exception:
                pass
            await self._channel_manager.stop_all()

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

    async def _attachment_cleanup_loop(self) -> None:
        interval = max(10, self._config.attachment_cleanup_interval_seconds)
        while True:
            await asyncio.sleep(interval)
            if self._agent is not None:
                await asyncio.to_thread(self._agent.cleanup_attachments)

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
            run_task = self._run_tasks.pop(client_id, None)
            if run_task is not None and not run_task.done():
                run_task.cancel()
            self._resolve_client_confirm_futures(client_id, approved=False)
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
            logger.info("RUN from %s session=%s", client_id, msg.session_id)
            old = self._run_tasks.pop(client_id, None)
            if old is not None and not old.done():
                old.cancel()
            task = asyncio.create_task(self._handle_run(ws, client_id, msg))
            self._run_tasks[client_id] = task
            task.add_done_callback(self._make_run_done_cb(client_id))
        elif msg.method == "upload_attachment":
            await self._handle_upload_attachment(ws, client_id, msg)
        elif msg.method == "cancel":
            self._handle_cancel(msg)
        elif msg.method == "confirm":
            self._handle_confirm(ws, client_id, msg)
        elif msg.method == "status":
            await self._handle_status(ws, msg)
        elif msg.method == "health":
            await self._handle_health(ws, msg)
        elif msg.method == "command":
            await self._handle_command(ws, client_id, msg)
        else:
            await ws.send(serialize(
                ServerMessage.error(msg.id, f"Unknown method: {msg.method}")
            ))

    def _make_run_done_cb(self, client_id: str):
        """Return a done callback that drops the tracked run task on finish."""

        def _on_done(task: asyncio.Task) -> None:
            if self._run_tasks.get(client_id) is task:
                self._run_tasks.pop(client_id, None)
            if not task.cancelled() and task.exception() is not None:
                logger.error("Run task error for %s: %s", client_id, task.exception())

        return _on_done

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
        self._client_sessions[client_id] = session_id
        input_text = msg.input_text
        if not input_text:
            await ws.send(serialize(ServerMessage.error(msg.id, "Empty input")))
            return

        try:
            attachments = msg.attachments
        except ValueError as exc:
            await ws.send(serialize(ServerMessage.error(msg.id, str(exc))))
            return

        async def ws_confirm(tool_name: str, tool_args: dict[str, Any]) -> bool:
            code = uuid.uuid4().hex[:8]
            key = (client_id, code)
            future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
            self._confirm_futures[key] = (session_id, future)
            logger.info("CONFIRM_REQUEST client=%s code=%s tool=%s", client_id, code, tool_name)
            await ws.send(serialize(ServerMessage.confirm_request(tool_name, tool_args, code)))
            try:
                result = await asyncio.wait_for(future, timeout=CONFIRM_TIMEOUT)
                logger.info("CONFIRM_RESOLVED client=%s code=%s result=%s", client_id, code, result)
                return result
            except (asyncio.TimeoutError, asyncio.CancelledError):
                logger.info("CONFIRM_TIMEOUT client=%s code=%s", client_id, code)
                return False
            finally:
                self._confirm_futures.pop(key, None)

        try:
            run_kwargs: dict[str, Any] = {"session_id": session_id, "confirm_callback": ws_confirm}
            if attachments:
                run_kwargs["attachments"] = attachments
            async for event in self._agent.run(input_text, **run_kwargs):
                frame = ServerMessage.event(msg.id, event.model_dump())
                await ws.send(serialize(frame))
        except Exception as e:
            logger.error("Run error for %s: %s", client_id, e)
            await ws.send(serialize(ServerMessage.error(msg.id, str(e))))
        finally:
            self._resolve_client_confirm_futures(client_id, approved=False)

        await ws.send(serialize(ServerMessage.result(msg.id, {"done": True})))
        logger.info("RUN done client=%s session=%s", client_id, session_id)

    async def _handle_upload_attachment(
        self,
        ws: ServerConnection,
        client_id: str,
        msg: ClientMessage,
    ) -> None:
        if self._agent is None:
            await ws.send(serialize(ServerMessage.error(msg.id, "Agent not initialized")))
            return
        session_id = msg.session_id or self._client_sessions.get(client_id) or client_id
        self._client_sessions[client_id] = session_id
        try:
            attachment = msg.upload_attachment
            ref = self._agent.store_attachment(session_id, attachment)
        except (ValueError, KeyError) as exc:
            await ws.send(serialize(ServerMessage.error(msg.id, str(exc))))
            return
        await ws.send(serialize(ServerMessage.result(msg.id, {"attachment": ref})))

    def _handle_cancel(self, msg: ClientMessage) -> None:
        if self._agent is None:
            return
        session_id = msg.session_id
        if session_id:
            self._agent.cancel(session_id)
            self._resolve_confirm_futures_for_session(session_id)
        else:
            self._agent.cancel()
            self._resolve_all_confirm_futures(approved=False)

    def _handle_confirm(self, ws: ServerConnection, client_id: str, msg: ClientMessage) -> None:
        code = msg.params.get("code", "")
        approved = msg.params.get("approved", False)
        logger.info("CONFIRM_REPLY client=%s code=%s approved=%s", client_id, code, approved)
        entry = self._confirm_futures.pop((client_id, code), None)
        if entry is not None and not entry[1].done():
            entry[1].set_result(approved)

    def _resolve_client_confirm_futures(self, client_id: str, approved: bool) -> None:
        for key, entry in list(self._confirm_futures.items()):
            if key[0] != client_id:
                continue
            self._confirm_futures.pop(key, None)
            if not entry[1].done():
                entry[1].set_result(approved)

    def _resolve_confirm_futures_for_session(
        self, session_id: str, approved: bool = False
    ) -> None:
        for key, entry in list(self._confirm_futures.items()):
            if entry[0] != session_id:
                continue
            self._confirm_futures.pop(key, None)
            if not entry[1].done():
                entry[1].set_result(approved)

    def _resolve_all_confirm_futures(self, approved: bool) -> None:
        for key, entry in list(self._confirm_futures.items()):
            self._confirm_futures.pop(key, None)
            if not entry[1].done():
                entry[1].set_result(approved)

    async def _handle_status(self, ws: ServerConnection, msg: ClientMessage) -> None:
        if self._agent is None:
            await ws.send(serialize(ServerMessage.error(msg.id, "Agent not initialized")))
            return
        status = await self._agent.get_status()
        status["sessions"] = self._agent.session_stats()
        status["connected_clients"] = len(self._clients)
        self._apply_client_session_status(ws, status)
        await ws.send(serialize(ServerMessage.result(msg.id, status)))

    def _apply_client_session_status(
        self, ws: ServerConnection, status: dict[str, Any]
    ) -> None:
        """Overlay the requesting client's run-session totals onto the status."""
        client_id = next((cid for cid, c in self._clients.items() if c is ws), None)
        if client_id is None:
            return
        session_id = self._client_sessions.get(client_id)
        if not session_id:
            return
        for s in status.get("sessions", []):
            if s.get("session_id") == session_id:
                status["total_prompt_tokens"] = s["total_prompt_tokens"]
                status["total_completion_tokens"] = s["total_completion_tokens"]
                status["total_tokens"] = s["total_prompt_tokens"] + s["total_completion_tokens"]
                status["total_iterations"] = s["total_iterations"]
                status["status"] = s.get("last_outcome", status["status"])
                break

    async def _handle_health(self, ws: ServerConnection, msg: ClientMessage) -> None:
        if self._agent is None:
            await ws.send(serialize(ServerMessage.result(msg.id, {"healthy": False})))
            return
        healthy = await self._agent.health_check()
        await ws.send(serialize(ServerMessage.result(msg.id, {"healthy": healthy})))

    async def _handle_command(
        self,
        ws: ServerConnection,
        client_id: str,
        msg: ClientMessage,
    ) -> None:
        cmd = msg.params.get("cmd", "")
        if self._agent is None:
            await ws.send(serialize(ServerMessage.error(msg.id, "Agent not initialized")))
            return

        if cmd == "/clear":
            session_id = (
                msg.session_id
                or self._client_sessions.get(client_id)
                or client_id
            )
            self._agent.drop_session(session_id)
            self._client_sessions[client_id] = session_id
            await ws.send(serialize(ServerMessage.result(
                msg.id,
                {"cleared": True, "session_id": session_id},
            )))
        elif cmd == "/compact":
            session_id = (
                msg.session_id
                or self._client_sessions.get(client_id)
                or client_id
            )
            self._agent.drop_session(session_id)
            self._client_sessions[client_id] = session_id
            await ws.send(serialize(ServerMessage.result(
                msg.id,
                {"compacted": True, "session_id": session_id},
            )))
        elif cmd == "/tools":
            tools = self._agent.registry.list_tools()
            await ws.send(serialize(ServerMessage.result(msg.id, {"tools": tools})))
        else:
            await ws.send(serialize(ServerMessage.error(msg.id, f"Unknown command: {cmd}")))

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

def _write_pid(log_path: Path) -> None:
    """Write ``<pid>\\n<log_path>`` so status/stop commands can report both."""
    PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    PID_PATH.write_text(f"{os.getpid()}\n{log_path}\n")


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

def resolve_service_log(log_dir: str | None, config_path: str | None = None) -> Path:
    """Log file path below the unified runtime ``logs/`` directory."""
    if log_dir:
        return Path(log_dir).expanduser().resolve() / "service.log"
    cfg = load_config(config_path) if config_path else load_config()
    return RuntimePaths.from_root(cfg.runtime_root).logs / "service.log"


def run_server(
    config_path: str | None = None,
    daemon: bool = False,
    log_path: Path | None = None,
) -> int:
    """Start the service server.

    Daemonization forks *before* the event loop starts so that asyncio
    primitives (e.g. ``asyncio.create_task`` in the scheduler) work in the
    child process. Returns the process exit code.
    """
    log_path = log_path or resolve_service_log(None, config_path)
    if daemon:
        daemonize(log_path)
    return asyncio.run(_serve(config_path, daemon, log_path))


async def _serve(
    config_path: str | None,
    daemon: bool,
    log_path: Path,
) -> None:
    """Async body of :func:`run_server`."""
    cfg = load_config(config_path) if config_path else load_config()

    log_path.parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler(str(log_path), mode="a"),
        ] if not daemon else [
            logging.FileHandler(str(log_path), mode="a"),
        ],
    )

    server = ServiceServer(cfg, log_path=log_path)
    await server.start()
    await server.serve_forever()


def daemonize(log_path: Path) -> None:
    """Double-fork to detach from the terminal. Log output goes to ``log_path``."""
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if os.fork() > 0:
        sys.exit(0)

    os.setsid()

    if os.fork() > 0:
        sys.exit(0)

    sys.stdin.close()
    sys.stdout = open(str(log_path), "a")  # noqa: SIM115
    sys.stderr = sys.stdout
