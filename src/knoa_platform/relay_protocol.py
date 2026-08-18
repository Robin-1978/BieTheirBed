"""End-to-end authenticated Node session protocol carried by an opaque Relay."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

os.environ.setdefault("CRYPTOGRAPHY_OPENSSL_NO_LEGACY", "1")

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

if TYPE_CHECKING:
    from knoa_platform.gateway.identity import GatewayDevice


def encode_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode_base64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


class ClientHello(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["client_hello"] = "client_hello"
    version: Literal[1] = 1
    ticket: str = Field(min_length=100, max_length=4096)
    installation_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    client_signing_public_key: str = Field(min_length=40, max_length=64)
    client_ephemeral_public_key: str = Field(min_length=40, max_length=64)
    client_nonce: str = Field(min_length=22, max_length=128)
    transport: Literal["relay"] = "relay"
    signature: str = Field(min_length=80, max_length=128)

    def transcript(self) -> dict[str, Any]:
        return {
            "audience": "knoa-node-client-hello-v1",
            "version": self.version,
            "ticket": self.ticket,
            "installation_id": self.installation_id,
            "device_id": self.device_id,
            "client_signing_public_key": self.client_signing_public_key,
            "client_ephemeral_public_key": self.client_ephemeral_public_key,
            "client_nonce": self.client_nonce,
            "transport": self.transport,
        }


class PairingClientHello(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["pairing_client_hello"] = "pairing_client_hello"
    version: Literal[1] = 1
    ticket: str = Field(min_length=100, max_length=4096)
    installation_id: str = Field(min_length=1, max_length=128)
    client_signing_public_key: str = Field(min_length=40, max_length=64)
    client_ephemeral_public_key: str = Field(min_length=40, max_length=64)
    client_nonce: str = Field(min_length=22, max_length=128)
    transport: Literal["relay"] = "relay"
    signature: str = Field(min_length=80, max_length=128)

    def transcript(self) -> dict[str, Any]:
        return {
            "audience": "knoa-node-pairing-client-hello-v1",
            "version": self.version,
            "ticket": self.ticket,
            "installation_id": self.installation_id,
            "client_signing_public_key": self.client_signing_public_key,
            "client_ephemeral_public_key": self.client_ephemeral_public_key,
            "client_nonce": self.client_nonce,
            "transport": self.transport,
        }


class ServerHello(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["server_hello"] = "server_hello"
    version: Literal[1] = 1
    node_id: str = Field(min_length=1, max_length=128)
    server_ephemeral_public_key: str = Field(min_length=40, max_length=64)
    server_nonce: str = Field(min_length=22, max_length=128)
    signature: str = Field(min_length=80, max_length=128)


@dataclass(frozen=True)
class TicketClaims:
    ticket_id: str
    hub_id: str
    node_id: str
    installation_id: str
    installation_key_digest: str
    expires_at: float
    max_session_lifetime: int
    scope: str


def verify_ticket(
    token: str,
    hub_signing_public_key: str,
    *,
    expected_hub_id: str,
    expected_node_id: str,
    clock=time.time,
) -> TicketClaims:
    encoded, separator, signature = token.partition(".")
    if not separator:
        raise PermissionError("Relay ticket rejected")
    try:
        Ed25519PublicKey.from_public_bytes(
            decode_base64url(hub_signing_public_key)
        ).verify(decode_base64url(signature), encoded.encode("ascii"))
        payload = json.loads(decode_base64url(encoded))
    except (InvalidSignature, ValueError, json.JSONDecodeError) as exc:
        raise PermissionError("Relay ticket rejected") from exc
    if (
        payload.get("aud") != "knoa-node-session-v1"
        or payload.get("protocol_version") != 1
        or payload.get("transport") != "relay"
        or payload.get("hub_id") != expected_hub_id
        or payload.get("node_id") != expected_node_id
        or float(payload.get("expires_at", 0)) <= float(clock())
    ):
        raise PermissionError("Relay ticket rejected")
    try:
        return TicketClaims(
            ticket_id=str(payload["ticket_id"]),
            hub_id=str(payload["hub_id"]),
            node_id=str(payload["node_id"]),
            installation_id=str(payload["installation_id"]),
            installation_key_digest=str(payload["installation_key_digest"]),
            expires_at=float(payload["expires_at"]),
            max_session_lifetime=int(payload["max_session_lifetime"]),
            scope=str(payload["scope"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PermissionError("Relay ticket rejected") from exc


def accept_client_hello(
    hello: ClientHello,
    *,
    session_id: str,
    hub_id: str,
    hub_signing_public_key: str,
    node_identity: NodeIdentity,
    device: GatewayDevice,
    clock=time.time,
) -> tuple[ServerHello, NodeCipherSession]:
    claims = verify_ticket(
        hello.ticket,
        hub_signing_public_key,
        expected_hub_id=hub_id,
        expected_node_id=node_identity.node_id,
        clock=clock,
    )
    if (
        claims.scope != "session"
        or claims.ticket_id != session_id
        or claims.installation_id != hello.installation_id
        or hello.device_id != device.device_id
        or hello.client_signing_public_key != device.public_key
        or claims.installation_key_digest
        != hashlib.sha256(device.public_key.encode("utf-8")).hexdigest()
    ):
        raise PermissionError("Relay client identity rejected")
    try:
        Ed25519PublicKey.from_public_bytes(
            decode_base64url(hello.client_signing_public_key)
        ).verify(decode_base64url(hello.signature), canonical_json(hello.transcript()))
        client_ephemeral = X25519PublicKey.from_public_bytes(
            decode_base64url(hello.client_ephemeral_public_key)
        )
    except (InvalidSignature, ValueError) as exc:
        raise PermissionError("Relay client proof rejected") from exc

    server_private = X25519PrivateKey.generate()
    server_public = encode_base64url(
        server_private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    )
    server_nonce = encode_base64url(__import__("secrets").token_bytes(24))
    transcript = server_hello_transcript(
        session_id=session_id,
        client_hello=hello,
        node_id=node_identity.node_id,
        server_ephemeral_public_key=server_public,
        server_nonce=server_nonce,
    )
    server_hello = ServerHello(
        node_id=node_identity.node_id,
        server_ephemeral_public_key=server_public,
        server_nonce=server_nonce,
        signature=node_identity.sign(canonical_json(transcript)),
    )
    client_to_node, node_to_client = derive_session_keys(
        server_private.exchange(client_ephemeral),
        ticket_id=claims.ticket_id,
        client_nonce=hello.client_nonce,
        server_nonce=server_nonce,
    )
    return server_hello, NodeCipherSession(
        session_id=session_id,
        decrypt_key=client_to_node,
        encrypt_key=node_to_client,
        expires_at=min(
            claims.expires_at + claims.max_session_lifetime,
            float(clock()) + claims.max_session_lifetime,
        ),
    )


def accept_pairing_client_hello(
    hello: PairingClientHello,
    *,
    session_id: str,
    hub_id: str,
    hub_signing_public_key: str,
    node_identity: NodeIdentity,
    clock=time.time,
) -> tuple[ServerHello, NodeCipherSession]:
    claims = verify_ticket(
        hello.ticket,
        hub_signing_public_key,
        expected_hub_id=hub_id,
        expected_node_id=node_identity.node_id,
        clock=clock,
    )
    if (
        claims.scope != "pairing"
        or claims.ticket_id != session_id
        or claims.installation_id != hello.installation_id
        or claims.installation_key_digest
        != hashlib.sha256(hello.client_signing_public_key.encode("utf-8")).hexdigest()
    ):
        raise PermissionError("Relay pairing identity rejected")
    try:
        Ed25519PublicKey.from_public_bytes(
            decode_base64url(hello.client_signing_public_key)
        ).verify(decode_base64url(hello.signature), canonical_json(hello.transcript()))
        client_ephemeral = X25519PublicKey.from_public_bytes(
            decode_base64url(hello.client_ephemeral_public_key)
        )
    except (InvalidSignature, ValueError) as exc:
        raise PermissionError("Relay pairing proof rejected") from exc

    server_private = X25519PrivateKey.generate()
    server_public = encode_base64url(
        server_private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    )
    server_nonce = encode_base64url(__import__("secrets").token_bytes(24))
    transcript = server_hello_transcript(
        session_id=session_id,
        client_hello=hello,
        node_id=node_identity.node_id,
        server_ephemeral_public_key=server_public,
        server_nonce=server_nonce,
    )
    server_hello = ServerHello(
        node_id=node_identity.node_id,
        server_ephemeral_public_key=server_public,
        server_nonce=server_nonce,
        signature=node_identity.sign(canonical_json(transcript)),
    )
    client_to_node, node_to_client = derive_session_keys(
        server_private.exchange(client_ephemeral),
        ticket_id=claims.ticket_id,
        client_nonce=hello.client_nonce,
        server_nonce=server_nonce,
    )
    return server_hello, NodeCipherSession(
        session_id=session_id,
        decrypt_key=client_to_node,
        encrypt_key=node_to_client,
        expires_at=min(
            claims.expires_at + claims.max_session_lifetime,
            float(clock()) + claims.max_session_lifetime,
        ),
    )


def server_hello_transcript(
    *,
    session_id: str,
    client_hello: ClientHello | PairingClientHello,
    node_id: str,
    server_ephemeral_public_key: str,
    server_nonce: str,
) -> dict[str, Any]:
    return {
        "audience": "knoa-node-server-hello-v1",
        "session_id": session_id,
        "client_hello_digest": hashlib.sha256(
            canonical_json(client_hello.model_dump(mode="json"))
        ).hexdigest(),
        "node_id": node_id,
        "server_ephemeral_public_key": server_ephemeral_public_key,
        "server_nonce": server_nonce,
    }


def derive_session_keys(
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
        info=b"knoa-node-session-v1",
    ).derive(shared_secret)
    return material[:32], material[32:]


class NodeCipherSession:
    def __init__(
        self,
        *,
        session_id: str,
        decrypt_key: bytes,
        encrypt_key: bytes,
        expires_at: float,
    ) -> None:
        self.session_id = session_id
        self.expires_at = expires_at
        self._decryptor = ChaCha20Poly1305(decrypt_key)
        self._encryptor = ChaCha20Poly1305(encrypt_key)
        self._receive_sequence = 0
        self._send_sequence = 0

    def decrypt(self, sequence: int, ciphertext: bytes) -> dict[str, Any]:
        if sequence != self._receive_sequence:
            raise PermissionError("Relay receive sequence rejected")
        plaintext = self._decryptor.decrypt(
            _nonce(b"C2N1", sequence),
            ciphertext,
            _aad(self.session_id, "client_to_node", sequence),
        )
        self._receive_sequence += 1
        value = json.loads(plaintext)
        if not isinstance(value, dict):
            raise TypeError("Relay message must be an object")
        return value

    def encrypt(self, value: dict[str, Any]) -> tuple[int, bytes]:
        sequence = self._send_sequence
        ciphertext = self._encryptor.encrypt(
            _nonce(b"N2C1", sequence),
            canonical_json(value),
            _aad(self.session_id, "node_to_client", sequence),
        )
        self._send_sequence += 1
        return sequence, ciphertext


def _nonce(prefix: bytes, sequence: int) -> bytes:
    return prefix + sequence.to_bytes(8, "big")


def _aad(session_id: str, direction: str, sequence: int) -> bytes:
    return canonical_json(
        {
            "audience": "knoa-node-packet-v1",
            "session_id": session_id,
            "direction": direction,
            "sequence": sequence,
        }
    )


__all__ = [
    "ClientHello",
    "NodeCipherSession",
    "PairingClientHello",
    "ServerHello",
    "accept_client_hello",
    "accept_pairing_client_hello",
    "canonical_json",
    "decode_base64url",
    "derive_session_keys",
    "encode_base64url",
    "server_hello_transcript",
    "verify_ticket",
]
