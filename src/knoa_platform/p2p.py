"""WebRTC data-channel HTTP tunnel used after authenticated Relay signaling.

The Hub only carries the SDP offer/answer through the existing authenticated
path. Once ICE succeeds, request data moves directly between peers. Every
request still passes through the normal Gateway or resource authorization
surface; P2P changes transport, not authority.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

import httpx

try:
    from aiortc import (
        RTCConfiguration,
        RTCIceServer,
        RTCPeerConnection,
        RTCSessionDescription,
    )
except ImportError as exc:  # P2P is an acceleration path; Relay remains available.
    RTCConfiguration = None  # type: ignore[assignment,misc]
    RTCIceServer = None  # type: ignore[assignment,misc]
    RTCPeerConnection = None  # type: ignore[assignment,misc]
    RTCSessionDescription = None  # type: ignore[assignment,misc]
    _AIORTC_IMPORT_ERROR: ImportError | None = exc
else:
    _AIORTC_IMPORT_ERROR = None

from knoa_platform.relay_protocol import decode_base64url, encode_base64url

_CHUNK_BYTES = 48 * 1024
_MAX_BODY_BYTES = 64 * 1024 * 1024
_BUFFER_HIGH_WATER = 1024 * 1024
_STUN_SERVERS = (
    []
    if RTCIceServer is None
    else [RTCIceServer(urls="stun:stun.cloudflare.com:3478")]
)
_FORWARDED_REQUEST_HEADERS = {
    "accept",
    "authorization",
    "content-type",
    "if-none-match",
    "last-event-id",
    "range",
}
_FORWARDED_RESPONSE_HEADERS = {
    "accept-ranges",
    "cache-control",
    "content-disposition",
    "content-length",
    "content-range",
    "content-type",
    "etag",
    "x-content-type-options",
    "x-knoa-sha256",
}


@dataclass
class _InboundRequest:
    method: str
    path: str
    headers: dict[str, str]
    expected_length: int
    body: bytearray = field(default_factory=bytearray)


@dataclass(frozen=True)
class P2PResponse:
    status: int
    headers: dict[str, str]
    body: bytes


class P2PUnavailableError(ConnectionError):
    """Raised when the optional WebRTC runtime is unavailable on this host."""


def p2p_available() -> bool:
    return _AIORTC_IMPORT_ERROR is None


def _require_p2p() -> None:
    if not p2p_available():
        raise P2PUnavailableError(
            "WebRTC P2P runtime is unavailable; use Relay fallback"
        ) from _AIORTC_IMPORT_ERROR


class P2PServer:
    """Answer WebRTC offers and dispatch data-channel requests to one ASGI app."""

    def __init__(self, app: Any) -> None:
        self._app = app
        self._peers: set[RTCPeerConnection] = set()

    async def create_answer(
        self,
        *,
        sdp: str,
        kind: Literal["app", "resource"],
    ) -> dict[str, str]:
        _require_p2p()
        peer = RTCPeerConnection(RTCConfiguration(iceServers=_STUN_SERVERS))
        self._peers.add(peer)

        @peer.on("connectionstatechange")
        async def connection_state_change() -> None:
            if peer.connectionState in {"failed", "closed", "disconnected"}:
                self._peers.discard(peer)
                await peer.close()

        @peer.on("datachannel")
        def data_channel(channel: Any) -> None:
            requests: dict[str, _InboundRequest] = {}
            receive_lock = asyncio.Lock()

            @channel.on("message")
            def message(raw: Any) -> None:
                asyncio.create_task(
                    self._receive_serialized(
                        receive_lock, channel, requests, raw, kind
                    ),
                    name="knoa-p2p-message",
                )

        try:
            await peer.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))
            answer = await peer.createAnswer()
            await peer.setLocalDescription(answer)
            await _wait_for_ice_gathering(peer)
            local = peer.localDescription
            if local is None:
                raise ConnectionError("WebRTC answer was not created")
            return {"type": local.type, "sdp": local.sdp}
        except Exception:
            self._peers.discard(peer)
            await peer.close()
            raise

    async def close(self) -> None:
        peers = tuple(self._peers)
        self._peers.clear()
        await asyncio.gather(*(peer.close() for peer in peers), return_exceptions=True)

    async def _receive(
        self,
        channel: Any,
        requests: dict[str, _InboundRequest],
        raw: Any,
        kind: Literal["app", "resource"],
    ) -> None:
        message: dict[str, Any] = {}
        try:
            message = json.loads(raw.decode() if isinstance(raw, bytes) else str(raw))
            request_id = str(message.get("request_id", ""))
            message_type = str(message.get("type", ""))
            if not request_id or len(request_id) > 128:
                raise ValueError("P2P request identity is invalid")
            if message_type == "request_start":
                method = str(message.get("method", "")).upper()
                path = str(message.get("path", ""))
                expected_length = int(message.get("body_length", -1))
                raw_headers = message.get("headers", {})
                resource_path = path.startswith("/v1/resource-invocations/")
                if (
                    request_id in requests
                    or method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}
                    or not path.startswith("/")
                    or path.startswith("//")
                    or "://" in path
                    or len(path) > 4096
                    or expected_length < 0
                    or expected_length > _MAX_BODY_BYTES
                    or not isinstance(raw_headers, dict)
                    or (kind == "resource" and not resource_path)
                ):
                    raise ValueError("P2P request start rejected")
                headers = {
                    str(key).lower(): str(value)
                    for key, value in raw_headers.items()
                    if str(key).lower() in _FORWARDED_REQUEST_HEADERS
                    and len(str(value)) <= 8192
                }
                requests[request_id] = _InboundRequest(
                    method=method,
                    path=path,
                    headers=headers,
                    expected_length=expected_length,
                )
                return
            request = requests.get(request_id)
            if request is None:
                raise ValueError("P2P request is absent")
            if message_type == "request_body":
                request.body.extend(decode_base64url(str(message.get("data", ""))))
                if (
                    len(request.body) > request.expected_length
                    or len(request.body) > _MAX_BODY_BYTES
                ):
                    raise ValueError("P2P request body is too large")
                return
            if message_type == "request_end":
                requests.pop(request_id, None)
                if len(request.body) != request.expected_length:
                    raise ValueError("P2P request body is incomplete")
                asyncio.create_task(
                    self._dispatch(channel, request_id, request),
                    name=f"knoa-p2p-request-{request_id}",
                )
                return
            if message_type == "reset":
                requests.pop(request_id, None)
                return
            raise ValueError("P2P request message is invalid")
        except Exception:
            try:
                await _send_json(
                    channel,
                    {"type": "reset", "request_id": str(message.get("request_id", ""))},
                )
            except Exception:
                pass

    async def _receive_serialized(
        self,
        lock: asyncio.Lock,
        channel: Any,
        requests: dict[str, _InboundRequest],
        raw: Any,
        kind: Literal["app", "resource"],
    ) -> None:
        async with lock:
            await self._receive(channel, requests, raw, kind)

    async def _dispatch(
        self,
        channel: Any,
        request_id: str,
        request: _InboundRequest,
    ) -> None:
        try:
            transport = httpx.ASGITransport(app=self._app, client=("p2p", 0))
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://knoa-node.local",
                timeout=120.0,
            ) as client:
                response = await client.request(
                    request.method,
                    request.path,
                    headers=request.headers,
                    content=bytes(request.body),
                )
            await _send_json(
                channel,
                {
                    "type": "response_start",
                    "request_id": request_id,
                    "status": response.status_code,
                    "headers": {
                        key.lower(): value
                        for key, value in response.headers.items()
                        if key.lower() in _FORWARDED_RESPONSE_HEADERS
                    },
                    "body_length": len(response.content),
                },
            )
            for offset in range(0, len(response.content), _CHUNK_BYTES):
                await _send_json(
                    channel,
                    {
                        "type": "response_body",
                        "request_id": request_id,
                        "data": encode_base64url(
                            response.content[offset : offset + _CHUNK_BYTES]
                        ),
                    },
                )
            await _send_json(
                channel,
                {"type": "response_end", "request_id": request_id},
            )
        except Exception:
            try:
                await _send_json(
                    channel,
                    {"type": "reset", "request_id": request_id},
                )
            except Exception:
                pass


class P2PClient:
    """Initiate one reusable WebRTC data channel and expose bounded HTTP RPC."""

    def __init__(self) -> None:
        self._peer: RTCPeerConnection | None = None
        self._channel: Any | None = None
        self._pending: dict[str, dict[str, Any]] = {}
        self._open = asyncio.Event()

    @property
    def connected(self) -> bool:
        return bool(
            self._peer
            and self._peer.connectionState == "connected"
            and self._channel
            and self._channel.readyState == "open"
        )

    async def connect(
        self,
        exchange: Callable[[dict[str, str]], Awaitable[dict[str, str]]],
        *,
        timeout: float = 12.0,
    ) -> None:
        _require_p2p()
        if self.connected:
            return
        await self.close()
        peer = RTCPeerConnection(RTCConfiguration(iceServers=_STUN_SERVERS))
        channel = peer.createDataChannel("knoa-http-v1", ordered=True)
        self._peer = peer
        self._channel = channel
        self._open = asyncio.Event()

        @channel.on("open")
        def opened() -> None:
            self._open.set()

        @channel.on("message")
        def message(raw: Any) -> None:
            self._receive(raw)

        @peer.on("connectionstatechange")
        async def state_changed() -> None:
            if peer.connectionState in {"failed", "closed", "disconnected"}:
                self._fail(ConnectionError("P2P connection closed"))

        try:
            offer = await peer.createOffer()
            await peer.setLocalDescription(offer)
            await _wait_for_ice_gathering(peer)
            local = peer.localDescription
            if local is None:
                raise ConnectionError("WebRTC offer was not created")
            answer = await exchange({"type": local.type, "sdp": local.sdp})
            await peer.setRemoteDescription(
                RTCSessionDescription(sdp=str(answer["sdp"]), type=str(answer["type"]))
            )
            await asyncio.wait_for(self._open.wait(), timeout=timeout)
        except Exception:
            await self.close()
            raise

    async def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
        timeout: float = 120.0,
    ) -> P2PResponse:
        if not self.connected or self._channel is None:
            raise ConnectionError("P2P connection is not ready")
        if len(body) > _MAX_BODY_BYTES:
            raise ValueError("P2P request body is too large")
        request_id = "p2p_" + secrets.token_urlsafe(18)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[P2PResponse] = loop.create_future()
        self._pending[request_id] = {
            "future": future,
            "status": 0,
            "headers": {},
            "expected": -1,
            "body": bytearray(),
        }
        try:
            await _send_json(
                self._channel,
                {
                    "type": "request_start",
                    "request_id": request_id,
                    "method": method.upper(),
                    "path": path,
                    "headers": headers or {},
                    "body_length": len(body),
                },
            )
            for offset in range(0, len(body), _CHUNK_BYTES):
                await _send_json(
                    self._channel,
                    {
                        "type": "request_body",
                        "request_id": request_id,
                        "data": encode_base64url(body[offset : offset + _CHUNK_BYTES]),
                    },
                )
            await _send_json(
                self._channel,
                {"type": "request_end", "request_id": request_id},
            )
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(request_id, None)

    async def close(self) -> None:
        peer, self._peer = self._peer, None
        self._channel = None
        self._open.clear()
        self._fail(ConnectionError("P2P connection closed"))
        if peer is not None:
            await peer.close()

    def _receive(self, raw: Any) -> None:
        message: dict[str, Any] = {}
        try:
            message = json.loads(raw.decode() if isinstance(raw, bytes) else str(raw))
            request_id = str(message.get("request_id", ""))
            pending = self._pending.get(request_id)
            if pending is None:
                return
            message_type = str(message.get("type", ""))
            if message_type == "response_start":
                pending["status"] = int(message["status"])
                pending["headers"] = {
                    str(key): str(value)
                    for key, value in dict(message.get("headers", {})).items()
                }
                pending["expected"] = int(message["body_length"])
                if (
                    pending["status"] < 100
                    or pending["status"] > 599
                    or pending["expected"] < 0
                    or pending["expected"] > _MAX_BODY_BYTES
                ):
                    raise ValueError("P2P response start is invalid")
                return
            if message_type == "response_body":
                pending["body"].extend(decode_base64url(str(message.get("data", ""))))
                if len(pending["body"]) > _MAX_BODY_BYTES:
                    raise ValueError("P2P response body is too large")
                return
            if message_type == "response_end":
                if len(pending["body"]) != pending["expected"]:
                    raise ValueError("P2P response body is incomplete")
                future = pending["future"]
                if not future.done():
                    future.set_result(
                        P2PResponse(
                            status=pending["status"],
                            headers=pending["headers"],
                            body=bytes(pending["body"]),
                        )
                    )
                return
            if message_type == "reset":
                raise ConnectionError("P2P request was reset")
            raise ValueError("P2P response message is invalid")
        except Exception as exc:
            pending = self._pending.get(str(message.get("request_id", "")))
            if pending is not None and not pending["future"].done():
                pending["future"].set_exception(exc)

    def _fail(self, error: Exception) -> None:
        for pending in self._pending.values():
            future = pending["future"]
            if not future.done():
                future.set_exception(error)
        self._pending.clear()


async def _send_json(channel: Any, message: dict[str, Any]) -> None:
    while int(getattr(channel, "bufferedAmount", 0)) > _BUFFER_HIGH_WATER:
        await asyncio.sleep(0.005)
    channel.send(json.dumps(message, separators=(",", ":"), ensure_ascii=False))


async def _wait_for_ice_gathering(
    peer: RTCPeerConnection,
    *,
    timeout: float = 8.0,
) -> None:
    if peer.iceGatheringState == "complete":
        return
    completed = asyncio.Event()

    @peer.on("icegatheringstatechange")
    def changed() -> None:
        if peer.iceGatheringState == "complete":
            completed.set()

    changed()
    await asyncio.wait_for(completed.wait(), timeout=timeout)


__all__ = [
    "P2PClient",
    "P2PResponse",
    "P2PServer",
    "P2PUnavailableError",
    "p2p_available",
]
