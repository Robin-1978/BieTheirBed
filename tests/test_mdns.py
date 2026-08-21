from __future__ import annotations

import asyncio
import ipaddress
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knoa_platform.mdns import (
    MdnsPublisher,
    SERVICE_TYPE,
    build_announcement,
    lan_addresses,
)


def test_build_announcement_contains_ptr_srv_txt_and_a_records() -> None:
    packet = build_announcement(
        node_id="node_test-1",
        port=9541,
        address="192.168.1.20",
        version="0.2.65",
        signing_public_key="public-key",
    )
    assert struct.unpack("!HHHHHH", packet[:12]) == (0, 0x8400, 0, 4, 0, 0)
    assert SERVICE_TYPE.encode() not in packet  # DNS names are label encoded.
    assert b"_knoa-node" in packet
    assert b"node_id=node_test-1" in packet
    assert ipaddress.ip_address("192.168.1.20").packed in packet
    assert struct.pack("!H", 9541) in packet


def test_build_announcement_includes_every_lan_address() -> None:
    packet = build_announcement(
        node_id="node_test-1",
        port=9541,
        addresses=["192.168.1.20", "10.12.28.139"],
        version="0.2.65",
        signing_public_key="public-key",
    )
    assert struct.unpack("!HHHHHH", packet[:12]) == (0, 0x8400, 0, 5, 0, 0)
    assert ipaddress.ip_address("192.168.1.20").packed in packet
    assert ipaddress.ip_address("10.12.28.139").packed in packet


def test_lan_addresses_skips_loopback_and_docker() -> None:
    with patch("knoa_platform.mdns._interface_ipv4_addresses") as mocked:
        mocked.return_value = ["10.12.28.139", "10.12.10.63"]
        assert lan_addresses() == ["10.12.28.139", "10.12.10.63"]


def test_mdns_send_drops_when_multicast_socket_is_full() -> None:
    class _FullSocket:
        def setsockopt(self, *_args: object) -> None:
            return None

        def sendto(self, _packet: bytes, _address: tuple[str, int]) -> None:
            raise BlockingIOError

    publisher = MdnsPublisher.__new__(MdnsPublisher)
    publisher._socket = _FullSocket()
    publisher.addresses = ["192.168.1.20"]
    publisher._send_nowait(b"packet", ("224.0.0.251", 5353))


def test_mdns_multicast_send_selects_every_interface() -> None:
    class _Socket:
        def __init__(self) -> None:
            self.selected: list[bytes] = []
            self.sent: list[tuple[bytes, tuple[str, int]]] = []

        def setsockopt(self, _level: int, _option: int, value: bytes) -> None:
            self.selected.append(value)

        def sendto(self, packet: bytes, address: tuple[str, int]) -> None:
            self.sent.append((packet, address))

    socket = _Socket()
    publisher = MdnsPublisher.__new__(MdnsPublisher)
    publisher._socket = socket
    publisher.addresses = ["192.168.1.20", "10.12.28.139"]

    publisher._send_nowait(b"packet", ("224.0.0.251", 5353))

    assert socket.selected == [b"\xc0\xa8\x01\x14", b"\x0a\x0c\x1c\x8b"]
    assert socket.sent == [
        (b"packet", ("224.0.0.251", 5353)),
        (b"packet", ("224.0.0.251", 5353)),
    ]


async def test_mdns_responder_ignores_own_address() -> None:
    publisher = MdnsPublisher(
        node_id="node_test-1",
        port=9541,
        version="0.2.65",
        signing_public_key="public-key",
        addresses=["192.168.1.20", "192.168.1.21"],
    )
    publisher._listener = MagicMock()
    publisher._socket = MagicMock()
    publisher._send_nowait = MagicMock()
    packet = b"announcement"
    looped_query = b"query _knoa-node._tcp.local"
    peer_query = b"query _knoa-node._tcp.local"

    with patch("asyncio.get_running_loop") as get_loop:
        get_loop.return_value.sock_recvfrom = AsyncMock(
            side_effect=[
                (looped_query, ("192.168.1.20", 5353)),
                (peer_query, ("192.168.1.55", 5353)),
                asyncio.CancelledError(),
            ]
        )
        with pytest.raises(asyncio.CancelledError):
            await publisher._answer_queries(packet)

    assert publisher._send_nowait.call_count == 2
    publisher._send_nowait.assert_any_call(packet, ("224.0.0.251", 5353))
    publisher._send_nowait.assert_any_call(packet, ("192.168.1.55", 5353))
