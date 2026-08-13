"""Owner-only device identity persistence for the future Secure Gateway."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from knoa_platform.gateway.storage import prepare_owner_only_database
from knoa_platform.sqlite_connection import connect_sqlite
from knoa_platform.sqlite_schema import (
    require_exact_table,
    require_index_columns,
)

DeviceState = Literal["active", "revoked"]
_MIN_PAIRING_TTL_SECONDS = 30
_MAX_PAIRING_TTL_SECONDS = 15 * 60


class PairingGrantRejectedError(PermissionError):
    """Reject absent, wrong, expired and consumed grants without an oracle."""


class DeviceAlreadyPairedError(ValueError):
    """Raised when one public key is already bound to a device identity."""


class DeviceNotFoundError(LookupError):
    """Raised when a device is absent from the requested principal scope."""


@dataclass(frozen=True)
class PairingGrant:
    grant_id: str
    secret: str
    principal_id: str
    created_at: float
    expires_at: float


@dataclass(frozen=True)
class GatewayDevice:
    device_id: str
    principal_id: str
    display_name: str
    public_key: str
    state: DeviceState
    created_at: float
    last_seen_at: float | None
    revoked_at: float | None


class GatewayIdentityRepository:
    """Persist pairing grants and revocable device identities atomically."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock=time.time,
        grant_id_factory=lambda: f"pgr_{uuid.uuid4().hex}",
        device_id_factory=lambda: f"dev_{uuid.uuid4().hex}",
        secret_factory=lambda: secrets.token_urlsafe(32),
    ) -> None:
        self._db_path = prepare_owner_only_database(
            db_path,
            label="Gateway identity database",
        )
        self._clock = clock
        self._grant_id_factory = grant_id_factory
        self._device_id_factory = device_id_factory
        self._secret_factory = secret_factory
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self._db_path, busy_timeout_ms=5000)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS gateway_pairing_grants (
                    grant_id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    secret_sha256 TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    consumed_at REAL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS gateway_devices (
                    device_id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    public_key TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_seen_at REAL,
                    revoked_at REAL
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS
                    gateway_devices_public_key_uidx
                ON gateway_devices(public_key)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS gateway_devices_principal_idx
                ON gateway_devices(principal_id, created_at)
                """
            )
            require_exact_table(
                connection,
                "gateway_pairing_grants",
                (
                    ("grant_id", "TEXT", False, None, 1),
                    ("principal_id", "TEXT", True, None, 0),
                    ("secret_sha256", "TEXT", True, None, 0),
                    ("created_at", "REAL", True, None, 0),
                    ("expires_at", "REAL", True, None, 0),
                    ("consumed_at", "REAL", False, None, 0),
                ),
                label="Gateway pairing grants",
            )
            require_exact_table(
                connection,
                "gateway_devices",
                (
                    ("device_id", "TEXT", False, None, 1),
                    ("principal_id", "TEXT", True, None, 0),
                    ("display_name", "TEXT", True, None, 0),
                    ("public_key", "TEXT", True, None, 0),
                    ("state", "TEXT", True, None, 0),
                    ("created_at", "REAL", True, None, 0),
                    ("last_seen_at", "REAL", False, None, 0),
                    ("revoked_at", "REAL", False, None, 0),
                ),
                label="Gateway devices",
            )
            require_index_columns(
                connection,
                "gateway_devices_public_key_uidx",
                ("public_key",),
                label="Gateway device public key index",
            )
            require_index_columns(
                connection,
                "gateway_devices_principal_idx",
                ("principal_id", "created_at"),
                label="Gateway device principal index",
            )

    def create_pairing_grant(
        self,
        principal_id: str,
        *,
        ttl_seconds: int = 5 * 60,
    ) -> PairingGrant:
        principal = self._principal(principal_id)
        if not _MIN_PAIRING_TTL_SECONDS <= ttl_seconds <= _MAX_PAIRING_TTL_SECONDS:
            raise ValueError("Pairing grant TTL must be between 30 and 900 seconds")
        grant_id = self._identifier(self._grant_id_factory(), "Pairing grant ID")
        secret = self._secret_factory().strip()
        if len(secret.encode("utf-8")) < 32 or len(secret) > 256:
            raise ValueError("Pairing secret must contain 32-256 bytes")
        created_at = float(self._clock())
        expires_at = created_at + ttl_seconds
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO gateway_pairing_grants (
                    grant_id, principal_id, secret_sha256,
                    created_at, expires_at, consumed_at
                ) VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (
                    grant_id,
                    principal,
                    self._secret_hash(secret),
                    created_at,
                    expires_at,
                ),
            )
        return PairingGrant(
            grant_id=grant_id,
            secret=secret,
            principal_id=principal,
            created_at=created_at,
            expires_at=expires_at,
        )

    def register_verified_device(
        self,
        grant_id: str,
        secret: str,
        *,
        display_name: str,
        public_key: str,
    ) -> GatewayDevice:
        """Consume a grant after the Gateway verifies private-key possession."""
        normalized_grant = self._identifier(grant_id, "Pairing grant ID")
        normalized_name = self._display_name(display_name)
        normalized_key = self._public_key(public_key)
        supplied_hash = self._secret_hash(secret.strip())
        now = float(self._clock())
        device_id = ""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT principal_id, secret_sha256, expires_at, consumed_at
                FROM gateway_pairing_grants
                WHERE grant_id = ?
                """,
                (normalized_grant,),
            ).fetchone()
            expected_hash = str(row["secret_sha256"]) if row is not None else "0" * 64
            valid_secret = hmac.compare_digest(expected_hash, supplied_hash)
            valid_state = bool(
                row is not None
                and row["consumed_at"] is None
                and float(row["expires_at"]) > now
            )
            if not (valid_secret and valid_state):
                raise PairingGrantRejectedError("Pairing grant rejected")
            principal_id = str(row["principal_id"])
            existing = connection.execute(
                """
                SELECT device_id, principal_id, display_name, public_key,
                       state, created_at, last_seen_at, revoked_at
                FROM gateway_devices
                WHERE public_key = ?
                """,
                (normalized_key,),
            ).fetchone()
            if existing is not None:
                if str(existing["principal_id"]) != principal_id:
                    raise DeviceAlreadyPairedError(
                        "Device public key is already paired"
                    )
                device_id = str(existing["device_id"])
                connection.execute(
                    """
                    UPDATE gateway_devices
                    SET display_name = ?, state = 'active', revoked_at = NULL
                    WHERE device_id = ?
                    """,
                    (normalized_name, device_id),
                )
            else:
                device_id = self._identifier(self._device_id_factory(), "Device ID")
                try:
                    connection.execute(
                        """
                        INSERT INTO gateway_devices (
                            device_id, principal_id, display_name, public_key,
                            state, created_at, last_seen_at, revoked_at
                        ) VALUES (?, ?, ?, ?, 'active', ?, NULL, NULL)
                        """,
                        (
                            device_id,
                            principal_id,
                            normalized_name,
                            normalized_key,
                            now,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise DeviceAlreadyPairedError(
                        "Device public key is already paired"
                    ) from exc
            updated = connection.execute(
                """
                UPDATE gateway_pairing_grants
                SET consumed_at = ?
                WHERE grant_id = ? AND consumed_at IS NULL
                """,
                (now, normalized_grant),
            )
            if updated.rowcount != 1:
                raise PairingGrantRejectedError("Pairing grant rejected")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.active_device(principal_id, device_id)

    def list_devices(self, principal_id: str) -> tuple[GatewayDevice, ...]:
        principal = self._principal(principal_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT device_id, principal_id, display_name, public_key,
                       state, created_at, last_seen_at, revoked_at
                FROM gateway_devices
                WHERE principal_id = ?
                ORDER BY created_at DESC, device_id DESC
                """,
                (principal,),
            ).fetchall()
        return tuple(self._device(row) for row in rows)

    def active_device(self, principal_id: str, device_id: str) -> GatewayDevice:
        principal = self._principal(principal_id)
        normalized_device = self._identifier(device_id, "Device ID")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT device_id, principal_id, display_name, public_key,
                       state, created_at, last_seen_at, revoked_at
                FROM gateway_devices
                WHERE principal_id = ? AND device_id = ? AND state = 'active'
                """,
                (principal, normalized_device),
            ).fetchone()
        if row is None:
            raise DeviceNotFoundError("Active device not found")
        return self._device(row)

    def active_device_by_id(self, device_id: str) -> GatewayDevice:
        normalized_device = self._identifier(device_id, "Device ID")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT device_id, principal_id, display_name, public_key,
                       state, created_at, last_seen_at, revoked_at
                FROM gateway_devices
                WHERE device_id = ? AND state = 'active'
                """,
                (normalized_device,),
            ).fetchone()
        if row is None:
            raise DeviceNotFoundError("Active device not found")
        return self._device(row)

    def mark_seen(self, device_id: str) -> GatewayDevice:
        device = self.active_device_by_id(device_id)
        now = float(self._clock())
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE gateway_devices
                SET last_seen_at = ?
                WHERE device_id = ? AND state = 'active'
                """,
                (now, device.device_id),
            )
            if updated.rowcount != 1:
                raise DeviceNotFoundError("Active device not found")
        return GatewayDevice(
            device_id=device.device_id,
            principal_id=device.principal_id,
            display_name=device.display_name,
            public_key=device.public_key,
            state=device.state,
            created_at=device.created_at,
            last_seen_at=now,
            revoked_at=device.revoked_at,
        )

    def revoke_device(self, principal_id: str, device_id: str) -> GatewayDevice:
        principal = self._principal(principal_id)
        normalized_device = self._identifier(device_id, "Device ID")
        now = float(self._clock())
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT device_id, principal_id, display_name, public_key,
                       state, created_at, last_seen_at, revoked_at
                FROM gateway_devices
                WHERE principal_id = ? AND device_id = ?
                """,
                (principal, normalized_device),
            ).fetchone()
            if row is None:
                raise DeviceNotFoundError("Device not found")
            if str(row["state"]) == "active":
                connection.execute(
                    """
                    UPDATE gateway_devices
                    SET state = 'revoked', revoked_at = ?
                    WHERE principal_id = ? AND device_id = ? AND state = 'active'
                    """,
                    (now, principal, normalized_device),
                )
                row = dict(row)
                row["state"] = "revoked"
                row["revoked_at"] = now
        return self._device(row)

    @staticmethod
    def _secret_hash(secret: str) -> str:
        return hashlib.sha256(secret.encode("utf-8")).hexdigest()

    @staticmethod
    def _principal(value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 256:
            raise ValueError("Principal ID must contain 1-256 characters")
        return normalized

    @staticmethod
    def _identifier(value: str, label: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 128 or any(
            character.isspace() for character in normalized
        ):
            raise ValueError(f"{label} must contain 1-128 non-space characters")
        return normalized

    @staticmethod
    def _display_name(value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized or len(normalized) > 80:
            raise ValueError("Device name must contain 1-80 characters")
        return normalized

    @staticmethod
    def _public_key(value: str) -> str:
        normalized = value.strip().rstrip("=")
        try:
            raw = base64.b64decode(
                normalized + "=" * (-len(normalized) % 4),
                altchars=b"-_",
                validate=True,
            )
        except (ValueError, base64.binascii.Error) as exc:
            raise ValueError("Device public key must use URL-safe base64") from exc
        if len(raw) != 32:
            raise ValueError("Device public key must contain 32 bytes")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _device(row: sqlite3.Row | dict) -> GatewayDevice:
        state = str(row["state"])
        if state not in {"active", "revoked"}:
            raise RuntimeError("Gateway device state is corrupt")
        return GatewayDevice(
            device_id=str(row["device_id"]),
            principal_id=str(row["principal_id"]),
            display_name=str(row["display_name"]),
            public_key=str(row["public_key"]),
            state=state,
            created_at=float(row["created_at"]),
            last_seen_at=(
                None if row["last_seen_at"] is None else float(row["last_seen_at"])
            ),
            revoked_at=(
                None if row["revoked_at"] is None else float(row["revoked_at"])
            ),
        )
