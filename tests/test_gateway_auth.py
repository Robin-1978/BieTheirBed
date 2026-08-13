from __future__ import annotations

import base64

import pytest

from knoa_platform.gateway import (
    GatewayAuthenticationRejectedError,
    GatewayAuthenticationService,
    GatewayAuthRepository,
    GatewayIdentityRepository,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _public_key(private_key: Ed25519PrivateKey) -> str:
    return _encoded(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )


def _signature(private_key: Ed25519PrivateKey, payload: bytes) -> str:
    return _encoded(private_key.sign(payload))


def _service(tmp_path, now: list[float]):
    database = tmp_path / "data" / "gateway.db"
    identity_ids = iter(("pgr_pair", "dev_phone"))
    challenge_ids = iter(("gch_pair", "gch_auth", "gch_replay"))
    identities = GatewayIdentityRepository(
        database,
        clock=lambda: now[0],
        grant_id_factory=lambda: next(identity_ids),
        device_id_factory=lambda: next(identity_ids),
        secret_factory=lambda: "p" * 43,
    )
    auth = GatewayAuthRepository(
        database,
        clock=lambda: now[0],
        challenge_id_factory=lambda: next(challenge_ids),
        session_id_factory=lambda: "gws_phone",
        nonce_factory=lambda: "n" * 43,
        session_secret_factory=lambda: "t" * 43,
    )
    return GatewayAuthenticationService(identities, auth), identities, auth


def _pair(service, identities, private_key):
    grant = identities.create_pairing_grant("personal:owner")
    challenge = service.begin_pairing(grant.grant_id)
    public_key = _public_key(private_key)
    payload = service.pairing_payload(
        challenge_id=challenge.challenge_id,
        grant_id=grant.grant_id,
        nonce=challenge.nonce,
        display_name="Robin Phone",
        public_key=public_key,
    )
    device = service.complete_pairing(
        grant_id=grant.grant_id,
        grant_secret=grant.secret,
        challenge_id=challenge.challenge_id,
        nonce=challenge.nonce,
        display_name="Robin Phone",
        public_key=public_key,
        signature=_signature(private_key, payload),
    )
    return device


def test_pairing_requires_ed25519_private_key_proof(tmp_path) -> None:
    now = [100.0]
    service, identities, _auth = _service(tmp_path, now)
    private_key = Ed25519PrivateKey.generate()
    grant = identities.create_pairing_grant("personal:owner")
    challenge = service.begin_pairing(grant.grant_id)
    public_key = _public_key(private_key)
    payload = service.pairing_payload(
        challenge_id=challenge.challenge_id,
        grant_id=grant.grant_id,
        nonce=challenge.nonce,
        display_name="Robin Phone",
        public_key=public_key,
    )

    wrong_key = Ed25519PrivateKey.generate()
    with pytest.raises(GatewayAuthenticationRejectedError):
        service.complete_pairing(
            grant_id=grant.grant_id,
            grant_secret=grant.secret,
            challenge_id=challenge.challenge_id,
            nonce=challenge.nonce,
            display_name="Robin Phone",
            public_key=public_key,
            signature=_signature(wrong_key, payload),
        )

    device = service.complete_pairing(
        grant_id=grant.grant_id,
        grant_secret=grant.secret,
        challenge_id=challenge.challenge_id,
        nonce=challenge.nonce,
        display_name="Robin Phone",
        public_key=public_key,
        signature=_signature(private_key, payload),
    )
    assert device.principal_id == "personal:owner"
    assert device.public_key == public_key


def test_device_authentication_issues_short_lived_opaque_session(tmp_path) -> None:
    now = [100.0]
    service, identities, _auth = _service(tmp_path, now)
    private_key = Ed25519PrivateKey.generate()
    device = _pair(service, identities, private_key)
    challenge = service.begin_authentication(device.device_id)
    payload = service.authentication_payload(
        challenge_id=challenge.challenge_id,
        device_id=device.device_id,
        nonce=challenge.nonce,
    )

    issued = service.complete_authentication(
        device_id=device.device_id,
        challenge_id=challenge.challenge_id,
        nonce=challenge.nonce,
        signature=_signature(private_key, payload),
        session_ttl_seconds=60,
    )
    authenticated = service.authenticate_session(issued.token)

    assert issued.token == "v1.gws_phone." + "t" * 43
    assert authenticated.session_id == issued.session_id
    assert authenticated.device.device_id == device.device_id
    assert authenticated.device.last_seen_at == 100.0


def test_authentication_challenge_cannot_be_replayed(tmp_path) -> None:
    now = [100.0]
    service, identities, _auth = _service(tmp_path, now)
    private_key = Ed25519PrivateKey.generate()
    device = _pair(service, identities, private_key)
    challenge = service.begin_authentication(device.device_id)
    payload = service.authentication_payload(
        challenge_id=challenge.challenge_id,
        device_id=device.device_id,
        nonce=challenge.nonce,
    )
    signature = _signature(private_key, payload)
    service.complete_authentication(
        device_id=device.device_id,
        challenge_id=challenge.challenge_id,
        nonce=challenge.nonce,
        signature=signature,
        session_ttl_seconds=60,
    )

    with pytest.raises(GatewayAuthenticationRejectedError):
        service.complete_authentication(
            device_id=device.device_id,
            challenge_id=challenge.challenge_id,
            nonce=challenge.nonce,
            signature=signature,
            session_ttl_seconds=60,
        )


def test_device_revocation_invalidates_existing_session_immediately(tmp_path) -> None:
    now = [100.0]
    service, identities, _auth = _service(tmp_path, now)
    private_key = Ed25519PrivateKey.generate()
    device = _pair(service, identities, private_key)
    challenge = service.begin_authentication(device.device_id)
    payload = service.authentication_payload(
        challenge_id=challenge.challenge_id,
        device_id=device.device_id,
        nonce=challenge.nonce,
    )
    issued = service.complete_authentication(
        device_id=device.device_id,
        challenge_id=challenge.challenge_id,
        nonce=challenge.nonce,
        signature=_signature(private_key, payload),
        session_ttl_seconds=60,
    )
    identities.revoke_device(device.principal_id, device.device_id)

    with pytest.raises(GatewayAuthenticationRejectedError):
        service.authenticate_session(issued.token)


def test_gateway_session_expires_and_rejects_wrong_secret(tmp_path) -> None:
    now = [100.0]
    service, identities, _auth = _service(tmp_path, now)
    private_key = Ed25519PrivateKey.generate()
    device = _pair(service, identities, private_key)
    challenge = service.begin_authentication(device.device_id)
    payload = service.authentication_payload(
        challenge_id=challenge.challenge_id,
        device_id=device.device_id,
        nonce=challenge.nonce,
    )
    issued = service.complete_authentication(
        device_id=device.device_id,
        challenge_id=challenge.challenge_id,
        nonce=challenge.nonce,
        signature=_signature(private_key, payload),
        session_ttl_seconds=60,
    )

    with pytest.raises(GatewayAuthenticationRejectedError):
        service.authenticate_session("v1.gws_phone." + "x" * 43)
    with pytest.raises(GatewayAuthenticationRejectedError):
        service.authenticate_session("v1.invalid session.secret")
    now[0] = 160.0
    with pytest.raises(GatewayAuthenticationRejectedError):
        service.authenticate_session(issued.token)
