from __future__ import annotations

import base64
import stat

import pytest

from pc_assistant.gateway import (
    DeviceNotFoundError,
    GatewayIdentityRepository,
    PairingGrantRejectedError,
)


def _public_key(seed: int) -> str:
    return base64.urlsafe_b64encode(bytes([seed]) * 32).decode("ascii").rstrip("=")


def _repository(tmp_path, now: list[float]) -> GatewayIdentityRepository:
    ids = iter(("pgr_test", "dev_test", "dev_other"))
    return GatewayIdentityRepository(
        tmp_path / "data" / "gateway.db",
        clock=lambda: now[0],
        grant_id_factory=lambda: next(ids),
        device_id_factory=lambda: next(ids),
        secret_factory=lambda: "s" * 43,
    )


def test_pairing_grant_is_single_use_and_binds_device_to_principal(tmp_path) -> None:
    now = [100.0]
    repository = _repository(tmp_path, now)
    grant = repository.create_pairing_grant("personal:owner")

    device = repository.register_verified_device(
        grant.grant_id,
        grant.secret,
        display_name="Robin's Phone",
        public_key=_public_key(1),
    )

    assert device.device_id == "dev_test"
    assert device.principal_id == "personal:owner"
    assert device.state == "active"
    assert repository.list_devices("personal:owner") == (device,)
    with pytest.raises(PairingGrantRejectedError):
        repository.register_verified_device(
            grant.grant_id,
            grant.secret,
            display_name="Second Phone",
            public_key=_public_key(2),
        )


def test_wrong_pairing_secret_does_not_consume_grant(tmp_path) -> None:
    now = [100.0]
    repository = _repository(tmp_path, now)
    grant = repository.create_pairing_grant("personal:owner")

    with pytest.raises(PairingGrantRejectedError):
        repository.register_verified_device(
            grant.grant_id,
            "wrong-secret-value-that-is-long-enough",
            display_name="Phone",
            public_key=_public_key(1),
        )

    device = repository.register_verified_device(
        grant.grant_id,
        grant.secret,
        display_name="Phone",
        public_key=_public_key(1),
    )
    assert device.state == "active"


def test_expired_pairing_grant_is_rejected(tmp_path) -> None:
    now = [100.0]
    repository = _repository(tmp_path, now)
    grant = repository.create_pairing_grant(
        "personal:owner",
        ttl_seconds=30,
    )
    now[0] = 130.0

    with pytest.raises(PairingGrantRejectedError):
        repository.register_verified_device(
            grant.grant_id,
            grant.secret,
            display_name="Phone",
            public_key=_public_key(1),
        )


def test_device_revocation_is_immediate_and_principal_scoped(tmp_path) -> None:
    now = [100.0]
    repository = _repository(tmp_path, now)
    grant = repository.create_pairing_grant("personal:owner")
    device = repository.register_verified_device(
        grant.grant_id,
        grant.secret,
        display_name="Phone",
        public_key=_public_key(1),
    )

    with pytest.raises(DeviceNotFoundError):
        repository.revoke_device("personal:other", device.device_id)
    now[0] = 200.0
    revoked = repository.revoke_device("personal:owner", device.device_id)

    assert revoked.state == "revoked"
    assert revoked.revoked_at == 200.0
    with pytest.raises(DeviceNotFoundError):
        repository.active_device("personal:owner", device.device_id)


def test_gateway_identity_database_is_owner_only(tmp_path) -> None:
    now = [100.0]
    repository = _repository(tmp_path, now)
    repository.create_pairing_grant("personal:owner")
    database = tmp_path / "data" / "gateway.db"

    assert stat.S_IMODE(database.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
