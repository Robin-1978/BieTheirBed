"""Gateway-owned, secret-free device security audit journal."""
from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from knoa_platform.gateway.storage import prepare_owner_only_database
from knoa_platform.sqlite_connection import connect_sqlite
from knoa_platform.sqlite_schema import require_exact_table, require_index_columns


@dataclass(frozen=True)
class GatewayAuditEvent:
    event_id: int
    device_id: str
    principal_id: str
    event_type: str
    occurred_at: float
    remote_address_hash: str
    detail_code: str


class GatewayAuditRepository:
    """Persist bounded security metadata without credentials or user content."""

    def __init__(self, db_path: str | Path, *, clock=time.time) -> None:
        self._db_path = prepare_owner_only_database(
            db_path,
            label="Gateway audit database",
        )
        self._clock = clock
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self._db_path, busy_timeout_ms=5000)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS gateway_device_audit (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at REAL NOT NULL,
                    remote_address_hash TEXT NOT NULL,
                    detail_code TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS gateway_device_audit_device_idx
                ON gateway_device_audit(principal_id, device_id, event_id DESC)
                """
            )
            require_exact_table(
                connection,
                "gateway_device_audit",
                (
                    ("event_id", "INTEGER", False, None, 1),
                    ("device_id", "TEXT", True, None, 0),
                    ("principal_id", "TEXT", True, None, 0),
                    ("event_type", "TEXT", True, None, 0),
                    ("occurred_at", "REAL", True, None, 0),
                    ("remote_address_hash", "TEXT", True, None, 0),
                    ("detail_code", "TEXT", True, None, 0),
                ),
                label="Gateway device audit",
            )
            require_index_columns(
                connection,
                "gateway_device_audit_device_idx",
                ("principal_id", "device_id", "event_id"),
                label="Gateway device audit index",
            )

    def append(
        self,
        event_type: str,
        *,
        device_id: str = "",
        principal_id: str = "",
        remote_address: str = "",
        detail_code: str = "",
    ) -> GatewayAuditEvent:
        event = self._text(event_type, "event_type", 64)
        device = self._text(device_id, "device_id", 128, allow_empty=True)
        principal = self._text(
            principal_id,
            "principal_id",
            256,
            allow_empty=True,
        )
        detail = self._text(detail_code, "detail_code", 256, allow_empty=True)
        address_hash = self.hash_remote_address(remote_address)
        occurred_at = float(self._clock())
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO gateway_device_audit (
                    device_id, principal_id, event_type, occurred_at,
                    remote_address_hash, detail_code
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (device, principal, event, occurred_at, address_hash, detail),
            )
            event_id = int(cursor.lastrowid)
        return GatewayAuditEvent(
            event_id=event_id,
            device_id=device,
            principal_id=principal,
            event_type=event,
            occurred_at=occurred_at,
            remote_address_hash=address_hash,
            detail_code=detail,
        )

    def list_for_device(
        self,
        principal_id: str,
        device_id: str,
        *,
        after_id: int = 0,
        limit: int = 100,
    ) -> tuple[GatewayAuditEvent, ...]:
        principal = self._text(principal_id, "principal_id", 256)
        device = self._text(device_id, "device_id", 128)
        if after_id < 0 or not 1 <= limit <= 200:
            raise ValueError("Invalid Gateway audit pagination")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_id, device_id, principal_id, event_type,
                       occurred_at, remote_address_hash, detail_code
                FROM gateway_device_audit
                WHERE principal_id = ? AND device_id = ? AND event_id > ?
                ORDER BY event_id ASC LIMIT ?
                """,
                (principal, device, after_id, limit),
            ).fetchall()
        return tuple(self._event(row) for row in rows)

    @staticmethod
    def hash_remote_address(value: str) -> str:
        normalized = value.strip()
        if not normalized:
            return ""
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _text(
        value: str,
        label: str,
        limit: int,
        *,
        allow_empty: bool = False,
    ) -> str:
        normalized = value.strip()
        if (not normalized and not allow_empty) or len(normalized) > limit:
            raise ValueError(f"{label} is invalid")
        return normalized

    @staticmethod
    def _event(row: sqlite3.Row) -> GatewayAuditEvent:
        return GatewayAuditEvent(
            event_id=int(row["event_id"]),
            device_id=str(row["device_id"]),
            principal_id=str(row["principal_id"]),
            event_type=str(row["event_type"]),
            occurred_at=float(row["occurred_at"]),
            remote_address_hash=str(row["remote_address_hash"]),
            detail_code=str(row["detail_code"]),
        )
