from pathlib import Path

import pytest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from knoa_platform.hub.repository import HubRepository
from knoa_platform.hub.service import HubService
from knoa_platform.hub.app import HubApplication
from knoa_platform.hub.push import PushDeliveryResult
from knoa_platform.node_identity import NodeIdentityStore
from knoa_platform.relay_protocol import canonical_json, encode_base64url


def _public_key(key: Ed25519PrivateKey) -> str:
    return encode_base64url(key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    ))


def _components(tmp_path: Path):
    repository = HubRepository(tmp_path / "hub.db", hub_id="workspace-1", clock=lambda: 1000.0)
    hub = HubService(
        repository,
        tmp_path / "hub.key",
        owner_token="o" * 43,
        clock=lambda: 1000.0,
    )
    node = NodeIdentityStore(tmp_path / "node.json", clock=lambda: 1000.0).load_or_create()
    grant = repository.create_enrollment_grant()
    transcript = {
        "audience": "knoa-node-enrollment-v1",
        "hub_id": hub.hub_id,
        "grant_id": grant.grant_id,
        "challenge": grant.challenge,
        "node_id": node.node_id,
        "signing_public_key": node.signing_public_key,
        "signing_key_version": node.signing_key_version,
        "configuration_public_key": node.configuration_public_key,
        "configuration_key_version": node.configuration_key_version,
    }
    hub.enroll_node({
        **transcript,
        "grant_secret": grant.secret,
        "display_name": "Desktop",
        "platform": "linux",
        "version": "1",
        "signature": node.sign(canonical_json(transcript)),
    })
    app_key = Ed25519PrivateKey.generate()
    repository.register_installation(
        "subject_owner",
        "installation-a",
        _public_key(app_key),
        "Phone",
    )
    return repository, hub, node


def _intent(node, *, intent_id: str = "ni_a", source_sequence: int = 1) -> dict:
    payload = {
        "audience": "knoa-notification-intent-v1",
        "workspace_id": "workspace-1",
        "node_id": node.node_id,
        "nonce": f"nonce-notification-{intent_id}",
        "timestamp": 1000.0,
        "intent_id": intent_id,
        "principal_id": "personal:owner",
        "category": "completed",
        "work_kind": "task",
        "work_id": "task-a",
        "execution_id": "execution-a",
        "semantic_code": "task.completed",
        "parameters": {"title": "Weekly report"},
        "deep_link": {
            "route": "task_execution",
            "task_id": "task-a",
            "execution_id": "execution-a",
        },
        "dedupe_key": "execution:execution-a:completed:",
        "priority": "normal",
        "expires_at": 2000.0,
        "source_sequence": source_sequence,
        "created_at": 1000.0,
    }
    return {**payload, "signature": node.sign(canonical_json(payload))}


def test_push_token_is_encrypted_and_notification_projection_is_idempotent(
    tmp_path: Path,
) -> None:
    repository, hub, node = _components(tmp_path)
    registered = hub.register_push_token(
        "subject_owner",
        "installation-a",
        provider="fcm",
        token="fcm-token-value-that-is-secret",
        locale="zh-CN",
        app_version="1.2.3",
    )
    assert registered["token_ciphertext"] != "fcm-token-value-that-is-secret"
    assert hub.decrypt_push_token(registered["token_ciphertext"]) == (
        "fcm-token-value-that-is-secret"
    )

    accepted = hub.publish_notification_intent(_intent(node))
    assert accepted["intent_id"] == "ni_a"
    pending = repository.pending_notification_deliveries()
    assert len(pending) == 1
    assert pending[0]["installation_id"] == "installation-a"
    assert "fcm-token-value-that-is-secret" not in repr(pending)

    notifications = repository.list_notifications("subject_owner")
    assert notifications[0]["deep_link"]["execution_id"] == "execution-a"
    assert notifications[0]["acknowledged_at"] is None
    acknowledged = repository.acknowledge_notification("subject_owner", "ni_a")
    assert acknowledged["acknowledged_at"] == 1000.0


class _Push:
    def __init__(self, *results: PushDeliveryResult) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, dict]] = []

    async def deliver(self, token: str, message: dict) -> PushDeliveryResult:
        self.calls.append((token, message))
        return self.results.pop(0)


