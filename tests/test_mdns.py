from __future__ import annotations

import ipaddress
import struct

from knoa_platform.mdns import MdnsPublisher, SERVICE_TYPE, build_announcement


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


def test_mdns_send_drops_when_multicast_socket_is_full() -> None:
    class _FullSocket:
        def sendto(self, _packet: bytes, _address: tuple[str, int]) -> None:
            raise BlockingIOError

    publisher = MdnsPublisher.__new__(MdnsPublisher)
    publisher._socket = _FullSocket()
    publisher._send_nowait(b"packet", ("224.0.0.251", 5353))
