from __future__ import annotations

import ipaddress
import struct

from knoa_platform.mdns import SERVICE_TYPE, build_announcement


def test_build_announcement_contains_ptr_srv_txt_and_a_records() -> None:
    packet = build_announcement(
        node_id="node_test-1",
        port=9532,
        address="192.168.1.20",
        version="0.2.65",
        signing_public_key="public-key",
    )
    assert struct.unpack("!HHHHHH", packet[:12]) == (0, 0x8400, 0, 4, 0, 0)
    assert SERVICE_TYPE.encode() not in packet  # DNS names are label encoded.
    assert b"_knoa-node" in packet
    assert b"node_id=node_test-1" in packet
    assert ipaddress.ip_address("192.168.1.20").packed in packet
    assert struct.pack("!H", 9532) in packet
