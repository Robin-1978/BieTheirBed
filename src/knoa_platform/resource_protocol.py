"""Authenticated Node-to-Node resource sessions carried by direct TLS or an opaque Relay."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import BaseModel, ConfigDict, Field

from knoa_platform.node_identity import NodeIdentity
from knoa_platform.relay_protocol import (
    canonical_json,
    decode_base64url,
    encode_base64url,
)


class ResourceClientHello(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["resource_client_hello"] = "resource_client_hello"
    version: Literal[1] = 1
    ticket: str = Field(min_length=100, max_length=8192)
    caller_node_id: str = Field(min_length=1, max_length=128)
    client_ephemeral_public_key: str = Field(min_length=40, max_length=64)
    client_nonce: str = Field(min_length=22, max_length=128)
    transport: Literal["relay"] = "relay"
    signature: str = Field(min_length=80, max_length=128)

    def transcript(self) -> dict[str, Any]:
        return {
            "audience": "knoa-resource-client-hello-v1",
            "version": self.version,
            "ticket": self.ticket,
            "caller_node_id": self.caller_node_id,
            "client_ephemeral_public_key": self.client_ephemeral_public_key,
            "client_nonce": self.client_nonce,
            "transport": self.transport,
        }


class ResourceServerHello(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["resource_server_hello"] = "resource_server_hello"
    version: Literal[1] = 1
    target_node_id: str = Field(min_length=1, max_length=128)
    server_ephemeral_public_key: str = Field(min_length=40, max_length=64)
    server_nonce: str = Field(min_length=22, max_length=128)
    signature: str = Field(min_length=80, max_length=128)


@dataclass(frozen=True)
class ResourceTicketClaims:
    ticket_id: str
    hub_id: str
    workspace_id: str
    invocation_id: str
    caller_node_id: str
    caller_signing_public_key: str
    target_node_id: str
    target_signing_public_key: str
    target_direct_gateway_url: str
    target_deployment_id: str
    target_materialized_digest: str
    max_deadline: float
    expires_at: float


def verify_resource_ticket(
    token: str,
    hub_signing_public_key: str,
    *,
    expected_hub_id: str,
    expected_workspace_id: str | None = None,
    expected_target_node_id: str | None = None,
    clock=time.time,
) -> ResourceTicketClaims:
    encoded, separator, signature = token.partition(".")
    if not separator:
        raise PermissionError("Resource ticket rejected")
    try:
        Ed25519PublicKey.from_public_bytes(
            decode_base64url(hub_signing_public_key)
        ).verify(decode_base64url(signature), encoded.encode("ascii"))
        payload = json.loads(decode_base64url(encoded))
    except (InvalidSignature, ValueError, json.JSONDecodeError) as exc:
        raise PermissionError("Resource ticket rejected") from exc
    if (
        payload.get("aud") != "knoa-resource-invocation-v1"
        or payload.get("protocol_version") != 1
        or payload.get("hub_id") != expected_hub_id
        or payload.get("workspace_id") != (expected_workspace_id or expected_hub_id)
        or payload.get("capability") != "model_inference"
        or not {"direct", "relay"}.issubset(
            set(payload.get("allowed_transports", ()))
        )
        or float(payload.get("expires_at", 0)) <= float(clock())
        or (
            expected_target_node_id is not None
            and payload.get("target_node_id") != expected_target_node_id
        )
    ):
        raise PermissionError("Resource ticket rejected")
    try:
        return ResourceTicketClaims(
            ticket_id=str(payload["ticket_id"]),
            hub_id=str(payload["hub_id"]),
            workspace_id=str(payload["workspace_id"]),
            invocation_id=str(payload["invocation_id"]),
            caller_node_id=str(payload["caller_node_id"]),
            caller_signing_public_key=str(payload["caller_signing_public_key"]),
            target_node_id=str(payload["target_node_id"]),
            target_signing_public_key=str(payload["target_signing_public_key"]),
            target_direct_gateway_url=str(payload.get("target_direct_gateway_url", "")),
            target_deployment_id=str(payload["target_deployment_id"]),
            target_materialized_digest=str(payload["target_materialized_digest"]),
            max_deadline=float(payload["max_deadline"]),
            expires_at=float(payload["expires_at"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PermissionError("Resource ticket rejected") from exc


@dataclass(frozen=True)
class PendingResourceClient:
    hello: ResourceClientHello
    private_key: X25519PrivateKey
    claims: ResourceTicketClaims


def create_resource_client_hello(
    identity: NodeIdentity,
    ticket: str,
    hub_signing_public_key: str,
    *,
    expected_hub_id: str,
    expected_workspace_id: str | None = None,
    clock=time.time,
) -> PendingResourceClient:
    claims = verify_resource_ticket(
        ticket,
        hub_signing_public_key,
        expected_hub_id=expected_hub_id,
        expected_workspace_id=expected_workspace_id,
        clock=clock,
    )
    if (
        claims.caller_node_id != identity.node_id
        or claims.caller_signing_public_key != identity.signing_public_key
    ):
        raise PermissionError("Resource caller identity rejected")
    private_key = X25519PrivateKey.generate()
    public_key = encode_base64url(
        private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    )
    unsigned = ResourceClientHello(
        ticket=ticket,
        caller_node_id=identity.node_id,
        client_ephemeral_public_key=public_key,
        client_nonce=encode_base64url(secrets.token_bytes(24)),
        signature="A" * 80,
    )
    hello = unsigned.model_copy(
        update={"signature": identity.sign(canonical_json(unsigned.transcript()))}
    )
    return PendingResourceClient(hello, private_key, claims)


def accept_resource_client_hello(
    hello: ResourceClientHello,
    *,
    session_id: str,
    hub_id: str,
    workspace_id: str | None = None,
    hub_signing_public_key: str,
    node_identity: NodeIdentity,
    clock=time.time,
) -> tuple[ResourceServerHello, ResourceCipherSession]:
    claims = verify_resource_ticket(
        hello.ticket,
        hub_signing_public_key,
        expected_hub_id=hub_id,
        expected_workspace_id=workspace_id,
        expected_target_node_id=node_identity.node_id,
        clock=clock,
    )
    if (
        claims.ticket_id != session_id
        or claims.caller_node_id != hello.caller_node_id
        or claims.target_signing_public_key != node_identity.signing_public_key
    ):
        raise PermissionError("Resource session identity rejected")
    try:
        Ed25519PublicKey.from_public_bytes(
            decode_base64url(claims.caller_signing_public_key)
        ).verify(decode_base64url(hello.signature), canonical_json(hello.transcript()))
        client_ephemeral = X25519PublicKey.from_public_bytes(
            decode_base64url(hello.client_ephemeral_public_key)
        )
    except (InvalidSignature, ValueError) as exc:
        raise PermissionError("Resource caller proof rejected") from exc
    server_private = X25519PrivateKey.generate()
    server_public = encode_base64url(
        server_private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    )
    server_nonce = encode_base64url(secrets.token_bytes(24))
    transcript = resource_server_hello_transcript(
        session_id=session_id,
        client_hello=hello,
        target_node_id=node_identity.node_id,
        server_ephemeral_public_key=server_public,
        server_nonce=server_nonce,
    )
    server_hello = ResourceServerHello(
        target_node_id=node_identity.node_id,
        server_ephemeral_public_key=server_public,
        server_nonce=server_nonce,
        signature=node_identity.sign(canonical_json(transcript)),
    )
    client_to_target, target_to_client = _derive_resource_keys(
        server_private.exchange(client_ephemeral),
        ticket_id=claims.ticket_id,
        client_nonce=hello.client_nonce,
        server_nonce=server_nonce,
    )
    return server_hello, ResourceCipherSession(
        session_id=session_id,
        decrypt_key=client_to_target,
        encrypt_key=target_to_client,
        decrypt_direction="caller_to_target",
        encrypt_direction="target_to_caller",
        expires_at=min(
            claims.expires_at + claims.max_deadline,
            float(clock()) + claims.max_deadline,
        ),
    )


def finish_resource_client_handshake(
    pending: PendingResourceClient,
    server_hello: ResourceServerHello,
    *,
    session_id: str,
) -> ResourceCipherSession:
    if server_hello.target_node_id != pending.claims.target_node_id:
        raise PermissionError("Resource target identity rejected")
    transcript = resource_server_hello_transcript(
        session_id=session_id,
        client_hello=pending.hello,
        target_node_id=server_hello.target_node_id,
        server_ephemeral_public_key=server_hello.server_ephemeral_public_key,
        server_nonce=server_hello.server_nonce,
    )
    try:
        Ed25519PublicKey.from_public_bytes(
            decode_base64url(pending.claims.target_signing_public_key)
        ).verify(
            decode_base64url(server_hello.signature), canonical_json(transcript)
        )
        server_ephemeral = X25519PublicKey.from_public_bytes(
            decode_base64url(server_hello.server_ephemeral_public_key)
        )
    except (InvalidSignature, ValueError) as exc:
        raise PermissionError("Resource target proof rejected") from exc
    client_to_target, target_to_client = _derive_resource_keys(
        pending.private_key.exchange(server_ephemeral),
        ticket_id=pending.claims.ticket_id,
        client_nonce=pending.hello.client_nonce,
        server_nonce=server_hello.server_nonce,
    )
    return ResourceCipherSession(
        session_id=session_id,
        decrypt_key=target_to_client,
        encrypt_key=client_to_target,
        decrypt_direction="target_to_caller",
        encrypt_direction="caller_to_target",
        expires_at=pending.claims.expires_at + pending.claims.max_deadline,
    )


def resource_server_hello_transcript(
    *,
    session_id: str,
    client_hello: ResourceClientHello,
    target_node_id: str,
    server_ephemeral_public_key: str,
    server_nonce: str,
) -> dict[str, Any]:
    return {
        "audience": "knoa-resource-server-hello-v1",
        "session_id": session_id,
        "client_hello_digest": hashlib.sha256(
            canonical_json(client_hello.model_dump(mode="json"))
        ).hexdigest(),
        "target_node_id": target_node_id,
        "server_ephemeral_public_key": server_ephemeral_public_key,
        "server_nonce": server_nonce,
    }


def _derive_resource_keys(
    shared_secret: bytes,
    *,
    ticket_id: str,
    client_nonce: str,
    server_nonce: str,
) -> tuple[bytes, bytes]:
    salt = hashlib.sha256(
        canonical_json(
            {
                "ticket_id": ticket_id,
                "client_nonce": client_nonce,
                "server_nonce": server_nonce,
            }
        )
    ).digest()
    material = HKDF(
        algorithm=hashes.SHA256(),
        length=64,
        salt=salt,
        info=b"knoa-resource-session-v1",
    ).derive(shared_secret)
    return material[:32], material[32:]


class ResourceCipherSession:
    def __init__(
        self,
        *,
        session_id: str,
        decrypt_key: bytes,
        encrypt_key: bytes,
        decrypt_direction: str,
        encrypt_direction: str,
        expires_at: float,
    ) -> None:
        self.session_id = session_id
        self.expires_at = expires_at
        self._decryptor = ChaCha20Poly1305(decrypt_key)
        self._encryptor = ChaCha20Poly1305(encrypt_key)
        self._decrypt_direction = decrypt_direction
        self._encrypt_direction = encrypt_direction
        self._receive_sequence = 0
        self._send_sequence = 0

    def decrypt(self, sequence: int, ciphertext: bytes) -> dict[str, Any]:
        if sequence != self._receive_sequence:
            raise PermissionError("Resource receive sequence rejected")
        plaintext = self._decryptor.decrypt(
            _nonce(self._decrypt_direction, sequence),
            ciphertext,
            _aad(self.session_id, self._decrypt_direction, sequence),
        )
        self._receive_sequence += 1
        value = json.loads(plaintext)
        if not isinstance(value, dict):
            raise TypeError("Resource message must be an object")
        return value

    def encrypt(self, value: dict[str, Any]) -> tuple[int, bytes]:
        sequence = self._send_sequence
        ciphertext = self._encryptor.encrypt(
            _nonce(self._encrypt_direction, sequence),
            canonical_json(value),
            _aad(self.session_id, self._encrypt_direction, sequence),
        )
        self._send_sequence += 1
        return sequence, ciphertext


def _nonce(direction: str, sequence: int) -> bytes:
    prefix = b"C2R1" if direction == "caller_to_target" else b"R2C1"
    return prefix + sequence.to_bytes(8, "big")


def _aad(session_id: str, direction: str, sequence: int) -> bytes:
    return canonical_json(
        {
            "audience": "knoa-resource-packet-v1",
            "session_id": session_id,
            "direction": direction,
            "sequence": sequence,
        }
    )


__all__ = [
    "PendingResourceClient",
    "ResourceCipherSession",
    "ResourceClientHello",
    "ResourceServerHello",
    "ResourceTicketClaims",
    "accept_resource_client_hello",
    "create_resource_client_hello",
    "finish_resource_client_handshake",
    "resource_server_hello_transcript",
    "verify_resource_ticket",
]
