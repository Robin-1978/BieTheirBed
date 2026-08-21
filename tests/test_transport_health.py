from knoa_platform.transport_health import TransportHealth


def test_transport_health_keeps_priority_and_stage_metrics() -> None:
    health = TransportHealth()
    health.record("p2p", "discovery", ok=True)
    health.record("p2p", "verification", ok=True)
    health.record("relay", "verification", ok=False, error=" timeout ")
    health.activate("p2p", reason="mDNS not found")
    snapshot = health.snapshot()
    assert snapshot["preferred_order"] == ["mdns", "p2p", "relay"]
    assert snapshot["active"] == "p2p"
    assert snapshot["discovery_success"]["p2p"] == 1
    assert snapshot["last_error"]["relay"] == "timeout"
