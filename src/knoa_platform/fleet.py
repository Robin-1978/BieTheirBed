"""Node-side sealed Fleet configuration candidate protocol V1."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from knoa_platform.configuration.models import ManagedConfig
from knoa_platform.node_identity import NodeIdentity


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def fleet_candidate_digest(document: ManagedConfig) -> str:
    return hashlib.sha256(_canonical(document.model_dump(mode="json"))).hexdigest()


def fleet_signature_payload(
    *,
    hub_id: str,
    rollout_id: str,
    node_id: str,
    device_id: str,
    expected_base_revision_digest: str,
    candidate_digest: str,
    expires_at: float,
) -> bytes:
    return _canonical(
        {
            "audience": "knoa-fleet-candidate-v1",
            "hub_id": hub_id,
            "rollout_id": rollout_id,
            "node_id": node_id,
            "device_id": device_id,
            "expected_base_revision_digest": expected_base_revision_digest,
            "candidate_digest": candidate_digest,
            "expires_at": expires_at,
        }
    )


@dataclass(frozen=True)
class SealedFleetEnvelope:
    version: str
    ephemeral_public_key: str
    nonce: str
    ciphertext: str
    associated_data: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "ephemeral_public_key": self.ephemeral_public_key,
            "nonce": self.nonce,
            "ciphertext": self.ciphertext,
            "associated_data": self.associated_data,
        }

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> SealedFleetEnvelope:
        if set(raw) != {
            "version",
            "ephemeral_public_key",
            "nonce",
            "ciphertext",
            "associated_data",
        }:
            raise ValueError("Fleet envelope fields are invalid")
        associated = raw["associated_data"]
        if not isinstance(associated, dict):
            raise ValueError(  # noqa: TRY004 - malformed external document
                "Fleet envelope associated data is invalid"
            )
        return cls(
            version=str(raw["version"]),
            ephemeral_public_key=str(raw["ephemeral_public_key"]),
            nonce=str(raw["nonce"]),
            ciphertext=str(raw["ciphertext"]),
            associated_data=dict(associated),
        )


def seal_fleet_candidate(
    configuration_public_key: str,
    payload: dict[str, Any],
    associated_data: dict[str, Any],
) -> SealedFleetEnvelope:
    ephemeral = X25519PrivateKey.generate()
    remote = X25519PublicKey.from_public_bytes(_decode(configuration_public_key))
    shared = ephemeral.exchange(remote)
    aad = _canonical(associated_data)
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=hashlib.sha256(aad).digest(),
        info=b"knoa-fleet-candidate-v1",
    ).derive(shared)
    nonce = os.urandom(12)
    ciphertext = ChaCha20Poly1305(key).encrypt(nonce, _canonical(payload), aad)
    public_key = ephemeral.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return SealedFleetEnvelope(
        version="knoa-fleet-envelope-v1",
        ephemeral_public_key=_encode(public_key),
        nonce=_encode(nonce),
        ciphertext=_encode(ciphertext),
        associated_data=associated_data,
    )


class FleetConfigurationPort(Protocol):
    async def get_config_current(self, principal_id: str): ...
    async def create_config_draft(self, principal_id: str): ...
    async def replace_config_draft(
        self, principal_id: str, draft_id: str, document: ManagedConfig, *, expected_version: int
    ): ...
    async def validate_config_draft(self, principal_id: str, draft_id: str, *, preflight: bool = False): ...
    async def publish_config_draft(
        self, principal_id: str, draft_id: str, *, expected_version: int, summary: str = ""
    ): ...


class FleetDevice(Protocol):
    public_key: str


class FleetDeviceRepositoryPort(Protocol):
    def active_device(self, principal_id: str, device_id: str) -> FleetDevice: ...


class FleetCandidateService:
    def __init__(
        self,
        identity: NodeIdentity,
        devices: FleetDeviceRepositoryPort,
        configuration: FleetConfigurationPort,
        *,
        clock=time.time,
    ) -> None:
        self._identity = identity
        self._devices = devices
        self._configuration = configuration
        self._clock = clock

    async def apply(
        self,
        principal_id: str,
        rollout_id: str,
        raw_envelope: dict[str, Any],
    ):
        envelope = SealedFleetEnvelope.parse(raw_envelope)
        payload = self._open(envelope)
        associated = envelope.associated_data
        required_aad = {
            "hub_id",
            "rollout_id",
            "node_id",
            "expected_base_revision_digest",
            "candidate_digest",
            "expires_at",
            "encryption_key_version",
        }
        if set(associated) != required_aad:
            raise ValueError("Fleet associated data fields are invalid")
        if associated["rollout_id"] != rollout_id:
            raise ValueError("Fleet rollout ID mismatch")
        if associated["node_id"] != self._identity.node_id:
            raise ValueError("Fleet candidate targets another Node")
        if int(associated["encryption_key_version"]) != self._identity.configuration_key_version:
            raise ValueError("Fleet candidate targets an unavailable encryption key")
        expires_at = float(associated["expires_at"])
        if expires_at <= self._clock() or expires_at > self._clock() + 24 * 60 * 60:
            raise ValueError("Fleet candidate is expired or exceeds maximum lifetime")
        if not isinstance(payload, dict):
            raise ValueError(  # noqa: TRY004 - malformed external document
                "Fleet candidate payload is invalid"
            )
        device_id = str(payload.get("device_id", ""))
        device = self._devices.active_device(principal_id, device_id)
        document = ManagedConfig.model_validate(payload.get("document"))
        candidate_digest = fleet_candidate_digest(document)
        if candidate_digest != associated["candidate_digest"]:
            raise ValueError("Fleet candidate digest mismatch")
        signature_payload = fleet_signature_payload(
            hub_id=str(associated["hub_id"]),
            rollout_id=rollout_id,
            node_id=self._identity.node_id,
            device_id=device_id,
            expected_base_revision_digest=str(associated["expected_base_revision_digest"]),
            candidate_digest=candidate_digest,
            expires_at=expires_at,
        )
        try:
            Ed25519PublicKey.from_public_bytes(_decode(device.public_key)).verify(
                _decode(str(payload.get("owner_signature", ""))),
                signature_payload,
            )
        except (InvalidSignature, ValueError) as exc:
            raise PermissionError("Fleet candidate owner signature is invalid") from exc
        revision, _state, _generations = await self._configuration.get_config_current(principal_id)
        if revision.config_digest != associated["expected_base_revision_digest"]:
            raise RuntimeError("revision_conflict")
        draft = await self._configuration.create_config_draft(principal_id)
        draft = await self._configuration.replace_config_draft(
            principal_id,
            draft.draft_id,
            document,
            expected_version=draft.draft_version,
        )
        validation = await self._configuration.validate_config_draft(
            principal_id,
            draft.draft_id,
            preflight=True,
        )
        if not validation.valid:
            raise ValueError("Fleet candidate preflight failed")
        return await self._configuration.publish_config_draft(
            principal_id,
            draft.draft_id,
            expected_version=draft.draft_version,
            summary=f"Fleet rollout {rollout_id}",
        )

    def _open(self, envelope: SealedFleetEnvelope) -> Any:
        if envelope.version != "knoa-fleet-envelope-v1":
            raise ValueError("Fleet envelope version is unsupported")
        ephemeral = X25519PublicKey.from_public_bytes(_decode(envelope.ephemeral_public_key))
        shared = self._identity.configuration_private_key.exchange(ephemeral)
        aad = _canonical(envelope.associated_data)
        key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=hashlib.sha256(aad).digest(),
            info=b"knoa-fleet-candidate-v1",
        ).derive(shared)
        plaintext = ChaCha20Poly1305(key).decrypt(
            _decode(envelope.nonce),
            _decode(envelope.ciphertext),
            aad,
        )
        return json.loads(plaintext)


__all__ = [
    "FleetCandidateService",
    "SealedFleetEnvelope",
    "fleet_candidate_digest",
    "fleet_signature_payload",
    "seal_fleet_candidate",
]
