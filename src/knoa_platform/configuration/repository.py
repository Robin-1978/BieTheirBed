"""SQLite Config Registry with immutable published revisions."""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from knoa_platform.configuration.models import (
    ConfigConflictError,
    ConfigControlState,
    ConfigDraft,
    ConfigRevision,
    ManagedConfig,
)
from knoa_platform.sqlite_connection import connect_sqlite, initialize_wal


class ConfigRegistry:
    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._path = Path(db_path).expanduser().resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._clock = clock
        self._id_factory = id_factory or (lambda: f"id-{secrets.token_urlsafe(18)}")
        initialize_wal(self._path)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS config_revisions (
                    revision_id TEXT PRIMARY KEY,
                    parent_revision_id TEXT NOT NULL,
                    document_json TEXT NOT NULL,
                    config_digest TEXT NOT NULL,
                    change_summary TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS config_drafts (
                    draft_id TEXT PRIMARY KEY,
                    base_revision_id TEXT NOT NULL,
                    document_json TEXT NOT NULL,
                    draft_version INTEGER NOT NULL,
                    updated_by TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(base_revision_id) REFERENCES config_revisions(revision_id)
                );
                CREATE TABLE IF NOT EXISTS config_control_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    desired_revision_id TEXT NOT NULL,
                    applied_revision_id TEXT NOT NULL,
                    apply_status TEXT NOT NULL,
                    apply_error_code TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(desired_revision_id) REFERENCES config_revisions(revision_id),
                    FOREIGN KEY(applied_revision_id) REFERENCES config_revisions(revision_id)
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self._path, foreign_keys=True)

    def initialize(self, document: ManagedConfig, *, actor: str) -> ConfigRevision:
        with self._connect() as db:
            row = db.execute(
                """SELECT revisions.*
                   FROM config_control_state AS state
                   JOIN config_revisions AS revisions
                     ON revisions.revision_id=state.applied_revision_id
                   WHERE state.singleton=1"""
            ).fetchone()
            if row is not None:
                stored = json.loads(str(row["document_json"]))
                if stored.get("schema_version") == document.schema_version:
                    return self._revision(row)
            revision = self._new_revision(
                document,
                parent="",
                actor=actor,
                summary=f"Initialize configuration schema v{document.schema_version}",
            )
            now = self._clock()
            db.execute("BEGIN IMMEDIATE")
            if row is not None:
                db.execute("DELETE FROM config_drafts")
                db.execute("DELETE FROM config_control_state")
                db.execute("DELETE FROM config_revisions")
            self._insert_revision(db, revision)
            db.execute(
                """INSERT INTO config_control_state(
                       singleton, desired_revision_id, applied_revision_id,
                       apply_status, apply_error_code, updated_at
                   ) VALUES (1, ?, ?, 'idle', '', ?)""",
                (revision.revision_id, revision.revision_id, now),
            )
        return revision

    def adopt(
        self,
        document: ManagedConfig,
        *,
        actor: str,
        summary: str,
    ) -> ConfigRevision:
        """Publish and apply one startup convergence revision atomically."""

        current = self.current()
        if current.document.digest == document.digest:
            return current
        revision = self._new_revision(
            document,
            parent=current.revision_id,
            actor=actor,
            summary=summary,
        )
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._insert_revision(db, revision)
            db.execute(
                """UPDATE config_control_state SET
                       desired_revision_id=?, applied_revision_id=?,
                       apply_status='idle', apply_error_code='', updated_at=?
                   WHERE singleton=1""",
                (revision.revision_id, revision.revision_id, self._clock()),
            )
        return revision

    def state(self) -> ConfigControlState:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM config_control_state WHERE singleton=1"
            ).fetchone()
        if row is None:
            raise RuntimeError("Config Registry is not initialized")
        return ConfigControlState(
            desired_revision_id=str(row["desired_revision_id"]),
            applied_revision_id=str(row["applied_revision_id"]),
            apply_status=str(row["apply_status"]),
            apply_error_code=str(row["apply_error_code"]),
            updated_at=float(row["updated_at"]),
        )

    def current(self) -> ConfigRevision:
        return self.revision(self.state().applied_revision_id)

    def desired(self) -> ConfigRevision:
        return self.revision(self.state().desired_revision_id)

    def revision(self, revision_id: str) -> ConfigRevision:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM config_revisions WHERE revision_id=?",
                (revision_id,),
            ).fetchone()
        if row is None:
            raise LookupError("Configuration revision not found")
        return self._revision(row)

    def history(self, *, limit: int = 50) -> tuple[ConfigRevision, ...]:
        if not 1 <= limit <= 200:
            raise ValueError("Configuration history limit must be between 1 and 200")
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM config_revisions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(self._revision(row) for row in rows)

    def prune_history(self, *, retain: int = 200) -> int:
        """Bound immutable history while preserving every live reference."""
        if not 2 <= retain <= 500:
            raise ValueError(
                "Configuration history retention must be between 2 and 500"
            )
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            protected = {
                str(row[0])
                for row in db.execute(
                    """SELECT desired_revision_id FROM config_control_state
                       UNION SELECT applied_revision_id FROM config_control_state
                       UNION SELECT base_revision_id FROM config_drafts"""
                ).fetchall()
            }
            protected.update(
                str(row[0])
                for row in db.execute(
                    "SELECT revision_id FROM config_revisions ORDER BY created_at DESC LIMIT ?",
                    (retain,),
                ).fetchall()
            )
            placeholders = ",".join("?" for _ in protected)
            deleted = db.execute(
                f"DELETE FROM config_revisions WHERE revision_id NOT IN ({placeholders})",
                tuple(sorted(protected)),
            )
            return int(deleted.rowcount)

    def create_draft(self, *, actor: str) -> ConfigDraft:
        current = self.desired()
        draft = ConfigDraft(
            draft_id=self._id_factory(),
            base_revision_id=current.revision_id,
            document=current.document,
            draft_version=1,
            updated_by=actor,
            updated_at=self._clock(),
        )
        with self._connect() as db:
            db.execute(
                """INSERT INTO config_drafts(
                       draft_id, base_revision_id, document_json, draft_version,
                       updated_by, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    draft.draft_id,
                    draft.base_revision_id,
                    draft.document.model_dump_json(),
                    draft.draft_version,
                    draft.updated_by,
                    draft.updated_at,
                ),
            )
        return draft

    def draft(self, draft_id: str) -> ConfigDraft:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM config_drafts WHERE draft_id=?",
                (draft_id,),
            ).fetchone()
        if row is None:
            raise LookupError("Configuration draft not found")
        return self._draft(row)

    def replace_draft(
        self,
        draft_id: str,
        document: ManagedConfig,
        *,
        expected_version: int,
        actor: str,
    ) -> ConfigDraft:
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM config_drafts WHERE draft_id=?",
                (draft_id,),
            ).fetchone()
            if row is None:
                raise LookupError("Configuration draft not found")
            if int(row["draft_version"]) != expected_version:
                raise ConfigConflictError("Configuration draft was changed elsewhere")
            version = expected_version + 1
            db.execute(
                """UPDATE config_drafts SET document_json=?, draft_version=?,
                       updated_by=?, updated_at=? WHERE draft_id=?""",
                (document.model_dump_json(), version, actor, now, draft_id),
            )
        return self.draft(draft_id)

    def publish_draft(
        self,
        draft_id: str,
        *,
        expected_version: int,
        actor: str,
        summary: str,
    ) -> ConfigRevision:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM config_drafts WHERE draft_id=?",
                (draft_id,),
            ).fetchone()
            if row is None:
                raise LookupError("Configuration draft not found")
            draft = self._draft(row)
            if draft.draft_version != expected_version:
                raise ConfigConflictError("Configuration draft was changed elsewhere")
            state = db.execute(
                "SELECT * FROM config_control_state WHERE singleton=1"
            ).fetchone()
            if state is None:
                raise RuntimeError("Config Registry is not initialized")
            if draft.base_revision_id != str(state["desired_revision_id"]):
                raise ConfigConflictError(
                    "Configuration draft is based on an old revision"
                )
            revision = self._new_revision(
                draft.document,
                parent=draft.base_revision_id,
                actor=actor,
                summary=summary,
            )
            self._insert_revision(db, revision)
            db.execute(
                """UPDATE config_control_state SET desired_revision_id=?,
                       apply_status='applying', apply_error_code='', updated_at=?
                   WHERE singleton=1""",
                (revision.revision_id, self._clock()),
            )
            db.execute("DELETE FROM config_drafts WHERE draft_id=?", (draft_id,))
        return revision

    def mark_applied(self, revision_id: str) -> ConfigControlState:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = db.execute(
                "SELECT desired_revision_id FROM config_control_state WHERE singleton=1"
            ).fetchone()
            if state is None or str(state["desired_revision_id"]) != revision_id:
                raise ConfigConflictError("Applied revision is no longer desired")
            db.execute(
                """UPDATE config_control_state SET applied_revision_id=?,
                       apply_status='idle', apply_error_code='', updated_at=?
                   WHERE singleton=1""",
                (revision_id, self._clock()),
            )
        return self.state()

    def mark_failed(self, revision_id: str, error_code: str) -> ConfigControlState:
        with self._connect() as db:
            state = db.execute(
                "SELECT desired_revision_id FROM config_control_state WHERE singleton=1"
            ).fetchone()
            if state is None or str(state["desired_revision_id"]) != revision_id:
                return self.state()
            db.execute(
                """UPDATE config_control_state SET apply_status='failed',
                       apply_error_code=?, updated_at=? WHERE singleton=1""",
                (error_code[:128], self._clock()),
            )
        return self.state()

    def rollback(
        self,
        revision_id: str,
        *,
        actor: str,
        summary: str,
        document: ManagedConfig | None = None,
    ) -> ConfigRevision:
        target = self.revision(revision_id)
        current = self.desired()
        revision = self._new_revision(
            document or target.document,
            parent=current.revision_id,
            actor=actor,
            summary=summary,
        )
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._insert_revision(db, revision)
            db.execute(
                """UPDATE config_control_state SET desired_revision_id=?,
                       apply_status='applying', apply_error_code='', updated_at=?
                   WHERE singleton=1""",
                (revision.revision_id, self._clock()),
            )
        return revision

    def _new_revision(
        self,
        document: ManagedConfig,
        *,
        parent: str,
        actor: str,
        summary: str,
    ) -> ConfigRevision:
        return ConfigRevision(
            revision_id=self._id_factory(),
            parent_revision_id=parent,
            document=document,
            config_digest=document.digest,
            change_summary=summary.strip()[:2000],
            created_by=actor,
            created_at=self._clock(),
        )

    @staticmethod
    def _insert_revision(db: sqlite3.Connection, revision: ConfigRevision) -> None:
        try:
            db.execute(
                """INSERT INTO config_revisions(
                       revision_id, parent_revision_id, document_json, config_digest,
                       change_summary, created_by, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    revision.revision_id,
                    revision.parent_revision_id,
                    revision.document.model_dump_json(),
                    revision.config_digest,
                    revision.change_summary,
                    revision.created_by,
                    revision.created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ConfigConflictError("Configuration revision already exists") from exc

    @staticmethod
    def _revision(row: sqlite3.Row) -> ConfigRevision:
        return ConfigRevision(
            revision_id=str(row["revision_id"]),
            parent_revision_id=str(row["parent_revision_id"]),
            document=ManagedConfig.model_validate_json(str(row["document_json"])),
            config_digest=str(row["config_digest"]),
            change_summary=str(row["change_summary"]),
            created_by=str(row["created_by"]),
            created_at=float(row["created_at"]),
        )

    @staticmethod
    def _draft(row: sqlite3.Row) -> ConfigDraft:
        return ConfigDraft(
            draft_id=str(row["draft_id"]),
            base_revision_id=str(row["base_revision_id"]),
            document=ManagedConfig.model_validate_json(str(row["document_json"])),
            draft_version=int(row["draft_version"]),
            updated_by=str(row["updated_by"]),
            updated_at=float(row["updated_at"]),
        )
