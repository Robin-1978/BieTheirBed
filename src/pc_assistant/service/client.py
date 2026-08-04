"""Thin async client that connects to the PC Assistant service.

Mirrors the ``Agent`` interface so ``ChatApp`` and other consumers can
use ``ServiceClient`` as a drop-in replacement.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import websockets

from pc_assistant.agent import AgentEvent
from pc_assistant.service.protocol import (
    SOCKET_PATH,
    ClientMessage,
    ServerMessage,
    serialize,
)

logger = logging.getLogger(__name__)


class ServiceClient:
    """WebSocket client that speaks the service wire protocol."""

    def __init__(
        self,
        socket_path: Path | str | None = None,
        *,
        host: str = "",
        port: int = 0,
        token: str = "",
    ) -> None:
        self._socket_path = Path(socket_path) if socket_path else SOCKET_PATH
        self._host = host
        self._port = port
        self._token = token
        self._ws: Any = None
        self._msg_id = 0
        self._pending: dict[int, asyncio.Future[ServerMessage]] = {}
        self._event_queues: dict[int, asyncio.Queue[ServerMessage | None]] = {}
        self._confirm_handler: Any = None
        self._notify_handler: Any = None
        self._reader_task: asyncio.Task | None = None
        self._connected = False

    # ── Connection lifecycle ──────────────────────────────────

    async def connect(self) -> None:
        if self._host and self._port > 0:
            uri = f"ws://{self._host}:{self._port}"
            self._ws = await websockets.connect(uri)
            if self._token:
                auth_msg = ClientMessage(method="auth", id=0, params={"token": self._token})
                await self._ws.send(auth_msg.model_dump_json())
        else:
            self._ws = await websockets.unix_connect(path=str(self._socket_path))
        self._connected = True
        self._reader_task = asyncio.create_task(self._reader_loop())

    async def disconnect(self) -> None:
        self._connected = False
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reader_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    # ── Reader loop (demuxes responses/events) ────────────────

    async def _reader_loop(self) -> None:
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                try:
                    data = json.loads(raw)
                    msg = ServerMessage.model_validate(data)
                except Exception:
                    continue

                if msg.type == "event":
                    queue = self._event_queues.get(msg.run_id)
                    if queue is not None:
                        await queue.put(msg)

                elif msg.type in ("result", "error"):
                    future = self._pending.pop(msg.id, None)
                    if future is not None and not future.done():
                        future.set_result(msg)
                    queue = self._event_queues.get(msg.id)
                    if queue is not None:
                        await queue.put(None)

                elif msg.type == "confirm_request":
                    if self._confirm_handler is not None:
                        asyncio.create_task(self._confirm_handler(msg.data))

                elif msg.type == "notify":
                    if self._notify_handler is not None:
                        try:
                            self._notify_handler(
                                msg.data.get("task_id", ""),
                                msg.data.get("message", ""),
                            )
                        except Exception:
                            pass

        except websockets.ConnectionClosed:
            self._connected = False
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Reader loop error: %s", e)
            self._connected = False

    # ── Request helpers ───────────────────────────────────────

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    async def _send(self, msg: ClientMessage) -> None:
        if self._ws is None:
            raise ConnectionError("Not connected to service")
        raw = msg.model_dump_json()
        await self._ws.send(raw)

    async def _request(self, method: str, params: dict[str, Any] | None = None) -> ServerMessage:
        msg_id = self._next_id()
        future: asyncio.Future[ServerMessage] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        await self._send(ClientMessage(method=method, id=msg_id, params=params or {}))
        return await asyncio.wait_for(future, timeout=30.0)

    # ── Agent-like interface ──────────────────────────────────

    async def run(
        self,
        user_input: str,
        *,
        session_id: str = "",
        confirm_callback: Any = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Stream agent events, matching ``Agent.run()`` signature."""
        msg_id = self._next_id()
        queue: asyncio.Queue[ServerMessage | None] = asyncio.Queue()
        self._event_queues[msg_id] = queue

        await self._send(ClientMessage(
            method="run",
            id=msg_id,
            params={"input": user_input, "session_id": session_id},
        ))

        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if item.type == "event":
                    yield AgentEvent.model_validate(item.data)
        finally:
            self._event_queues.pop(msg_id, None)

    async def cancel(self, session_id: str = "") -> None:
        await self._send(ClientMessage(
            method="cancel",
            id=self._next_id(),
            params={"session_id": session_id},
        ))

    async def health_check(self) -> bool:
        try:
            resp = await self._request("health")
            return resp.data.get("healthy", False)
        except Exception:
            return False

    def get_status(self) -> dict[str, Any]:
        return {"connected": self._connected, "service": True}

    async def get_status_async(self) -> dict[str, Any]:
        resp = await self._request("status")
        return resp.data

    async def confirm(self, code: str, approved: bool) -> None:
        await self._send(ClientMessage(
            method="confirm",
            id=self._next_id(),
            params={"code": code, "approved": approved},
        ))

    async def command(self, cmd: str) -> dict[str, Any]:
        resp = await self._request("command", {"cmd": cmd})
        return resp.data

    # ── Callback registration ─────────────────────────────────

    def set_confirm_handler(self, handler: Any) -> None:
        self._confirm_handler = handler

    def set_notify_handler(self, handler: Any) -> None:
        self._notify_handler = handler

    @property
    def is_connected(self) -> bool:
        return self._connected
