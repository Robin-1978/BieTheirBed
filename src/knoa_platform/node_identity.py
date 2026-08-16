"""Stable Node identity with separate signing and configuration encryption keys."""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _private_bytes(key: Ed25519PrivateKey | X25519PrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )


def _public_bytes(key) -> bytes:
    return key.public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


@dataclass(frozen=True)
class NodeIdentity:
    node_id: str
    signing_private_key: Ed25519PrivateKey
    configuration_private_key: X25519PrivateKey
    signing_key_version: int
    configuration_key_version: int
    created_at: float

    @property
    def signing_public_key(self) -> str:
        return _encode(_public_bytes(self.signing_private_key.public_key()))

    @property
    def configuration_public_key(self) -> str:
        return _encode(_public_bytes(self.configuration_private_key.public_key()))

    def descriptor(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "signing_public_key": self.signing_public_key,
            "signing_key_version": self.signing_key_version,
            "configuration_public_key": self.configuration_public_key,
            "configuration_key_version": self.configuration_key_version,
            "created_at": self.created_at,
        }

    def sign(self, payload: bytes) -> str:
        return _encode(self.signing_private_key.sign(payload))


class NodeIdentityStore:
    """Create once and persist owner-only raw Node identity material."""

    def __init__(self, path: str | Path, *, clock=time.time) -> None:
        self._path = Path(path).expanduser().resolve()
        self._clock = clock

    def load_or_create(self) -> NodeIdentity:
        if self._path.exists():
            return self._load()
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        identity = NodeIdentity(
            node_id=f"node_{secrets.token_urlsafe(18)}",
            signing_private_key=Ed25519PrivateKey.generate(),
            configuration_private_key=X25519PrivateKey.generate(),
            signing_key_version=1,
            configuration_key_version=1,
            created_at=float(self._clock()),
        )
        payload = {
            **identity.descriptor(),
            "signing_private_key": _encode(_private_bytes(identity.signing_private_key)),
            "configuration_private_key": _encode(
                _private_bytes(identity.configuration_private_key)
            ),
        }
        temporary = self._path.with_name(f".{self._path.name}.{secrets.token_hex(8)}.tmp")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, self._path)
            except FileExistsError:
                return self._load()
            directory = os.open(self._path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)
        return identity

    def _load(self) -> NodeIdentity:
        if self._path.is_symlink() or not self._path.is_file():
            raise ValueError("Node identity must be a regular file")
        if self._path.stat().st_mode & 0o077:
            raise PermissionError("Node identity must use mode 0600")
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        identity = NodeIdentity(
            node_id=str(raw["node_id"]),
            signing_private_key=Ed25519PrivateKey.from_private_bytes(
                _decode(str(raw["signing_private_key"]))
            ),
            configuration_private_key=X25519PrivateKey.from_private_bytes(
                _decode(str(raw["configuration_private_key"]))
            ),
            signing_key_version=int(raw["signing_key_version"]),
            configuration_key_version=int(raw["configuration_key_version"]),
            created_at=float(raw["created_at"]),
        )
        if raw.get("signing_public_key") != identity.signing_public_key:
            raise ValueError("Node signing identity is inconsistent")
        if raw.get("configuration_public_key") != identity.configuration_public_key:
            raise ValueError("Node configuration identity is inconsistent")
        return identity


__all__ = ["NodeIdentity", "NodeIdentityStore"]
