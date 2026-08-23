from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

from knoa_platform.hub.repository import HubRepository
from knoa_platform.hub.service import HubService


class _Clock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _signature(secret: str, event_id: str, timestamp: str, body: bytes) -> str:
    transcript = event_id.encode() + b"\n" + timestamp.encode() + b"\n" + body
    return hmac.new(secret.encode(), transcript, hashlib.sha256).hexdigest()


def test_webhook_ingress_verifies_hmac_deduplicates_and_waits_for_node_ack(
    tmp_path: Path,
) -> None:
    clock = _Clock(1_000.0)
    repository = HubRepository(tmp_path / "hub.db", hub_id="workspace-a", clock=clock)
    service = HubService(
        repository,
        tmp_path / "hub.key",
        owner_token="owner-" + "x" * 40,
        clock=clock,
    )
    secret = "secret-" + "s" * 32
    repository.put_webhook_route({
        "route_id": "whr_route_a",
        "account_id": "subject_owner",
        "node_id": "node-a",
        "principal_id": "principal-a",
        "task_id": "task-a",
        "trigger_id": "trigger-a",
        "display_name": "Build event",
        "secret_ciphertext": service._encrypt_webhook_secret(secret),
    })
    body = json.dumps({"ref": "main"}, separators=(",", ":")).encode()
    signature = _signature(secret, "event-a", "1000", body)

    first, created = service.accept_webhook(
        "whr_route_a", event_id="event-a", timestamp_text="1000",
        signature=signature, body=body, payload={"ref": "main"},
    )
    repeated, repeated_created = service.accept_webhook(
        "whr_route_a", event_id="event-a", timestamp_text="1000",
        signature=signature, body=body, payload={"ref": "main"},
    )

    assert created is True
    assert repeated_created is False
    assert repeated["ingress_id"] == first["ingress_id"]
    pulled = repository.pull_webhook_events("node-a")
    assert pulled[0]["trigger_id"] == "trigger-a"
    assert pulled[0]["payload"] == {"ref": "main"}
    assert repository.acknowledge_webhook_events("node-b", (first["ingress_id"],)) == 0
    assert repository.acknowledge_webhook_events("node-a", (first["ingress_id"],)) == 1
    assert repository.pull_webhook_events("node-a") == ()
    with pytest.raises(PermissionError):
        service.accept_webhook(
            "whr_route_a", event_id="event-b", timestamp_text="1000",
            signature="0" * 64, body=body, payload={"ref": "main"},
        )


def test_webhook_secret_rotation_has_bounded_overlap(tmp_path: Path) -> None:
    clock = _Clock(2_000.0)
    repository = HubRepository(tmp_path / "hub.db", hub_id="workspace-a", clock=clock)
    service = HubService(
        repository,
        tmp_path / "hub.key",
        owner_token="owner-" + "x" * 40,
        clock=clock,
    )
    old = "old-" + "o" * 32
    new = "new-" + "n" * 32
    repository.put_webhook_route({
        "route_id": "whr_route_a", "account_id": "subject_owner",
        "node_id": "node-a", "principal_id": "principal-a", "task_id": "task-a",
        "trigger_id": "trigger-a", "display_name": "Build event",
        "secret_ciphertext": service._encrypt_webhook_secret(old),
    })
    repository.rotate_webhook_secret(
        "whr_route_a", secret_ciphertext=service._encrypt_webhook_secret(new), overlap_until=2_300.0
    )
    body = b"{}"
    service.accept_webhook(
        "whr_route_a", event_id="old-overlap", timestamp_text="2000",
        signature=_signature(old, "old-overlap", "2000", body), body=body, payload={},
    )
    clock.value = 2_301.0
    with pytest.raises(PermissionError):
        service.accept_webhook(
            "whr_route_a", event_id="old-expired", timestamp_text="2301",
            signature=_signature(old, "old-expired", "2301", body), body=body, payload={},
        )
