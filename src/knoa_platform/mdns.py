"""Small, dependency-free mDNS service advertiser for LAN Node discovery."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import struct
from collections.abc import Mapping

logger = logging.getLogger(__name__)

MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353
SERVICE_TYPE = "_knoa-node._tcp.local."


def _labels(value: str) -> bytes:
    result = bytearray()
    for label in value.rstrip(".").split("."):
        encoded = label.encode("utf-8")
        if not 0 < len(encoded) < 64:
            raise ValueError("mDNS label must contain 1-63 bytes")
        result.append(len(encoded))
        result.extend(encoded)
    result.append(0)
    return bytes(result)


def _record(name: str, record_type: int, data: bytes, *, ttl: int = 120) -> bytes:
    return _labels(name) + struct.pack("!HHIH", record_type, 1, ttl, len(data)) + data


def _txt(values: Mapping[str, str]) -> bytes:
    payload = bytearray()
    for key, value in values.items():
        item = f"{key}={value}".encode("utf-8")
        if len(item) > 255:
            continue
        payload.append(len(item))
        payload.extend(item)
    return bytes(payload) or b"\x00"


def _local_address() -> str | None:
    """Choose a routable local IPv4 address without sending any traffic."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("224.0.0.251", MDNS_PORT))
        address = probe.getsockname()[0]
    except OSError:
        address = None
    finally:
        probe.close()
    try:
        parsed = ipaddress.ip_address(address or "")
    except ValueError:
        return None
    if parsed.is_loopback or parsed.is_unspecified or parsed.is_link_local:
        return None
    return str(parsed)


def build_announcement(
    *,
    node_id: str,
    port: int,
    address: str,
    version: str,
    signing_public_key: str,
) -> bytes:
    """Build a standard PTR/SRV/TXT/A announcement packet."""
    safe_id = "".join(char if char.isalnum() or char in "-_" else "-" for char in node_id)
    instance = f"{safe_id}.{SERVICE_TYPE}"
    host = f"{safe_id}.local."
    header = struct.pack("!HHHHHH", 0, 0x8400, 0, 4, 0, 0)
    ipv4 = ipaddress.ip_address(address)
    if ipv4.version != 4:
        raise ValueError("mDNS advertiser currently supports IPv4 only")
    txt = _txt({
        "node_id": node_id,
        "version": version,
        "protocol": "1",
        "signing_key": signing_public_key,
    })
    return header + b"".join((
        _record(SERVICE_TYPE, 12, _labels(instance)),
        _record(instance, 33, struct.pack("!HHH", 0, 0, port) + _labels(host)),
        _record(instance, 16, txt),
        _record(host, 1, ipv4.packed),
    ))


class MdnsPublisher:
    """Periodically announce and withdraw one Knoa Node service."""

    def __init__(
        self,
        *,
        node_id: str,
        port: int,
        version: str,
        signing_public_key: str,
        address: str | None = None,
    ) -> None:
        self.node_id = node_id
        self.port = port
        self.version = version
        self.signing_public_key = signing_public_key
        self.address = address or _local_address()
        self._socket: socket.socket | None = None
        self._task: asyncio.Task[None] | None = None
        self._listener: socket.socket | None = None
        self._listener_task: asyncio.Task[None] | None = None

    async def start(self) -> bool:
        if self._task is not None and not self._task.done():
            return True
        if not self.address:
            logger.info("mDNS disabled: no routable LAN IPv4 address")
            return False
        try:
            packet = build_announcement(
                node_id=self.node_id,
                port=self.port,
                address=self.address,
                version=self.version,
                signing_public_key=self.signing_public_key,
            )
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 0)
            # Never perform a blocking multicast send on the application's
            # asyncio event loop.  On a congested or unavailable LAN route,
            # the kernel can block sendto() while waiting for buffer space,
            # which would stall every Node HTTP/MCP endpoint.
            sock.setblocking(False)
            self._socket = sock
            self._task = asyncio.create_task(self._announce(packet), name="knoa-mdns")
            try:
                listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if hasattr(socket, "SO_REUSEPORT"):
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                listener.bind(("", MDNS_PORT))
                listener.setsockopt(
                    socket.IPPROTO_IP,
                    socket.IP_ADD_MEMBERSHIP,
                    socket.inet_aton(MDNS_GROUP) + socket.inet_aton(self.address),
                )
                listener.setblocking(False)
                self._listener = listener
                self._listener_task = asyncio.create_task(
                    self._answer_queries(packet), name="knoa-mdns-responder"
                )
            except OSError as exc:
                logger.info("mDNS query responder unavailable; announcements continue: %s", exc)
            return True
        except OSError as exc:
            logger.warning("mDNS advertiser unavailable: %s", exc)
            await self.stop()
            return False

    async def _announce(self, packet: bytes) -> None:
        assert self._socket is not None
        try:
            while True:
                self._send_nowait(packet, (MDNS_GROUP, MDNS_PORT))
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            goodbye = packet.replace(struct.pack("!I", 120), struct.pack("!I", 0))
            try:
                for _ in range(2):
                    self._send_nowait(goodbye, (MDNS_GROUP, MDNS_PORT))
                    await asyncio.sleep(0.05)
            except OSError:
                pass
            raise
        except OSError as exc:
            logger.debug("mDNS announcement stopped: %s", exc)

    async def _answer_queries(self, packet: bytes) -> None:
        if self._listener is None or self._socket is None:
            return
        loop = asyncio.get_running_loop()
        try:
            while True:
                query, sender = await loop.sock_recvfrom(self._listener, 4096)
                if sender[0] == self.address:
                    continue
                if b"_knoa-node" in query or b"_services" in query:
                    self._send_nowait(packet, (MDNS_GROUP, MDNS_PORT))
                    if sender[0] != MDNS_GROUP:
                        self._send_nowait(packet, sender)
        except asyncio.CancelledError:
            raise
        except OSError as exc:
            logger.debug("mDNS query responder stopped: %s", exc)

    def _send_nowait(self, packet: bytes, address: tuple[str, int]) -> None:
        """Best-effort non-blocking send used by all mDNS paths."""
        assert self._socket is not None
        try:
            self._socket.sendto(packet, address)
        except BlockingIOError:
            logger.debug("mDNS packet dropped because the socket buffer is full")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        listener_task, self._listener_task = self._listener_task, None
        if listener_task is not None:
            listener_task.cancel()
            await asyncio.gather(listener_task, return_exceptions=True)
        listener, self._listener = self._listener, None
        if listener is not None:
            listener.close()
        sock, self._socket = self._socket, None
        if sock is not None:
            sock.close()


__all__ = ["MDNS_GROUP", "MDNS_PORT", "SERVICE_TYPE", "MdnsPublisher", "build_announcement"]