@pytest.mark.asyncio
async def test_delivery_success_uses_typed_deep_link_and_marks_row_delivered(
    tmp_path: Path,
) -> None:
    repository, hub, node = _components(tmp_path)
    hub.register_push_token(
        "subject_owner", "installation-a", provider="fcm",
        token="fcm-token-value-that-is-secret", locale="en-US", app_version="1",
    )
    hub.publish_notification_intent(_intent(node))
    push = _Push(PushDeliveryResult(True, provider_message_id="projects/p/messages/1"))
    app = HubApplication(hub, push_delivery=push)

    await app._deliver_pending_notifications()

    assert push.calls[0][0] == "fcm-token-value-that-is-secret"
    assert push.calls[0][1]["data"] == {
        "intent_id": "ni_a",
        "category": "completed",
        "route": "task_execution",
        "task_id": "task-a",
        "execution_id": "execution-a",
    }
    assert repository.pending_notification_deliveries() == ()


@pytest.mark.asyncio
async def test_temporary_failure_retries_and_permanent_failure_invalidates_token(
    tmp_path: Path,
) -> None:
    repository, hub, node = _components(tmp_path)
    hub.register_push_token(
        "subject_owner", "installation-a", provider="fcm",
        token="fcm-token-value-that-is-secret", locale="zh-CN", app_version="1",
    )
    hub.publish_notification_intent(_intent(node))
    retrying = _Push(PushDeliveryResult(False, error_code="unavailable"))
    await HubApplication(hub, push_delivery=retrying)._deliver_pending_notifications()
    with repository._connect() as db:
        delivery = dict(db.execute(
            "SELECT * FROM notification_deliveries WHERE intent_id='ni_a'"
        ).fetchone())
    assert delivery["state"] == "retry"
    assert delivery["attempt"] == 1
    assert delivery["next_attempt_at"] == 1010.0

    # Re-registering a refreshed token reactivates the installation and makes
    # existing unexpired inbox entries eligible again.
    hub.register_push_token(
        "subject_owner", "installation-a", provider="fcm",
        token="new-fcm-token-value-that-is-secret", locale="zh-CN", app_version="2",
    )
    with repository._connect() as db:
        db.execute(
            "UPDATE notification_deliveries SET next_attempt_at=1000 WHERE intent_id='ni_a'"
        )
    invalid = _Push(PushDeliveryResult(
        False, error_code="unregistered", permanent_token_failure=True,
    ))
    await HubApplication(hub, push_delivery=invalid)._deliver_pending_notifications()
    with repository._connect() as db:
        installation = dict(db.execute(
            "SELECT * FROM push_installations WHERE installation_id='installation-a'"
        ).fetchone())
    assert installation["state"] == "invalid"
    assert repository.pending_notification_deliveries() == ()


def test_duplicate_projection_and_multiple_devices_have_one_delivery_per_device(
    tmp_path: Path,
) -> None:
    repository, hub, node = _components(tmp_path)
    second_key = Ed25519PrivateKey.generate()
    repository.register_installation(
        "subject_owner", "installation-b", _public_key(second_key), "Tablet",
    )
    for installation_id, token in (
        ("installation-a", "fcm-token-value-for-phone"),
        ("installation-b", "fcm-token-value-for-tablet"),
    ):
        hub.register_push_token(
            "subject_owner", installation_id, provider="fcm", token=token,
            locale="zh-CN", app_version="1",
        )
    projection = _intent(node)
    hub.publish_notification_intent(projection)
    # A duplicate transport envelope is rejected by nonce replay protection;
    # retrying with a fresh envelope but the same intent remains idempotent.
    duplicate = _intent(node)
    duplicate["nonce"] = "nonce-notification-ni-a-retry"
    unsigned = {key: value for key, value in duplicate.items() if key != "signature"}
    duplicate["signature"] = node.sign(canonical_json(unsigned))
    hub.publish_notification_intent(duplicate)

    assert len(repository.list_notifications("subject_owner")) == 1
    assert {row["installation_id"] for row in repository.pending_notification_deliveries()} == {
        "installation-a", "installation-b",
    }


def test_push_token_registration_is_account_scoped(tmp_path: Path) -> None:
    _repository, hub, _node = _components(tmp_path)
    try:
        hub.register_push_token(
            "another-account",
            "installation-a",
            provider="fcm",
            token="fcm-token-value-that-is-secret",
            locale="en-US",
            app_version="1",
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("cross-account push token registration must fail")
