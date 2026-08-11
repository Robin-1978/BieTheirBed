"""Ed25519 device proof and revocable opaque Secure Gateway sessions."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# Ed25519 uses modern OpenSSL primitives. Relocated Conda installations may
# retain an unusable legacy-provider module path; cryptography explicitly
# supports disabling that optional provider for this case.
os.environ.setdefault("CRYPTOGRAPHY_OPENSSL_NO_LEGACY", "1")

from cryptography.exceptions import InvalidSignature  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PublicKey,
)

from pc_assistant.gateway.identity import (
    DeviceNotFoundError,
    GatewayDevice,
    GatewayIdentityRepository,
)
from pc_assistant.gateway.storage import prepare_owner_only_database
from pc_assistant.sqlite_connection import connect_sqlite
from pc_assistant.sqlite_schema import require_exact_table, require_index_columns

ChallengePurpose = Literal["pair", "authenticate"]
_MIN_CHALLENGE_TTL_SECONDS = 15
_MAX_CHALLENGE_TTL_SECONDS = 2 * 60
_MIN_SESSION_TTL_SECONDS = 60
_MAX_SESSION_TTL_SECONDS = 60 * 60


class GatewayAuthenticationRejectedError(PermissionError):
    """Return one rejection for invalid proof, challenge, session or device."""


@dataclass(frozen=True)
class GatewayChallenge:
    challenge_id: str
    purpose: ChallengePurpose
    subject_id: str
    nonce: str
    created_at: float
    expires_at: float


@dataclass(frozen=True)
class IssuedGatewaySession:
    session_id: str
    token: str
    device_id: str
    principal_id: str
    created_at: float
    expires_at: float


@dataclass(frozen=True)
class GatewaySessionIdentity:
    session_id: str
    device_id: str
    principal_id: str
    created_at: float
    expires_at: float


@dataclass(frozen=True)
class AuthenticatedGatewaySession:
    session_id: str
    device: GatewayDevice
    created_at: float
    expires_at: float


class GatewayAuthRepository:
    """Atomically consume proof challenges and persist opaque sessions."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock=time.time,
        challenge_id_factory=lambda: f"gch_{uuid.uuid4().hex}",
        session_id_factory=lambda: f"gws_{uuid.uuid4().hex}",
        nonce_factory=lambda: secrets.token_urlsafe(32),
        session_secret_factory=lambda: secrets.token_urlsafe(32),
    ) -> None:
        self._db_path = prepare_owner_only_database(
            db_path,
            label="Gateway authentication database",
        )
        self._clock = clock
        self._challenge_id_factory = challenge_id_factory
        self._session_id_factory = session_id_factory
        self._nonce_factory = nonce_factory
        self._session_secret_factory = session_secret_factory
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self._db_path, busy_timeout_ms=5000)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS gateway_auth_challenges (
                    challenge_id TEXT PRIMARY KEY,
                    purpose TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    nonce_sha256 TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    consumed_at REAL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS gateway_sessions (
                    session_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    secret_sha256 TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    revoked_at REAL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS gateway_sessions_device_idx
                ON gateway_sessions(device_id, expires_at)
                """
            )
            require_exact_table(
                connection,
                "gateway_auth_challenges",
                (
                    ("challenge_id", "TEXT", False, None, 1),
                    ("purpose", "TEXT", True, None, 0),
                    ("subject_id", "TEXT", True, None, 0),
                    ("nonce_sha256", "TEXT", True, None, 0),
                    ("created_at", "REAL", True, None, 0),
                    ("expires_at", "REAL", True, None, 0),
                    ("consumed_at", "REAL", False, None, 0),
                ),
                label="Gateway authentication challenges",
            )
            require_exact_table(
                connection,
                "gateway_sessions",
                (
                    ("session_id", "TEXT", False, None, 1),
                    ("device_id", "TEXT", True, None, 0),
                    ("principal_id", "TEXT", True, None, 0),
                    ("secret_sha256", "TEXT", True, None, 0),
                    ("created_at", "REAL", True, None, 0),
                    ("expires_at", "REAL", True, None, 0),
                    ("revoked_at", "REAL", False, None, 0),
                ),
                label="Gateway sessions",
            )
            require_index_columns(
                connection,
                "gateway_sessions_device_idx",
                ("device_id", "expires_at"),
                label="Gateway session device index",
            )

    def issue_challenge(
        self,
        purpose: ChallengePurpose,
        subject_id: str,
        *,
        ttl_seconds: int = 60,
    ) -> GatewayChallenge:
        if purpose not in {"pair", "authenticate"}:
            raise ValueError("Unknown Gateway challenge purpose")
        subject = self._identifier(subject_id, "Challenge subject")
        if not _MIN_CHALLENGE_TTL_SECONDS <= ttl_seconds <= _MAX_CHALLENGE_TTL_SECONDS:
            raise ValueError("Gateway challenge TTL must be between 15 and 120 seconds")
        challenge_id = self._identifier(
            self._challenge_id_factory(),
            "Challenge ID",
        )
        nonce = self._secret(self._nonce_factory(), "Challenge nonce")
        created_at = float(self._clock())
        expires_at = created_at + ttl_seconds
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO gateway_auth_challenges (
                    challenge_id, purpose, subject_id, nonce_sha256,
                    created_at, expires_at, consumed_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    challenge_id,
                    purpose,
                    subject,
                    self._hash(nonce),
                    created_at,
                    expires_at,
                ),
            )
        return GatewayChallenge(
            challenge_id=challenge_id,
            purpose=purpose,
            subject_id=subject,
            nonce=nonce,
            created_at=created_at,
            expires_at=expires_at,
        )

    def consume_challenge(
        self,
        challenge_id: str,
        purpose: ChallengePurpose,
        subject_id: str,
        nonce: str,
    ) -> None:
        try:
            normalized_id = self._identifier(challenge_id, "Challenge ID")
            normalized_subject = self._identifier(subject_id, "Challenge subject")
        except ValueError as exc:
            raise GatewayAuthenticationRejectedError(
                "Gateway authentication rejected"
            ) from exc
        supplied_hash = self._hash(nonce.strip())
        now = float(self._clock())
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT purpose, subject_id, nonce_sha256, expires_at, consumed_at
                FROM gateway_auth_challenges
                WHERE challenge_id = ?
                """,
                (normalized_id,),
            ).fetchone()
            expected_hash = str(row["nonce_sha256"]) if row is not None else "0" * 64
            valid = bool(
                row is not None
                and hmac.compare_digest(expected_hash, supplied_hash)
                and str(row["purpose"]) == purpose
                and str(row["subject_id"]) == normalized_subject
                and row["consumed_at"] is None
                and float(row["expires_at"]) > now
            )
            if not valid:
                raise GatewayAuthenticationRejectedError(
                    "Gateway authentication rejected"
                )
            updated = connection.execute(
                """
                UPDATE gateway_auth_challenges
                SET consumed_at = ?
                WHERE challenge_id = ? AND consumed_at IS NULL
                """,
                (now, normalized_id),
            )
            if updated.rowcount != 1:
                raise GatewayAuthenticationRejectedError(
                    "Gateway authentication rejected"
                )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def issue_session(
        self,
        device: GatewayDevice,
        *,
        ttl_seconds: int = 15 * 60,
    ) -> IssuedGatewaySession:
        if device.state != "active":
            raise GatewayAuthenticationRejectedError(
                "Gateway authentication rejected"
            )
        if not _MIN_SESSION_TTL_SECONDS <= ttl_seconds <= _MAX_SESSION_TTL_SECONDS:
            raise ValueError("Gateway session TTL must be between 60 and 3600 seconds")
        session_id = self._identifier(self._session_id_factory(), "Session ID")
        secret = self._secret(self._session_secret_factory(), "Session secret")
        created_at = float(self._clock())
        expires_at = created_at + ttl_seconds
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO gateway_sessions (
                    session_id, device_id, principal_id, secret_sha256,
                    created_at, expires_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    session_id,
                    device.device_id,
                    device.principal_id,
                    self._hash(secret),
                    created_at,
                    expires_at,
                ),
            )
        return IssuedGatewaySession(
            session_id=session_id,
            token=f"v1.{session_id}.{secret}",
            device_id=device.device_id,
            principal_id=device.principal_id,
            created_at=created_at,
            expires_at=expires_at,
        )

    def authenticate_session(self, token: str) -> GatewaySessionIdentity:
        parts = token.strip().split(".")
        if len(parts) != 3 or parts[0] != "v1":
            raise GatewayAuthenticationRejectedError(
                "Gateway authentication rejected"
            )
        try:
            session_id = self._identifier(parts[1], "Session ID")
        except ValueError as exc:
            raise GatewayAuthenticationRejectedError(
                "Gateway authentication rejected"
            ) from exc
        supplied_hash = self._hash(parts[2])
        now = float(self._clock())
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT session_id, device_id, principal_id, secret_sha256,
                       created_at, expires_at, revoked_at
                FROM gateway_sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        expected_hash = str(row["secret_sha256"]) if row is not None else "0" * 64
        valid = bool(
            row is not None
            and hmac.compare_digest(expected_hash, supplied_hash)
            and row["revoked_at"] is None
            and float(row["expires_at"]) > now
        )
        if not valid:
            raise GatewayAuthenticationRejectedError(
                "Gateway authentication rejected"
            )
        return GatewaySessionIdentity(
            session_id=str(row["session_id"]),
            device_id=str(row["device_id"]),
            principal_id=str(row["principal_id"]),
            created_at=float(row["created_at"]),
            expires_at=float(row["expires_at"]),
        )

    def revoke_session(self, session_id: str) -> bool:
        normalized = self._identifier(session_id, "Session ID")
        now = float(self._clock())
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE gateway_sessions
                SET revoked_at = ?
                WHERE session_id = ? AND revoked_at IS NULL
                """,
                (now, normalized),
            )
            return updated.rowcount == 1

    def revoke_sessions_for_device(self, device_id: str) -> int:
        normalized_device = self._identifier(device_id, "Device ID")
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE gateway_sessions
                SET revoked_at = ?
                WHERE device_id = ? AND revoked_at IS NULL
                """,
                (float(self._clock()), normalized_device),
            )
            return int(updated.rowcount)

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _identifier(value: str, label: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 128 or any(
            character.isspace() for character in normalized
        ):
            raise ValueError(f"{label} must contain 1-128 non-space characters")
        return normalized

    @staticmethod
    def _secret(value: str, label: str) -> str:
        normalized = value.strip()
        if len(normalized.encode("utf-8")) < 32 or len(normalized) > 256:
            raise ValueError(f"{label} must contain 32-256 bytes")
        return normalized


class GatewayAuthenticationService:
    """Verify private-key possession before pairing or issuing a session."""

    def __init__(
        self,
        identities: GatewayIdentityRepository,
        auth: GatewayAuthRepository,
    ) -> None:
        self._identities = identities
        self._auth = auth

    def begin_pairing(self, grant_id: str) -> GatewayChallenge:
        return self._auth.issue_challenge("pair", grant_id)

    def complete_pairing(
        self,
        *,
        grant_id: str,
        grant_secret: str,
        challenge_id: str,
        nonce: str,
        display_name: str,
        public_key: str,
        signature: str,
    ) -> GatewayDevice:
        normalized_name = " ".join(display_name.split())
        normalized_key, public_key_bytes = self._public_key(public_key)
        payload = self.pairing_payload(
            challenge_id=challenge_id,
            grant_id=grant_id,
            nonce=nonce,
            display_name=normalized_name,
            public_key=normalized_key,
        )
        self._verify(public_key_bytes, signature, payload)
        self._auth.consume_challenge(
            challenge_id,
            "pair",
            grant_id,
            nonce,
        )
        return self._identities.register_verified_device(
            grant_id,
            grant_secret,
            display_name=normalized_name,
            public_key=normalized_key,
        )

    def begin_authentication(self, device_id: str) -> GatewayChallenge:
        try:
            device = self._identities.active_device_by_id(device_id)
        except (DeviceNotFoundError, ValueError) as exc:
            raise GatewayAuthenticationRejectedError(
                "Gateway authentication rejected"
            ) from exc
        return self._auth.issue_challenge("authenticate", device.device_id)

    def complete_authentication(
        self,
        *,
        device_id: str,
        challenge_id: str,
        nonce: str,
        signature: str,
        session_ttl_seconds: int = 15 * 60,
    ) -> IssuedGatewaySession:
        try:
            device = self._identities.active_device_by_id(device_id)
        except (DeviceNotFoundError, ValueError) as exc:
            raise GatewayAuthenticationRejectedError(
                "Gateway authentication rejected"
            ) from exc
        _normalized_key, public_key_bytes = self._public_key(device.public_key)
        payload = self.authentication_payload(
            challenge_id=challenge_id,
            device_id=device.device_id,
            nonce=nonce,
        )
        self._verify(public_key_bytes, signature, payload)
        self._auth.consume_challenge(
            challenge_id,
            "authenticate",
            device.device_id,
            nonce,
        )
        return self._auth.issue_session(device, ttl_seconds=session_ttl_seconds)

    def authenticate_session(self, token: str) -> AuthenticatedGatewaySession:
        identity = self._auth.authenticate_session(token)
        try:
            device = self._identities.active_device(
                identity.principal_id,
                identity.device_id,
            )
            device = self._identities.mark_seen(device.device_id)
        except DeviceNotFoundError as exc:
            raise GatewayAuthenticationRejectedError(
                "Gateway authentication rejected"
            ) from exc
        return AuthenticatedGatewaySession(
            session_id=identity.session_id,
            device=device,
            created_at=identity.created_at,
            expires_at=identity.expires_at,
        )

    def revoke_device(self, principal_id: str, device_id: str) -> GatewayDevice:
        """Revoke a paired device and every active session issued to it."""
        device = self._identities.revoke_device(principal_id, device_id)
        self._auth.revoke_sessions_for_device(device.device_id)
        return device

    @staticmethod
    def pairing_payload(
        *,
        challenge_id: str,
        grant_id: str,
        nonce: str,
        display_name: str,
        public_key: str,
    ) -> bytes:
        return GatewayAuthenticationService._payload(
            "pair",
            challenge_id,
            grant_id,
            nonce,
            " ".join(display_name.split()),
            public_key.strip().rstrip("="),
        )

    @staticmethod
    def authentication_payload(
        *,
        challenge_id: str,
        device_id: str,
        nonce: str,
    ) -> bytes:
        return GatewayAuthenticationService._payload(
            "authenticate",
            challenge_id,
            device_id,
            nonce,
        )

    @staticmethod
    def _payload(purpose: str, *fields: str) -> bytes:
        normalized = [field.strip() for field in fields]
        if any(not field or "\n" in field or "\r" in field for field in normalized):
            raise ValueError("Gateway proof fields must be non-empty single lines")
        return ("KNOA-GATEWAY-PROOF-V1\n" + purpose + "\n" + "\n".join(normalized)).encode(
            "utf-8"
        )

    @staticmethod
    def _public_key(value: str) -> tuple[str, bytes]:
        raw = GatewayAuthenticationService._decode(value, expected_bytes=32)
        normalized = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        return normalized, raw

    @staticmethod
    def _verify(public_key: bytes, signature: str, payload: bytes) -> None:
        raw_signature = GatewayAuthenticationService._decode(
            signature,
            expected_bytes=64,
        )
        try:
            Ed25519PublicKey.from_public_bytes(public_key).verify(
                raw_signature,
                payload,
            )
        except (InvalidSignature, ValueError) as exc:
            raise GatewayAuthenticationRejectedError(
                "Gateway authentication rejected"
            ) from exc

    @staticmethod
    def _decode(value: str, *, expected_bytes: int) -> bytes:
        normalized = value.strip().rstrip("=")
        try:
            raw = base64.b64decode(
                normalized + "=" * (-len(normalized) % 4),
                altchars=b"-_",
                validate=True,
            )
        except (ValueError, base64.binascii.Error) as exc:
            raise GatewayAuthenticationRejectedError(
                "Gateway authentication rejected"
            ) from exc
        if len(raw) != expected_bytes:
            raise GatewayAuthenticationRejectedError(
                "Gateway authentication rejected"
            )
        return raw
