"""Small, dependency-free mDNS service advertiser for LAN Node discovery."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import struct
import subprocess
import sys
from collections.abc import Mapping, Sequence

logger = logging.getLogger(__name__)

MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353
SERVICE_TYPE = "_knoa-node._tcp.local."
_SKIP_IFACE_PREFIXES = ("lo", "docker", "br-", "veth", "virbr", "podman", "tun", "tap")


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


def _is_advertisable_ipv4(parsed: ipaddress.IPv4Address) -> bool:
    if parsed.is_loopback or parsed.is_unspecified or parsed.is_link_local:
        return False
    if parsed in ipaddress.ip_network("169.254.0.0/16"):
        return False
    # Typical container bridge ranges are not useful for phone LAN discovery.
    if parsed in ipaddress.ip_network("172.17.0.0/16"):
        return False
    return True


def _add_ipv4(addresses: list[str], seen: set[str], value: str | None) -> None:
    if not value or value in seen:
        return
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        return
    if parsed.version != 4 or not _is_advertisable_ipv4(parsed):
        return
    seen.add(value)
    addresses.append(value)


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
    if not _is_advertisable_ipv4(parsed):
        return None
    return str(parsed)


def _interface_ipv4_addresses() -> list[str]:
    """Collect non-loopback IPv4 addresses bound to real LAN interfaces."""
    addresses: list[str] = []
    seen: set[str] = set()
    if sys.platform == "win32":
        try:
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-NetIPAddress -AddressFamily IPv4 -AddressState Preferred | "
                    "Where-Object { $_.InterfaceAlias -notmatch "
                    "'Loopback|vEthernet|WSL|Docker|VirtualBox|VMware|Hyper-V|"
                    "Tailscale|ZeroTier|WireGuard' -and $_.PrefixOrigin -ne 'WellKnown' } | "
                    "Select-Object -ExpandProperty IPAddress",
                ],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if completed.returncode == 0:
                for line in completed.stdout.splitlines():
                    _add_ipv4(addresses, seen, line.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            completed = subprocess.run(
                ["ip", "-4", "-o", "addr", "show"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if completed.returncode == 0:
                for line in completed.stdout.splitlines():
                    parts = line.split()
                    if len(parts) < 4 or "inet" not in parts:
                        continue
                    iface = parts[1]
                    if any(iface.startswith(prefix) for prefix in _SKIP_IFACE_PREFIXES):
                        continue
                    inet_index = parts.index("inet")
                    _add_ipv4(addresses, seen, parts[inet_index + 1].split("/", 1)[0])
        except (OSError, subprocess.SubprocessError):
            pass
    # Hostname resolution is only a fallback.  On Windows it can include a
    # VPN/virtual adapter that the PowerShell interface query intentionally
    # filtered out, so never merge those guesses into a verified interface
    # list when the query already returned usable LAN addresses.
    if not addresses:
        try:
            for infos in socket.getaddrinfo(
                socket.gethostname(), None, socket.AF_INET, socket.SOCK_DGRAM
            ):
                _add_ipv4(addresses, seen, infos[4][0])
        except OSError:
            pass
        _add_ipv4(addresses, seen, _local_address())
    return addresses


def lan_addresses() -> list[str]:
    """Return every IPv4 address that should appear in mDNS announcements."""
    return _interface_ipv4_addresses()


def build_announcement(
    *,
    node_id: str,
    port: int,
    address: str = "",
    addresses: Sequence[str] | None = None,
    version: str,
    signing_public_key: str,
) -> bytes:
    """Build a standard PTR/SRV/TXT/A announcement packet."""
    resolved = list(addresses) if addresses else ([address] if address else [])
    if not resolved:
        raise ValueError("mDNS announcement requires at least one IPv4 address")
    safe_id = "".join(char if char.isalnum() or char in "-_" else "-" for char in node_id)
    instance = f"{safe_id}.{SERVICE_TYPE}"
    host = f"{safe_id}.local."
    txt = _txt({
        "node_id": node_id,
        "version": version,
        "protocol": "1",
        "signing_key": signing_public_key,
    })
    records = [
        _record(SERVICE_TYPE, 12, _labels(instance)),
        _record(instance, 33, struct.pack("!HHH", 0, 0, port) + _labels(host)),
        _record(instance, 16, txt),
    ]
    for item in resolved:
        ipv4 = ipaddress.ip_address(item)
        if ipv4.version != 4:
            raise ValueError("mDNS advertiser currently supports IPv4 only")
        records.append(_record(host, 1, ipv4.packed))
    header = struct.pack("!HHHHHH", 0, 0x8400, 0, len(records), 0, 0)
    return header + b"".join(records)


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
        addresses: Sequence[str] | None = None,
    ) -> None:
        self.node_id = node_id
        self.port = port
        self.version = version
        self.signing_public_key = signing_public_key
        if addresses is not None:
            self.addresses = list(addresses)
        elif address:
            self.addresses = [address]
        else:
            self.addresses = lan_addresses()
        self.address = self.addresses[0] if self.addresses else None
        self._socket: socket.socket | None = None
        self._task: asyncio.Task[None] | None = None
        self._listener: socket.socket | None = None
        self._listener_task: asyncio.Task[None] | None = None
        self._last_error = ""
        self._responder_available = False

    def status(self) -> dict[str, object]:
        """Return a safe, user-facing snapshot of LAN advertisement health."""
        return {
            "enabled": True,
            "available": self._socket is not None and self._task is not None,
            "advertising": self._socket is not None and self._task is not None,
            "responder": self._responder_available,
            "addresses": list(self.addresses),
            "port": self.port,
            "service_type": SERVICE_TYPE,
            "last_error": self._last_error,
        }

    async def start(self) -> bool:
        if self._task is not None and not self._task.done():
            return True
        if not self.addresses:
            self._last_error = "no_routable_lan_address"
            logger.info("mDNS disabled: no routable LAN IPv4 address")
            return False
        try:
            packet = build_announcement(
                node_id=self.node_id,
                port=self.port,
                addresses=self.addresses,
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
                for item in self.addresses:
                    try:
                        listener.setsockopt(
                            socket.IPPROTO_IP,
                            socket.IP_ADD_MEMBERSHIP,
                            socket.inet_aton(MDNS_GROUP) + socket.inet_aton(item),
                        )
                    except OSError as exc:
                        logger.debug("mDNS membership skipped for %s: %s", item, exc)
                listener.setblocking(False)
                self._listener = listener
                self._responder_available = True
                self._listener_task = asyncio.create_task(
                    self._answer_queries(packet), name="knoa-mdns-responder"
                )
            except OSError as exc:
                self._responder_available = False
                self._last_error = f"query_responder_unavailable:{type(exc).__name__}"
                logger.info("mDNS query responder unavailable; announcements continue: %s", exc)
            logger.info(
                "mDNS advertising _knoa-node on %s:%s via %s",
                self.node_id,
                self.port,
                ", ".join(self.addresses),
            )
            if self._responder_available:
                self._last_error = ""
            return True
        except OSError as exc:
            self._last_error = f"advertiser_unavailable:{type(exc).__name__}"
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
        own_addresses = set(self.addresses)
        try:
            while True:
                query, sender = await loop.sock_recvfrom(self._listener, 4096)
                if sender[0] in own_addresses:
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
        if address == (MDNS_GROUP, MDNS_PORT):
            # A packet containing several A records is not enough for a
            # multi-NIC host: multicast egress still follows the system's
            # single default route unless the interface is selected. Send
            # once per advertised address so Wi-Fi and wired peers each see
            # the announcement on their local link.
            for interface_address in self.addresses:
                try:
                    self._socket.setsockopt(
                        socket.IPPROTO_IP,
                        socket.IP_MULTICAST_IF,
                        socket.inet_aton(interface_address),
                    )
                    self._socket.sendto(packet, address)
                except BlockingIOError:
                    logger.debug(
                        "mDNS packet dropped because the socket buffer is full on %s",
                        interface_address,
                    )
                except OSError as exc:
                    logger.debug(
                        "mDNS multicast send skipped on %s: %s",
                        interface_address,
                        exc,
                    )
            return
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
        self._responder_available = False


__all__ = [
    "MDNS_GROUP",
    "MDNS_PORT",
    "SERVICE_TYPE",
    "MdnsPublisher",
    "build_announcement",
    "lan_addresses",
]
