"""Lifecycle owner for target-state Core API WebSocket endpoints."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import websockets
from websockets.asyncio.server import Server

from pc_assistant.network_tls import is_loopback_host
from pc_assistant.service.core_api import CORE_WS_MAX_SIZE
from pc_assistant.service.core_server import CoreServer


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
        tcp: TcpCoreEndpoint,
    ) -> None:
        if not 0 <= tcp.port <= 65535:
            raise ValueError("TCP port must be between 0 and 65535")
        if not is_loopback_host(tcp.host):
            raise ValueError(
                "TCP Core API must bind to loopback until TLS is configured"
            )
        self._tcp = tcp
        self._servers: list[Server] = []

    @property
    def started(self) -> bool:
        return bool(self._servers)

    @property
    def bound_tcp_port(self) -> int | None:
        if not self._servers:
            return None
        server = self._servers[0]
        sockets = server.sockets
        if not sockets:
            return None
        return int(sockets[0].getsockname()[1])

    async def start(self) -> None:
        if self._servers:
            raise RuntimeError("CoreServiceHost is already started")
        try:
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

    async def serve_forever(self) -> None:
        if not self._servers:
            raise RuntimeError("CoreServiceHost is not started")
        await asyncio.gather(*(server.serve_forever() for server in self._servers))

    async def __aenter__(self) -> CoreServiceHost:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.stop()
