"""Small single-Hub SQLite control-plane repository."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from knoa_platform.sqlite_connection import connect_sqlite, initialize_wal


@dataclass(frozen=True)
class EnrollmentGrant:
    grant_id: str
    secret: str
    challenge: str
    expires_at: float


class HubRepository:
    def __init__(self, path: str | Path, *, hub_id: str, clock=time.time) -> None:
        self.path = Path(path).expanduser().resolve()
        self.hub_id = hub_id
        self._clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        initialize_wal(self.path)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS account_subjects(
                    subject_id TEXT PRIMARY KEY, login_identity TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS hub_memberships(
                    hub_id TEXT NOT NULL, subject_id TEXT NOT NULL, role TEXT NOT NULL,
                    state TEXT NOT NULL, created_at REAL NOT NULL,
                    PRIMARY KEY(hub_id, subject_id)
                );
                CREATE TABLE IF NOT EXISTS app_installations(
                    installation_id TEXT PRIMARY KEY, hub_id TEXT NOT NULL,
                    subject_id TEXT NOT NULL, public_key TEXT NOT NULL,
                    display_name TEXT NOT NULL, state TEXT NOT NULL,
                    created_at REAL NOT NULL, last_seen REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS node_enrollment_grants(
                    grant_id TEXT PRIMARY KEY, secret_sha256 TEXT NOT NULL,
                    challenge TEXT NOT NULL, expires_at REAL NOT NULL,
                    consumed_at REAL
                );
                CREATE TABLE IF NOT EXISTS nodes(
                    node_id TEXT PRIMARY KEY, hub_id TEXT NOT NULL,
                    display_name TEXT NOT NULL, signing_public_key TEXT NOT NULL,
                    signing_key_version INTEGER NOT NULL,
                    configuration_public_key TEXT NOT NULL,
                    configuration_key_version INTEGER NOT NULL,
                    platform TEXT NOT NULL, version TEXT NOT NULL,
                    state TEXT NOT NULL, created_at REAL NOT NULL, last_seen REAL
                );
                CREATE TABLE IF NOT EXISTS connection_tickets(
                    ticket_id TEXT PRIMARY KEY, node_id TEXT NOT NULL,
                    installation_id TEXT NOT NULL, expires_at REAL NOT NULL,
                    consumed_at REAL
                );
                CREATE TABLE IF NOT EXISTS presence_nonces(
                    node_id TEXT NOT NULL, nonce TEXT NOT NULL, observed_at REAL NOT NULL,
                    PRIMARY KEY(node_id, nonce)
                );
                CREATE TABLE IF NOT EXISTS fleet_rollouts(
                    rollout_id TEXT PRIMARY KEY, hub_id TEXT NOT NULL,
                    installation_id TEXT NOT NULL, state TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fleet_envelopes(
                    rollout_id TEXT NOT NULL, node_id TEXT NOT NULL,
                    expected_base_revision_digest TEXT NOT NULL,
                    candidate_digest TEXT NOT NULL, sealed_candidate TEXT NOT NULL,
                    expires_at REAL NOT NULL, state TEXT NOT NULL,
                    result_code TEXT NOT NULL, updated_at REAL NOT NULL,
                    PRIMARY KEY(rollout_id, node_id)
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.path, foreign_keys=True)

    def initialize_owner(self, subject_id: str, login_identity: str) -> None:
        now = self._clock()
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO account_subjects VALUES (?, ?, 'active', ?)",
                (subject_id, login_identity, now),
            )
            db.execute(
                "INSERT OR IGNORE INTO hub_memberships VALUES (?, ?, 'owner', 'active', ?)",
                (self.hub_id, subject_id, now),
            )

    def register_installation(
        self, subject_id: str, installation_id: str, public_key: str, display_name: str
    ) -> dict:
        now = self._clock()
        with self._connect() as db:
            db.execute(
                """INSERT INTO app_installations VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
                   ON CONFLICT(installation_id) DO UPDATE SET
                     public_key=excluded.public_key, display_name=excluded.display_name,
                     state='active', last_seen=excluded.last_seen""",
                (installation_id, self.hub_id, subject_id, public_key, display_name, now, now),
            )
        return self.installation(installation_id)

    def installation(self, installation_id: str) -> dict:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM app_installations WHERE installation_id=? AND state='active'",
                (installation_id,),
            ).fetchone()
        if row is None:
            raise LookupError("App installation not found")
        return dict(row)

    def create_enrollment_grant(self, ttl_seconds: int = 600) -> EnrollmentGrant:
        if not 60 <= ttl_seconds <= 3600:
            raise ValueError("Enrollment TTL must be between 60 and 3600 seconds")
        grant = EnrollmentGrant(
            grant_id=f"neg_{secrets.token_urlsafe(18)}",
            secret=secrets.token_urlsafe(32),
            challenge=secrets.token_urlsafe(32),
            expires_at=self._clock() + ttl_seconds,
        )
        with self._connect() as db:
            db.execute(
                "INSERT INTO node_enrollment_grants VALUES (?, ?, ?, ?, NULL)",
                (
                    grant.grant_id,
                    hashlib.sha256(grant.secret.encode()).hexdigest(),
                    grant.challenge,
                    grant.expires_at,
                ),
            )
        return grant

    def enrollment(self, grant_id: str, secret: str) -> dict:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM node_enrollment_grants WHERE grant_id=?",
                (grant_id,),
            ).fetchone()
        supplied = hashlib.sha256(secret.encode()).hexdigest()
        if (
            row is None
            or not secrets.compare_digest(str(row["secret_sha256"]), supplied)
            or row["consumed_at"] is not None
            or float(row["expires_at"]) <= self._clock()
        ):
            raise PermissionError("Node enrollment rejected")
        return dict(row)

    def consume_enrollment(self, grant_id: str, node: dict) -> dict:
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            updated = db.execute(
                """UPDATE node_enrollment_grants SET consumed_at=?
                   WHERE grant_id=? AND consumed_at IS NULL AND expires_at>?""",
                (now, grant_id, now),
            )
            if updated.rowcount != 1:
                raise PermissionError("Node enrollment rejected")
            db.execute(
                """INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, NULL)""",
                (
                    node["node_id"], self.hub_id, node["display_name"],
                    node["signing_public_key"], node["signing_key_version"],
                    node["configuration_public_key"], node["configuration_key_version"],
                    node["platform"], node["version"], now,
                ),
            )
        return self.node(node["node_id"])

    def node(self, node_id: str) -> dict:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM nodes WHERE node_id=? AND state='active'", (node_id,)
            ).fetchone()
        if row is None:
            raise LookupError("Node not found")
        return dict(row)

    def list_nodes(self) -> tuple[dict, ...]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM nodes WHERE hub_id=? ORDER BY display_name, node_id",
                (self.hub_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def record_presence(self, node_id: str, nonce: str) -> dict:
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO presence_nonces VALUES (?, ?, ?)", (node_id, nonce, now)
            )
            db.execute("UPDATE nodes SET last_seen=? WHERE node_id=? AND state='active'", (now, node_id))
            db.execute("DELETE FROM presence_nonces WHERE observed_at<?", (now - 600,))
        return self.node(node_id)

    def create_ticket(self, ticket_id: str, node_id: str, installation_id: str, expires_at: float) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO connection_tickets VALUES (?, ?, ?, ?, NULL)",
                (ticket_id, node_id, installation_id, expires_at),
            )

    def consume_ticket(self, ticket_id: str, node_id: str, installation_id: str) -> None:
        now = self._clock()
        with self._connect() as db:
            updated = db.execute(
                """UPDATE connection_tickets SET consumed_at=? WHERE ticket_id=?
                   AND node_id=? AND installation_id=? AND consumed_at IS NULL AND expires_at>?""",
                (now, ticket_id, node_id, installation_id, now),
            )
        if updated.rowcount != 1:
            raise PermissionError("Connection ticket rejected")

    def put_rollout(self, rollout_id: str, installation_id: str, envelopes: tuple[dict, ...]) -> None:
        now = self._clock()
        for item in envelopes:
            self.node(str(item["node_id"]))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO fleet_rollouts VALUES (?, ?, ?, 'pending', ?)",
                (rollout_id, self.hub_id, installation_id, now),
            )
            for item in envelopes:
                db.execute(
                    """INSERT INTO fleet_envelopes VALUES (?, ?, ?, ?, ?, ?, 'pending', '', ?)""",
                    (
                        rollout_id, item["node_id"], item["expected_base_revision_digest"],
                        item["candidate_digest"], item["sealed_candidate"],
                        item["expires_at"], now,
                    ),
                )

    def pending_envelopes(self, node_id: str) -> tuple[dict, ...]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM fleet_envelopes WHERE node_id=? AND state='pending'
                   AND expires_at>? ORDER BY updated_at""",
                (node_id, self._clock()),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def report_envelope(self, rollout_id: str, node_id: str, state: str, result_code: str) -> None:
        if state not in {"applied", "failed", "skipped"}:
            raise ValueError("Fleet result state is invalid")
        with self._connect() as db:
            updated = db.execute(
                """UPDATE fleet_envelopes SET state=?, result_code=?, updated_at=?
                   WHERE rollout_id=? AND node_id=?""",
                (state, result_code[:128], self._clock(), rollout_id, node_id),
            )
        if updated.rowcount != 1:
            raise LookupError("Fleet envelope not found")


__all__ = ["EnrollmentGrant", "HubRepository"]
