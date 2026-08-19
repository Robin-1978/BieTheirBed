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
                CREATE TABLE IF NOT EXISTS workspace_resources(
                    resource_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    canonical_digest TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(workspace_id, resource_id)
                );
                CREATE TABLE IF NOT EXISTS deployments(
                    deployment_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    resource_generation INTEGER NOT NULL,
                    resource_digest TEXT NOT NULL,
                    target_node_id TEXT NOT NULL,
                    desired_generation INTEGER NOT NULL,
                    spec_json TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS deployments_by_workspace_kind
                    ON deployments(workspace_id, kind, target_node_id, deployment_id);
                CREATE TABLE IF NOT EXISTS work_projections(
                    workspace_id TEXT NOT NULL,
                    entity_kind TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    state TEXT NOT NULL,
                    progress REAL,
                    summary TEXT NOT NULL,
                    approval_summary TEXT NOT NULL,
                    artifact_refs_json TEXT NOT NULL,
                    source_generation INTEGER NOT NULL,
                    source_digest TEXT NOT NULL,
                    projection_seq INTEGER NOT NULL,
                    source_created_at REAL NOT NULL,
                    source_updated_at REAL NOT NULL,
                    projected_at REAL NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(workspace_id, entity_kind, entity_id)
                );
                CREATE INDEX IF NOT EXISTS work_projections_by_workspace
                    ON work_projections(workspace_id, entity_kind, source_updated_at DESC);
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
            if "direct_gateway_url" not in columns:
                db.execute(
                    "ALTER TABLE nodes ADD COLUMN direct_gateway_url TEXT NOT NULL DEFAULT ''"
                )

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
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT subject_id FROM app_installations WHERE installation_id=?",
                (installation_id,),
            ).fetchone()
            if existing is not None and existing["subject_id"] != subject_id:
                raise PermissionError("App installation belongs to another account")
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

    def record_presence(
        self,
        node_id: str,
        nonce: str,
        *,
        version: str = "",
        direct_gateway_url: str = "",
    ) -> dict:
        now = self._clock()
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT INTO presence_nonces VALUES (?, ?, ?)", (node_id, nonce, now)
                )
                db.execute(
                    """UPDATE nodes SET last_seen=?, direct_gateway_url=?,
                          version=COALESCE(NULLIF(?, ''), version)
                       WHERE node_id=? AND state='active'""",
                    (now, direct_gateway_url, version, node_id),
                )
                db.execute("DELETE FROM presence_nonces WHERE observed_at<?", (now - 600,))
        except sqlite3.IntegrityError as exc:
            raise PermissionError("Node nonce was already consumed") from exc
        return self.node(node_id)

    def consume_node_nonce(self, node_id: str, nonce: str) -> None:
        """Reject replayed Node control-plane requests without changing presence."""

        now = self._clock()
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT INTO presence_nonces VALUES (?, ?, ?)", (node_id, nonce, now)
                )
                db.execute("DELETE FROM presence_nonces WHERE observed_at<?", (now - 600,))
        except sqlite3.IntegrityError as exc:
            raise PermissionError("Node nonce was already consumed") from exc

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

    def put_workspace_resource(self, item: dict, *, created_by: str) -> dict:
        kind = str(item["kind"])
        if kind not in {"model", "mcp"}:
            raise ValueError("Workspace resource kind is invalid")
        resource_id = str(item["resource_id"])
        generation = int(item["generation"])
        if generation < 1:
            raise ValueError("Workspace resource generation is invalid")
        now = self._clock()
        with self._connect() as db:
            existing = db.execute(
                "SELECT * FROM workspace_resources WHERE workspace_id=? AND resource_id=?",
                (self.hub_id, resource_id),
            ).fetchone()
            spec_json = json.dumps(
                item.get("spec", {}), ensure_ascii=False, sort_keys=True
            )
            if existing is not None:
                if str(existing["kind"]) != kind:
                    raise ValueError("Workspace resource kind is immutable")
                current_generation = int(existing["generation"])
                if generation < current_generation:
                    raise ValueError("Workspace resource generation is stale")
                if generation == current_generation and (
                    str(existing["canonical_digest"]) != str(item["canonical_digest"])
                    or str(existing["spec_json"]) != spec_json
                ):
                    raise ValueError("Published generation is immutable")
            db.execute(
                """INSERT INTO workspace_resources(
                       resource_id, workspace_id, kind, generation, canonical_digest,
                       display_name, spec_json, enabled, created_by, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(workspace_id, resource_id) DO UPDATE SET
                     kind=excluded.kind,
                     generation=excluded.generation,
                     canonical_digest=excluded.canonical_digest,
                     display_name=excluded.display_name,
                     spec_json=excluded.spec_json,
                     enabled=excluded.enabled,
                     created_by=excluded.created_by,
                     updated_at=excluded.updated_at""",
                (
                    resource_id,
                    self.hub_id,
                    kind,
                    generation,
                    str(item["canonical_digest"]),
                    str(item["display_name"]),
                    spec_json,
                    int(bool(item.get("enabled", True))),
                    created_by,
                    now if existing is None else float(existing["created_at"]),
                    now,
                ),
            )
        return self.workspace_resource(resource_id)

    def workspace_resource(self, resource_id: str) -> dict:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM workspace_resources WHERE workspace_id=? AND resource_id=?",
                (self.hub_id, resource_id),
            ).fetchone()
        if row is None:
            raise LookupError("Workspace resource not found")
        item = dict(row)
        item["spec"] = json.loads(item.pop("spec_json"))
        item["enabled"] = bool(item["enabled"])
        return item

    def list_workspace_resources(self, *, kind: str = "") -> tuple[dict, ...]:
        values: list[object] = [self.hub_id]
        clause = "workspace_id=?"
        if kind:
            clause += " AND kind=?"
            values.append(kind)
        with self._connect() as db:
            rows = db.execute(
                f"SELECT * FROM workspace_resources WHERE {clause} "
                "ORDER BY kind, display_name, resource_id",
                tuple(values),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["spec"] = json.loads(item.pop("spec_json"))
            item["enabled"] = bool(item["enabled"])
            items.append(item)
        return tuple(items)

    def put_deployment(self, item: dict) -> dict:
        kind = str(item["kind"])
        if kind not in {"model", "mcp"}:
            raise ValueError("Deployment kind is invalid")
        resource = self.workspace_resource(str(item["resource_id"]))
        if resource["kind"] != kind:
            raise ValueError("Deployment resource kind mismatch")
        if int(item["resource_generation"]) != int(resource["generation"]):
            raise ValueError("Deployment resource generation mismatch")
        if str(item["resource_digest"]) != str(resource["canonical_digest"]):
            raise ValueError("Deployment resource digest mismatch")
        self.node(str(item["target_node_id"]))
        now = self._clock()
        with self._connect() as db:
            existing = db.execute(
                "SELECT * FROM deployments WHERE deployment_id=?",
                (str(item["deployment_id"]),),
            ).fetchone()
            desired_generation = int(item["desired_generation"])
            spec_json = json.dumps(
                item.get("spec", {}), ensure_ascii=False, sort_keys=True
            )
            if existing is not None:
                current_generation = int(existing["desired_generation"])
                if desired_generation < current_generation:
                    raise ValueError("Deployment desired generation is stale")
                if desired_generation == current_generation and any(
                    (
                        str(existing["kind"]) != kind,
                        str(existing["resource_id"]) != str(item["resource_id"]),
                        int(existing["resource_generation"])
                        != int(item["resource_generation"]),
                        str(existing["resource_digest"]) != str(item["resource_digest"]),
                        str(existing["target_node_id"]) != str(item["target_node_id"]),
                        str(existing["spec_json"]) != spec_json,
                        bool(existing["enabled"]) != bool(item.get("enabled", True)),
                    )
                ):
                    raise ValueError("Deployment generation is immutable")
            db.execute(
                """INSERT INTO deployments(
                       deployment_id, workspace_id, kind, resource_id,
                       resource_generation, resource_digest, target_node_id,
                       desired_generation, spec_json, enabled, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(deployment_id) DO UPDATE SET
                     kind=excluded.kind,
                     resource_id=excluded.resource_id,
                     resource_generation=excluded.resource_generation,
                     resource_digest=excluded.resource_digest,
                     target_node_id=excluded.target_node_id,
                     desired_generation=excluded.desired_generation,
                     spec_json=excluded.spec_json,
                     enabled=excluded.enabled,
                     updated_at=excluded.updated_at""",
                (
                    item["deployment_id"], self.hub_id, kind, item["resource_id"],
                    item["resource_generation"], item["resource_digest"],
                    item["target_node_id"], desired_generation,
                    spec_json,
                    int(bool(item.get("enabled", True))),
                    now if existing is None else float(existing["created_at"]), now,
                ),
            )
        return self.deployment(str(item["deployment_id"]))

    def deployment(self, deployment_id: str) -> dict:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM deployments WHERE deployment_id=? AND workspace_id=?",
                (deployment_id, self.hub_id),
            ).fetchone()
        if row is None:
            raise LookupError("Deployment not found")
        item = dict(row)
        item["spec"] = json.loads(item.pop("spec_json"))
        item["enabled"] = bool(item["enabled"])
        return item

    def list_deployments(self, *, kind: str = "", node_id: str = "") -> tuple[dict, ...]:
        clauses = ["workspace_id=?"]
        values: list[object] = [self.hub_id]
        if kind:
            clauses.append("kind=?")
            values.append(kind)
        if node_id:
            clauses.append("target_node_id=?")
            values.append(node_id)
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM deployments WHERE " + " AND ".join(clauses)
                + " ORDER BY kind, deployment_id",
                tuple(values),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["spec"] = json.loads(item.pop("spec_json"))
            item["enabled"] = bool(item["enabled"])
            items.append(item)
        return tuple(items)

    def put_work_projection(self, node_id: str, item: dict) -> dict:
        self.node(node_id)
        entity_kind = str(item["entity_kind"])
        if entity_kind not in {"conversation", "task"}:
            raise ValueError("Work projection kind is invalid")
        projection_seq = int(item["projection_seq"])
        now = self._clock()
        with self._connect() as db:
            existing = db.execute(
                """SELECT projection_seq FROM work_projections
                   WHERE workspace_id=? AND entity_kind=? AND entity_id=?""",
                (self.hub_id, entity_kind, str(item["entity_id"])),
            ).fetchone()
            if existing is not None and projection_seq <= int(existing["projection_seq"]):
                return self.work_projection(entity_kind, str(item["entity_id"]))
            db.execute(
                """INSERT INTO work_projections(
                       workspace_id, entity_kind, entity_id, node_id, principal_id,
                       title, state, progress, summary, approval_summary,
                       artifact_refs_json, source_generation, source_digest,
                       projection_seq, source_created_at, source_updated_at,
                       projected_at, payload_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(workspace_id, entity_kind, entity_id) DO UPDATE SET
                     node_id=excluded.node_id,
                     principal_id=excluded.principal_id,
                     title=excluded.title,
                     state=excluded.state,
                     progress=excluded.progress,
                     summary=excluded.summary,
                     approval_summary=excluded.approval_summary,
                     artifact_refs_json=excluded.artifact_refs_json,
                     source_generation=excluded.source_generation,
                     source_digest=excluded.source_digest,
                     projection_seq=excluded.projection_seq,
                     source_created_at=excluded.source_created_at,
                     source_updated_at=excluded.source_updated_at,
                     projected_at=excluded.projected_at,
                     payload_json=excluded.payload_json""",
                (
                    self.hub_id, entity_kind, item["entity_id"], node_id,
                    item.get("principal_id", ""), item.get("title", ""),
                    item["state"], item.get("progress"), item.get("summary", ""),
                    item.get("approval_summary", ""),
                    json.dumps(item.get("artifact_refs", []), ensure_ascii=False, sort_keys=True),
                    item.get("source_generation", 1), item["source_digest"], projection_seq,
                    item["source_created_at"], item["source_updated_at"], now,
                    json.dumps(item.get("payload", {}), ensure_ascii=False, sort_keys=True),
                ),
            )
        return self.work_projection(entity_kind, str(item["entity_id"]))

    def work_projection(self, entity_kind: str, entity_id: str) -> dict:
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM work_projections
                   WHERE workspace_id=? AND entity_kind=? AND entity_id=?""",
                (self.hub_id, entity_kind, entity_id),
            ).fetchone()
        if row is None:
            raise LookupError("Work projection not found")
        return self._decode_work_projection(row)

    def list_work_projections(
        self, *, entity_kind: str = "", node_id: str = "", limit: int = 200
    ) -> tuple[dict, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("Work projection limit is invalid")
        clauses = ["workspace_id=?"]
        values: list[object] = [self.hub_id]
        if entity_kind:
            clauses.append("entity_kind=?")
            values.append(entity_kind)
        if node_id:
            clauses.append("node_id=?")
            values.append(node_id)
        values.append(limit)
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM work_projections WHERE " + " AND ".join(clauses)
                + " ORDER BY source_updated_at DESC, entity_id DESC LIMIT ?",
                tuple(values),
            ).fetchall()
        return tuple(self._decode_work_projection(row) for row in rows)

    @staticmethod
    def _decode_work_projection(row: sqlite3.Row) -> dict:
        item = dict(row)
        item["artifact_refs"] = json.loads(item.pop("artifact_refs_json"))
        item["payload"] = json.loads(item.pop("payload_json"))
        return item

    def put_resource_grant(self, item: dict) -> dict:
        caller = self.node(str(item["caller_node_id"]))
        capability = str(item.get("capability", "model_inference"))
        if capability == "model_inference":
            deployment = self.deployment(str(item["target_deployment_id"]))
            if deployment["kind"] != "model":
                raise ValueError("Model grant requires a Model Deployment")
        elif capability == "mcp_invoke":
            deployment = self.deployment(str(item["target_deployment_id"]))
            if deployment["kind"] != "mcp":
                raise ValueError("MCP grant requires an MCP Deployment")
        else:
            raise ValueError("Resource grant capability is invalid")
        if caller["workspace_id"] != self.hub_id or deployment["workspace_id"] != self.hub_id:
            raise PermissionError("Resource grant crosses Workspace")
        with self._connect() as db:
            db.execute(
                """INSERT INTO resource_grants VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                   ON CONFLICT(grant_id) DO UPDATE SET
                     caller_node_id=excluded.caller_node_id,
                     target_deployment_id=excluded.target_deployment_id,
                     capability=excluded.capability,
                     max_request_deadline=excluded.max_request_deadline,
                     expires_at=excluded.expires_at,
                     revoked_at=NULL""",
                (
                    item["grant_id"], self.hub_id, item["caller_node_id"],
                    item["target_deployment_id"], capability,
                    item["max_request_deadline"],
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

    def active_resource_grant(
        self,
        caller_node_id: str,
        deployment_id: str,
        *,
        capability: str = "model_inference",
    ) -> dict:
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM resource_grants WHERE workspace_id=?
                   AND caller_node_id=? AND target_deployment_id=?
                   AND capability=? AND revoked_at IS NULL
                   AND expires_at>? ORDER BY expires_at DESC LIMIT 1""",
                (
                    self.hub_id,
                    caller_node_id,
                    deployment_id,
                    capability,
                    self._clock(),
                ),
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

    def revoke_resource_grant(self, grant_id: str) -> dict:
        now = self._clock()
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM resource_grants WHERE grant_id=? AND workspace_id=?",
                (grant_id, self.hub_id),
            ).fetchone()
            if row is None:
                raise LookupError("Resource grant not found")
            db.execute(
                "UPDATE resource_grants SET revoked_at=? WHERE grant_id=? AND workspace_id=?",
                (now, grant_id, self.hub_id),
            )
        return self.resource_grant(grant_id)

    def put_deployment_observation(self, node_id: str, item: dict) -> dict:
        deployment = self.deployment(str(item["deployment_id"]))
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
                   WHERE o.deployment_id IN (
                       SELECT deployment_id FROM deployments WHERE workspace_id=?
                   ) ORDER BY o.deployment_id""",
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
