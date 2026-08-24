from __future__ import annotations

from knoa_platform.gateway.audit import GatewayAuditRepository


def test_gateway_audit_is_device_scoped_ordered_and_secret_free(tmp_path) -> None:
    now = [100.0]
    repository = GatewayAuditRepository(
        tmp_path / "data" / "gateway.db",
        clock=lambda: now[0],
    )
    first = repository.append(
        "paired",
        device_id="dev-a",
        principal_id="personal:owner",
        remote_address="192.0.2.10",
    )
    now[0] = 101.0
    second = repository.append(
        "command",
        device_id="dev-a",
        principal_id="personal:owner",
        remote_address="192.0.2.10",
        detail_code="GET /v1/tasks",
    )
    repository.append(
        "command",
        device_id="dev-b",
        principal_id="personal:owner",
    )

    events = repository.list_for_device(
        "personal:owner",
        "dev-a",
        after_id=first.event_id,
    )

    assert events == (second,)
    assert second.remote_address_hash
    assert second.remote_address_hash != "192.0.2.10"


def test_gateway_audit_prunes_by_age_and_hard_event_limit(tmp_path) -> None:
    now = [200_000.0]
    repository = GatewayAuditRepository(
        tmp_path / "data" / "gateway.db",
        clock=lambda: now[0],
    )
    now[0] = 1.0
    repository.append("paired", device_id="dev-a", principal_id="owner")
    now[0] = 150_000.0
    repository.append("command", device_id="dev-a", principal_id="owner")
    repository.append("command", device_id="dev-a", principal_id="owner")
    repository.append("command", device_id="dev-a", principal_id="owner")
    now[0] = 200_000.0

    assert repository.prune(retention_seconds=86_400, max_events=2) == 2
    events = repository.list_for_device("owner", "dev-a")
    assert len(events) == 2
    assert [event.event_id for event in events] == [3, 4]
