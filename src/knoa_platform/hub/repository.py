"""Small single-Hub SQLite control-plane repository."""

from __future__ import annotations

import hashlib
import json
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
                CREATE TABLE IF NOT EXISTS workspaces(
                    workspace_id TEXT PRIMARY KEY,
                    identity_issuer_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workspace_memberships(
                    workspace_id TEXT NOT NULL,
                    identity_issuer_id TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(workspace_id, identity_issuer_id, subject_id)
                );
                CREATE TABLE IF NOT EXISTS model_resources(
                    resource_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    canonical_digest TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    provider_protocol TEXT NOT NULL,
                    model_identity TEXT NOT NULL,
                    declared_capabilities TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_deployments(
                    deployment_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    resource_revision INTEGER NOT NULL,
                    target_node_id TEXT NOT NULL,
                    desired_revision INTEGER NOT NULL,
                    enabled INTEGER NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS resource_grants(
                    grant_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    caller_node_id TEXT NOT NULL,
                    target_deployment_id TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    max_request_deadline REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    revoked_at REAL
                );
                CREATE TABLE IF NOT EXISTS deployment_observations(
                    deployment_id TEXT PRIMARY KEY,
                    node_id TEXT NOT NULL,
                    applied_digest TEXT NOT NULL,
                    health_epoch INTEGER NOT NULL,
                    health TEXT NOT NULL,
                    capabilities TEXT NOT NULL,
                    available_capacity INTEGER NOT NULL,
                    observed_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    node_signature TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS resource_invocation_tickets(
                    ticket_id TEXT PRIMARY KEY,
                    invocation_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    caller_node_id TEXT NOT NULL,
                    target_deployment_id TEXT NOT NULL,
                    issued_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    issuance_state TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS invocation_audit_observations(
                    invocation_id TEXT NOT NULL,
                    reporting_node_id TEXT NOT NULL,
                    reported_state TEXT NOT NULL,
                    execution_epoch TEXT NOT NULL,
                    report_seq INTEGER NOT NULL,
                    usage_summary TEXT NOT NULL,
                    node_signature TEXT NOT NULL,
                    observed_at REAL NOT NULL,
                    PRIMARY KEY(invocation_id, reporting_node_id, report_seq)
                );
                """
            )
            columns = {
                str(row["name"])
                for row in db.execute("PRAGMA table_info(nodes)").fetchall()
            }
            if "workspace_id" not in columns:
                db.execute("ALTER TABLE nodes ADD COLUMN workspace_id TEXT NOT NULL DEFAULT ''")
                db.execute("UPDATE nodes SET workspace_id=hub_id WHERE workspace_id='' ")

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.path, foreign_keys=True)

    def initialize_owner(
        self,
        subject_id: str,
        login_identity: str,
        *,
        identity_issuer_id: str | None = None,
    ) -> None:
        now = self._clock()
        issuer_id = identity_issuer_id or self.hub_id
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO account_subjects VALUES (?, ?, 'active', ?)",
                (subject_id, login_identity, now),
            )
            db.execute(
                "INSERT OR IGNORE INTO hub_memberships VALUES (?, ?, 'owner', 'active', ?)",
                (self.hub_id, subject_id, now),
            )
            db.execute(
                "INSERT OR IGNORE INTO workspaces VALUES (?, ?, ?, 'personal', 'active', ?)",
                (self.hub_id, issuer_id, "Personal Workspace", now),
            )
            db.execute(
                """INSERT OR IGNORE INTO workspace_memberships
                   VALUES (?, ?, ?, 'owner', 'active', ?)""",
                (self.hub_id, issuer_id, subject_id, now),
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
                """INSERT INTO nodes(
                     node_id, hub_id, display_name, signing_public_key,
                     signing_key_version, configuration_public_key,
                     configuration_key_version, platform, version, state,
                     created_at, last_seen, workspace_id
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, NULL, ?)""",
                (
                    node["node_id"], self.hub_id, node["display_name"],
                    node["signing_public_key"], node["signing_key_version"],
                    node["configuration_public_key"], node["configuration_key_version"],
                    node["platform"], node["version"], now, self.hub_id,
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

    def workspace(self) -> dict:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM workspaces WHERE workspace_id=? AND state='active'",
                (self.hub_id,),
            ).fetchone()
        if row is None:
            raise LookupError("Workspace not found")
        return dict(row)

    def put_model_resource(self, item: dict, *, created_by: str) -> dict:
        now = self._clock()
        with self._connect() as db:
            db.execute(
                """INSERT INTO model_resources VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(resource_id) DO UPDATE SET
                     revision=excluded.revision,
                     canonical_digest=excluded.canonical_digest,
                     display_name=excluded.display_name,
                     provider_protocol=excluded.provider_protocol,
                     model_identity=excluded.model_identity,
                     declared_capabilities=excluded.declared_capabilities,
                     created_by=excluded.created_by,
                     created_at=excluded.created_at""",
                (
                    item["resource_id"], self.hub_id, item["revision"],
                    item["canonical_digest"], item["display_name"],
                    item["provider_protocol"], item["model_identity"],
                    json.dumps(item.get("declared_capabilities", {}), sort_keys=True),
                    created_by, now,
                ),
            )
        return self.model_resource(str(item["resource_id"]))

    def model_resource(self, resource_id: str) -> dict:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM model_resources WHERE resource_id=? AND workspace_id=?",
                (resource_id, self.hub_id),
            ).fetchone()
        if row is None:
            raise LookupError("Model resource not found")
        item = dict(row)
        item["declared_capabilities"] = json.loads(item["declared_capabilities"])
        return item

    def list_model_resources(self) -> tuple[dict, ...]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM model_resources WHERE workspace_id=?
                   ORDER BY display_name, resource_id""",
                (self.hub_id,),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["declared_capabilities"] = json.loads(item["declared_capabilities"])
            items.append(item)
        return tuple(items)

    def put_model_deployment(self, item: dict) -> dict:
        resource = self.model_resource(str(item["resource_id"]))
        self.node(str(item["target_node_id"]))
        if int(item["resource_revision"]) != int(resource["revision"]):
            raise ValueError("Model resource revision mismatch")
        now = self._clock()
        with self._connect() as db:
            db.execute(
                """INSERT INTO model_deployments VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(deployment_id) DO UPDATE SET
                     resource_id=excluded.resource_id,
                     resource_revision=excluded.resource_revision,
                     target_node_id=excluded.target_node_id,
                     desired_revision=excluded.desired_revision,
                     enabled=excluded.enabled""",
                (
                    item["deployment_id"], self.hub_id, item["resource_id"],
                    item["resource_revision"], item["target_node_id"],
                    item["desired_revision"], int(bool(item.get("enabled", True))), now,
                ),
            )
        return self.model_deployment(str(item["deployment_id"]))

    def model_deployment(self, deployment_id: str) -> dict:
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM model_deployments
                   WHERE deployment_id=? AND workspace_id=?""",
                (deployment_id, self.hub_id),
            ).fetchone()
        if row is None:
            raise LookupError("Model deployment not found")
        item = dict(row)
        item["enabled"] = bool(item["enabled"])
        return item

    def list_model_deployments(self) -> tuple[dict, ...]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM model_deployments WHERE workspace_id=?
                   ORDER BY deployment_id""",
                (self.hub_id,),
            ).fetchall()
        return tuple({**dict(row), "enabled": bool(row["enabled"])} for row in rows)

    def put_resource_grant(self, item: dict) -> dict:
        caller = self.node(str(item["caller_node_id"]))
        deployment = self.model_deployment(str(item["target_deployment_id"]))
        if caller["workspace_id"] != self.hub_id or deployment["workspace_id"] != self.hub_id:
            raise PermissionError("Resource grant crosses Workspace")
        with self._connect() as db:
            db.execute(
                """INSERT INTO resource_grants VALUES (?, ?, ?, ?, 'model_inference', ?, ?, NULL)
                   ON CONFLICT(grant_id) DO UPDATE SET
                     caller_node_id=excluded.caller_node_id,
                     target_deployment_id=excluded.target_deployment_id,
                     max_request_deadline=excluded.max_request_deadline,
                     expires_at=excluded.expires_at,
                     revoked_at=NULL""",
                (
                    item["grant_id"], self.hub_id, item["caller_node_id"],
                    item["target_deployment_id"], item["max_request_deadline"],
                    item["expires_at"],
                ),
            )
        return self.resource_grant(str(item["grant_id"]))

    def resource_grant(self, grant_id: str) -> dict:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM resource_grants WHERE grant_id=? AND workspace_id=?",
                (grant_id, self.hub_id),
            ).fetchone()
        if row is None:
            raise LookupError("Resource grant not found")
        return dict(row)

    def active_resource_grant(self, caller_node_id: str, deployment_id: str) -> dict:
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM resource_grants WHERE workspace_id=?
                   AND caller_node_id=? AND target_deployment_id=?
                   AND capability='model_inference' AND revoked_at IS NULL
                   AND expires_at>? ORDER BY expires_at DESC LIMIT 1""",
                (self.hub_id, caller_node_id, deployment_id, self._clock()),
            ).fetchone()
        if row is None:
            raise PermissionError("Remote model grant rejected")
        return dict(row)

    def list_resource_grants(self) -> tuple[dict, ...]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM resource_grants WHERE workspace_id=?
                   ORDER BY target_deployment_id, caller_node_id""",
                (self.hub_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def put_deployment_observation(self, node_id: str, item: dict) -> dict:
        deployment = self.model_deployment(str(item["deployment_id"]))
        if deployment["target_node_id"] != node_id:
            raise PermissionError("Deployment observation Node mismatch")
        with self._connect() as db:
            db.execute(
                """INSERT INTO deployment_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(deployment_id) DO UPDATE SET
                     node_id=excluded.node_id,
                     applied_digest=excluded.applied_digest,
                     health_epoch=excluded.health_epoch,
                     health=excluded.health,
                     capabilities=excluded.capabilities,
                     available_capacity=excluded.available_capacity,
                     observed_at=excluded.observed_at,
                     expires_at=excluded.expires_at,
                     node_signature=excluded.node_signature""",
                (
                    item["deployment_id"], node_id, item["applied_digest"],
                    item["health_epoch"], item["health"],
                    json.dumps(item.get("capabilities", {}), sort_keys=True),
                    item["available_capacity"], item["observed_at"],
                    item["expires_at"], item["signature"],
                ),
            )
        return self.deployment_observation(str(item["deployment_id"]))

    def deployment_observation(self, deployment_id: str) -> dict:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM deployment_observations WHERE deployment_id=?",
                (deployment_id,),
            ).fetchone()
        if row is None:
            raise LookupError("Deployment observation not found")
        item = dict(row)
        item["capabilities"] = json.loads(item["capabilities"])
        return item

    def list_deployment_observations(self) -> tuple[dict, ...]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT o.* FROM deployment_observations o
                   JOIN model_deployments d ON d.deployment_id=o.deployment_id
                   WHERE d.workspace_id=? ORDER BY o.deployment_id""",
                (self.hub_id,),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["capabilities"] = json.loads(item["capabilities"])
            items.append(item)
        return tuple(items)

    def record_resource_ticket(self, payload: dict) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO resource_invocation_tickets VALUES (?, ?, ?, ?, ?, ?, ?, 'issued')""",
                (
                    payload["ticket_id"], payload["invocation_id"], self.hub_id,
                    payload["caller_node_id"], payload["target_deployment_id"],
                    payload["issued_at"], payload["expires_at"],
                ),
            )

    def record_invocation_observation(self, node_id: str, item: dict) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT OR IGNORE INTO invocation_audit_observations
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item["invocation_id"], node_id, item["reported_state"],
                    item.get("execution_epoch", ""), item["report_seq"],
                    json.dumps(item.get("usage_summary", {}), sort_keys=True),
                    item["signature"], item["observed_at"],
                ),
            )

    def list_invocation_observations(self, invocation_id: str) -> tuple[dict, ...]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM invocation_audit_observations
                   WHERE invocation_id=? ORDER BY reporting_node_id, report_seq""",
                (invocation_id,),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["usage_summary"] = json.loads(item["usage_summary"])
            items.append(item)
        return tuple(items)


__all__ = ["EnrollmentGrant", "HubRepository"]
