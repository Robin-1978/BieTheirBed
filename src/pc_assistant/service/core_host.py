"""Lifecycle owner for target-state Core API WebSocket endpoints."""
from __future__ import annotations

import asyncio
import ipaddress
import os
import stat
from dataclasses import dataclass
from pathlib import Path

import websockets
from websockets.asyncio.server import Server

from pc_assistant.service.core_api import CORE_WS_MAX_SIZE
from pc_assistant.service.core_server import CoreServer


@dataclass(frozen=True)
class UnixCoreEndpoint:
    server: CoreServer
    socket_path: Path


@dataclass(frozen=True)
class TcpCoreEndpoint:
    server: CoreServer
    host: str
    port: int


class CoreServiceHost:
    """Start and stop Core API endpoints without in-process lifecycle fallback."""

    def __init__(
        self,
        *,
        unix: UnixCoreEndpoint | None = None,
        tcp: TcpCoreEndpoint | None = None,
    ) -> None:
        if unix is None and tcp is None:
            raise ValueError("At least one Core API endpoint is required")
        if tcp is not None and not 0 <= tcp.port <= 65535:
            raise ValueError("TCP port must be between 0 and 65535")
        if tcp is not None and not _is_loopback_host(tcp.host):
            raise ValueError(
                "TCP Core API must bind to loopback until TLS is configured"
            )
        self._unix = unix
        self._tcp = tcp
        self._servers: list[Server] = []
        self._owns_unix_socket = False

    @property
    def started(self) -> bool:
        return bool(self._servers)

    @property
    def bound_tcp_port(self) -> int | None:
        if self._tcp is None or not self._servers:
            return None
        server = self._servers[-1]
        sockets = server.sockets
        if not sockets:
            return None
        return int(sockets[0].getsockname()[1])

    async def start(self) -> None:
        if self._servers:
            raise RuntimeError("CoreServiceHost is already started")
        try:
            if self._unix is not None:
                path = self._unix.socket_path.expanduser().resolve()
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                parent_stat = path.parent.stat()
                if parent_stat.st_uid != os.geteuid():
                    raise RuntimeError(
                        f"Unix socket directory must be owned by the service user: {path.parent}"
                    )
                if stat.S_IMODE(parent_stat.st_mode) & 0o077:
                    raise RuntimeError(
                        f"Unix socket directory must be owner-only: {path.parent}"
                    )
                if os.path.lexists(path):
                    raise RuntimeError(f"Refusing to replace existing path: {path}")
                server = await websockets.unix_serve(
                    self._unix.server.handle,
                    path=str(path),
                    max_size=CORE_WS_MAX_SIZE,
                )
                self._servers.append(server)
                self._owns_unix_socket = True
                path.chmod(0o600)
            if self._tcp is not None:
                server = await websockets.serve(
                    self._tcp.server.handle,
                    self._tcp.host,
                    self._tcp.port,
                    max_size=CORE_WS_MAX_SIZE,
                )
                self._servers.append(server)
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        servers, self._servers = self._servers, []
        for server in servers:
            server.close()
        if servers:
            await asyncio.gather(
                *(server.wait_closed() for server in servers),
                return_exceptions=True,
            )
        if self._unix is not None and self._owns_unix_socket:
            path = self._unix.socket_path.expanduser().resolve()
            if os.path.lexists(path) and stat.S_ISSOCK(path.lstat().st_mode):
                path.unlink()
        self._owns_unix_socket = False

    async def serve_forever(self) -> None:
        if not self._servers:
            raise RuntimeError("CoreServiceHost is not started")
        await asyncio.gather(*(server.serve_forever() for server in self._servers))

    async def __aenter__(self) -> CoreServiceHost:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.stop()


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False
