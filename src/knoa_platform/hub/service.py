"""Account, Node enrollment, ticket and opaque Fleet control contracts."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from knoa_platform.hub.repository import HubRepository


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class HubService:
    def __init__(
        self,
        repository: HubRepository,
        identity_path: str | Path,
        *,
        owner_token: str,
        owner_subject_id: str = "subject_owner",
        clock=time.time,
    ) -> None:
        if len(owner_token) < 32:
            raise ValueError("Hub owner token must contain at least 32 characters")
        self.repository = repository
        self.hub_id = repository.hub_id
        self.owner_subject_id = owner_subject_id
        self._owner_token_hash = hashlib.sha256(owner_token.encode()).digest()
        self._clock = clock
        self._signing_key = self._load_or_create_key(Path(identity_path))
        repository.initialize_owner(owner_subject_id, "bootstrap-owner")

    @property
    def signing_public_key(self) -> str:
        return _encode(
            self._signing_key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
        )

    def authenticate_owner(self, token: str) -> str:
        supplied = hashlib.sha256(token.encode()).digest()
        if not secrets.compare_digest(supplied, self._owner_token_hash):
            raise PermissionError("Hub account authentication rejected")
        return self.owner_subject_id

    def enroll_node(self, request: dict) -> dict:
        grant = self.repository.enrollment(str(request["grant_id"]), str(request["grant_secret"]))
        transcript = {
            "audience": "knoa-node-enrollment-v1",
            "hub_id": self.hub_id,
            "grant_id": request["grant_id"],
            "challenge": grant["challenge"],
            "node_id": request["node_id"],
            "signing_public_key": request["signing_public_key"],
            "signing_key_version": request["signing_key_version"],
            "configuration_public_key": request["configuration_public_key"],
            "configuration_key_version": request["configuration_key_version"],
        }
        try:
            Ed25519PublicKey.from_public_bytes(_decode(str(request["signing_public_key"]))).verify(
                _decode(str(request["signature"])), _canonical(transcript)
            )
        except (InvalidSignature, ValueError, KeyError) as exc:
            raise PermissionError("Node enrollment signature rejected") from exc
        return self.repository.consume_enrollment(str(request["grant_id"]), request)

    def record_presence(self, request: dict) -> dict:
        node = self.repository.node(str(request["node_id"]))
        timestamp = float(request["timestamp"])
        if abs(self._clock() - timestamp) > 120:
            raise PermissionError("Node presence timestamp rejected")
        transcript = {
            "audience": "knoa-node-presence-v1",
            "hub_id": self.hub_id,
            "node_id": node["node_id"],
            "timestamp": timestamp,
            "nonce": request["nonce"],
        }
        try:
            Ed25519PublicKey.from_public_bytes(_decode(node["signing_public_key"])).verify(
                _decode(str(request["signature"])), _canonical(transcript)
            )
        except (InvalidSignature, ValueError) as exc:
            raise PermissionError("Node presence signature rejected") from exc
        return self.repository.record_presence(node["node_id"], str(request["nonce"]))

    def issue_ticket(self, installation_id: str, node_id: str, transport: str) -> str:
        installation = self.repository.installation(installation_id)
        self.repository.node(node_id)
        if transport not in {"direct", "relay"}:
            raise ValueError("Connection transport is invalid")
        now = self._clock()
        payload = {
            "aud": "knoa-node-session-v1",
            "hub_id": self.hub_id,
            "node_id": node_id,
            "installation_id": installation_id,
            "installation_key_digest": hashlib.sha256(
                str(installation["public_key"]).encode()
            ).hexdigest(),
            "ticket_id": f"tkt_{secrets.token_urlsafe(18)}",
            "issued_at": now,
            "expires_at": now + 90,
            "transport": transport,
            "protocol_version": 1,
            "max_session_lifetime": 3600,
        }
        encoded = _encode(_canonical(payload))
        signature = _encode(self._signing_key.sign(encoded.encode()))
        self.repository.create_ticket(
            payload["ticket_id"], node_id, installation_id, payload["expires_at"]
        )
        return f"{encoded}.{signature}"

    def verify_and_consume_ticket(self, token: str) -> dict:
        encoded, separator, signature = token.partition(".")
        if not separator:
            raise PermissionError("Connection ticket rejected")
        try:
            self._signing_key.public_key().verify(_decode(signature), encoded.encode())
            payload = json.loads(_decode(encoded))
        except (InvalidSignature, ValueError, json.JSONDecodeError) as exc:
            raise PermissionError("Connection ticket rejected") from exc
        if payload.get("aud") != "knoa-node-session-v1" or payload.get("hub_id") != self.hub_id:
            raise PermissionError("Connection ticket rejected")
        self.repository.consume_ticket(
            str(payload["ticket_id"]), str(payload["node_id"]), str(payload["installation_id"])
        )
        return payload

    @staticmethod
    def _load_or_create_key(path: Path) -> Ed25519PrivateKey:
        path = path.expanduser().resolve()
        def load_existing() -> Ed25519PrivateKey:
            if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
                raise PermissionError("Hub identity must be a mode 0600 regular file")
            return Ed25519PrivateKey.from_private_bytes(_decode(path.read_text().strip()))

        if path.exists():
            return load_existing()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        key = Ed25519PrivateKey.generate()
        raw = key.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return load_existing()
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(_encode(raw))
            stream.flush()
            os.fsync(stream.fileno())
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return key


__all__ = ["HubService"]
