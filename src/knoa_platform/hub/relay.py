"""Bounded opaque Relay protocol V1; no Node business method awareness."""

from __future__ import annotations

import asyncio
import base64
import binascii
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from starlette.websockets import WebSocket


class RelayFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1] = 1
    session_id: str = Field(min_length=1, max_length=128)
    stream_id: int = Field(ge=0, le=2**31 - 1)
    frame_type: Literal["open", "data", "window_update", "half_close", "reset"]
    sequence: int = Field(ge=0, le=2**63 - 1)
    ciphertext_length: int = Field(ge=0, le=1024 * 1024)
    ciphertext: str = Field(default="", max_length=1_500_000)
    window_bytes: int = Field(default=0, ge=0, le=16 * 1024 * 1024)

    def validate_bounds(self) -> None:
        if self.frame_type == "data" and not self.ciphertext:
            raise ValueError("Relay data frame requires ciphertext")
        if self.frame_type != "data" and self.ciphertext:
            raise ValueError("Only Relay data frames may contain ciphertext")
        try:
            encoded = self.ciphertext.encode("ascii")
            decoded = base64.b64decode(
                encoded + b"=" * (-len(encoded) % 4),
                altchars=b"-_",
                validate=True,
            )
        except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
            raise ValueError("Relay ciphertext must be base64url") from exc
        if self.ciphertext_length != len(decoded):
            raise ValueError("Relay ciphertext length mismatch")
        if self.frame_type == "window_update" and self.window_bytes <= 0:
            raise ValueError("Relay window update must grant bytes")


@dataclass
class NodeRelayConnection:
    websocket: WebSocket
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class ClientRelayConnection:
    node_id: str
    websocket: WebSocket
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class RelayBroker:
    def __init__(self) -> None:
        self._nodes: dict[str, NodeRelayConnection] = {}
        self._clients: dict[str, ClientRelayConnection] = {}
        self._lock = asyncio.Lock()

    async def register_node(self, node_id: str, websocket: WebSocket) -> NodeRelayConnection:
        connection = NodeRelayConnection(websocket)
        async with self._lock:
            previous = self._nodes.get(node_id)
            self._nodes[node_id] = connection
        if previous is not None:
            await previous.websocket.close(code=4001, reason="replaced")
        return connection

    async def unregister_node(self, node_id: str, connection: NodeRelayConnection) -> None:
        clients: list[ClientRelayConnection] = []
        async with self._lock:
            if self._nodes.get(node_id) is connection:
                self._nodes.pop(node_id, None)
                session_ids = [
                    session_id
                    for session_id, client in self._clients.items()
                    if client.node_id == node_id
                ]
                clients = [self._clients.pop(session_id) for session_id in session_ids]
        await asyncio.gather(
            *(
                client.websocket.close(code=4404, reason="node offline")
                for client in clients
            ),
            return_exceptions=True,
        )

    async def register_client(
        self,
        session_id: str,
        node_id: str,
        websocket: WebSocket,
    ) -> None:
        async with self._lock:
            if session_id in self._clients:
                raise ValueError("Relay session already exists")
            self._clients[session_id] = ClientRelayConnection(node_id, websocket)

    async def connected_node_ids(self) -> frozenset[str]:
        """Return the live Relay registry without exposing mutable connections."""
        async with self._lock:
            return frozenset(self._nodes)

    async def unregister_client(self, session_id: str) -> None:
        async with self._lock:
            self._clients.pop(session_id, None)

    async def send_to_node(self, node_id: str, frame: RelayFrame) -> None:
        async with self._lock:
            connection = self._nodes.get(node_id)
        if connection is None:
            raise LookupError("Node is offline")
        async with connection.send_lock:
            await connection.websocket.send_json({"frame": frame.model_dump(mode="json")})

    async def send_to_client(self, node_id: str, frame: RelayFrame) -> None:
        async with self._lock:
            connection = self._clients.get(frame.session_id)
        if connection is None:
            return
        if connection.node_id != node_id:
            raise ValueError("Relay session targets another Node")
        async with connection.send_lock:
            await connection.websocket.send_json({"frame": frame.model_dump(mode="json")})


__all__ = ["RelayBroker", "RelayFrame"]
