"""Principal-owned runtime sessions and transcript persistence."""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from knoa_platform.agent_runtime.contracts import RuntimeScope
from knoa_platform.exceptions import SessionNotFoundError
from knoa_platform.sqlite_connection import connect_sqlite, initialize_wal
from knoa_platform.sqlite_schema import (
    require_exact_table,
    require_foreign_keys,
    require_index_columns,
)

_MAX_TRANSCRIPT_BYTES = 16 * 1024 * 1024


class SessionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    messages: tuple[dict[str, Any], ...] = ()


class RuntimeSessionRepository:
    """Store opaque sessions and enforce ownership on every state operation."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        handle_factory: Callable[[], str] | None = None,
    ) -> None:
        self._path = Path(db_path).expanduser().resolve()
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._path.parent.chmod(0o700)
        self._handle_factory = handle_factory or (lambda: secrets.token_urlsafe(24))
        initialize_wal(self._path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = connect_sqlite(self._path, foreign_keys=True)
        self._path.chmod(0o600)
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS runtime_sessions (
                    session_handle TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runtime_session_transcripts (
                    session_handle TEXT PRIMARY KEY
                        REFERENCES runtime_sessions(session_handle) ON DELETE CASCADE,
                    messages_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runtime_active_sessions (
                    principal_id TEXT PRIMARY KEY,
                    session_handle TEXT NOT NULL
                        REFERENCES runtime_sessions(session_handle) ON DELETE CASCADE,
                    updated_at REAL NOT NULL
                );
                """
            )
            require_exact_table(
                db,
                "runtime_sessions",
                (
                    ("session_handle", "TEXT", False, None, 1),
                    ("principal_id", "TEXT", True, None, 0),
                    ("agent_id", "TEXT", True, None, 0),
                    ("created_at", "REAL", True, None, 0),
                    ("updated_at", "REAL", True, None, 0),
                ),
                label="Runtime session",
            )
            require_exact_table(
                db,
                "runtime_session_transcripts",
                (
                    ("session_handle", "TEXT", False, None, 1),
                    ("messages_json", "TEXT", True, None, 0),
                    ("updated_at", "REAL", True, None, 0),
                ),
                label="Runtime session",
            )
            require_exact_table(
                db,
                "runtime_active_sessions",
                (
                    ("principal_id", "TEXT", False, None, 1),
                    ("session_handle", "TEXT", True, None, 0),
                    ("updated_at", "REAL", True, None, 0),
                ),
                label="Runtime session",
            )
            require_foreign_keys(
                db,
                "runtime_session_transcripts",
                (("runtime_sessions", "session_handle", "session_handle", "NO ACTION", "CASCADE"),),
                label="Runtime session transcript",
            )
            require_foreign_keys(
                db,
                "runtime_active_sessions",
                (("runtime_sessions", "session_handle", "session_handle", "NO ACTION", "CASCADE"),),
                label="Runtime active session",
            )
            db.execute(
                """CREATE INDEX IF NOT EXISTS runtime_sessions_by_principal
                   ON runtime_sessions(principal_id, updated_at DESC)"""
            )
            require_index_columns(
                db,
                "runtime_sessions_by_principal",
                ("principal_id", "updated_at"),
                label="Runtime session",
            )

    @staticmethod
    def _principal(principal_id: str) -> str:
        normalized = principal_id.strip()
        if not normalized:
            raise ValueError("principal_id must not be empty")
        return normalized

    @staticmethod
    def _owned(db: sqlite3.Connection, scope: RuntimeScope) -> None:
        row = db.execute(
            """SELECT 1 FROM runtime_sessions
               WHERE session_handle=? AND principal_id=?""",
            (scope.session_handle, scope.principal_id),
        ).fetchone()
        if row is None:
            raise SessionNotFoundError()

    def create(
        self,
        principal_id: str,
        *,
        activate: bool = True,
        agent_id: str = "knoa",
    ) -> RuntimeScope:
        principal = self._principal(principal_id)
        selected_agent = agent_id.strip()
        if not selected_agent:
            raise ValueError("agent_id must not be empty")
        for _ in range(5):
            handle = self._handle_factory().strip()
            scope = RuntimeScope(principal_id=principal, session_handle=handle)
            now = time.time()
            try:
                with self._connect() as db:
                    db.execute(
                        """INSERT INTO runtime_sessions(
                               session_handle, principal_id, agent_id,
                               created_at, updated_at
                           ) VALUES (?, ?, ?, ?, ?)""",
                        (
                            scope.session_handle,
                            scope.principal_id,
                            selected_agent,
                            now,
                            now,
                        ),
                    )
                    if activate:
                        db.execute(
                            """INSERT INTO runtime_active_sessions(
                                   principal_id, session_handle, updated_at
                               ) VALUES (?, ?, ?)
                               ON CONFLICT(principal_id) DO UPDATE SET
                                   session_handle=excluded.session_handle,
                                   updated_at=excluded.updated_at""",
                            (scope.principal_id, scope.session_handle, now),
                        )
                return scope
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("Could not allocate a unique session handle")

    def resolve(self, principal_id: str, session_handle: str) -> RuntimeScope:
        scope = RuntimeScope(
            principal_id=self._principal(principal_id),
            session_handle=session_handle,
        )
        with self._connect() as db:
            self._owned(db, scope)
        return scope

    def isolated_task_scope(
        self,
        source: RuntimeScope,
        task_key: str,
    ) -> RuntimeScope:
        """Create or resolve one stable Agent Session for an external Task identity."""

        normalized_key = task_key.strip()
        if not normalized_key or len(normalized_key) > 256:
            raise ValueError("task_key must contain 1-256 characters")
        digest = hashlib.sha256(
            f"{source.principal_id}\0{source.session_handle}\0{normalized_key}".encode()
        ).hexdigest()[:40]
        isolated = RuntimeScope(
            principal_id=source.principal_id,
            session_handle=f"task-{digest}",
        )
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._owned(db, source)
            source_row = db.execute(
                "SELECT agent_id FROM runtime_sessions WHERE session_handle=?",
                (source.session_handle,),
            ).fetchone()
            assert source_row is not None
            db.execute(
                """INSERT OR IGNORE INTO runtime_sessions(
                       session_handle, principal_id, agent_id, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?)""",
                (
                    isolated.session_handle,
                    isolated.principal_id,
                    str(source_row["agent_id"]),
                    now,
                    now,
                ),
            )
            self._owned(db, isolated)
        return isolated

    def agent_id(self, scope: RuntimeScope) -> str:
        with self._connect() as db:
            row = db.execute(
                """SELECT agent_id FROM runtime_sessions
                   WHERE session_handle=? AND principal_id=?""",
                (scope.session_handle, scope.principal_id),
            ).fetchone()
        if row is None:
            raise SessionNotFoundError()
        return str(row["agent_id"])

    def active(self, principal_id: str) -> RuntimeScope | None:
        principal = self._principal(principal_id)
        with self._connect() as db:
            row = db.execute(
                """SELECT a.session_handle
                   FROM runtime_active_sessions a
                   JOIN runtime_sessions s
                     ON s.session_handle=a.session_handle
                    AND s.principal_id=a.principal_id
                   WHERE a.principal_id=?""",
                (principal,),
            ).fetchone()
        if row is None:
            return None
        return RuntimeScope(principal_id=principal, session_handle=str(row[0]))

    def set_active(self, scope: RuntimeScope) -> None:
        now = time.time()
        with self._connect() as db:
            self._owned(db, scope)
            db.execute(
                """INSERT INTO runtime_active_sessions(
                       principal_id, session_handle, updated_at
                   ) VALUES (?, ?, ?)
                   ON CONFLICT(principal_id) DO UPDATE SET
                       session_handle=excluded.session_handle,
                       updated_at=excluded.updated_at""",
                (scope.principal_id, scope.session_handle, now),
            )

    def save(self, scope: RuntimeScope, snapshot: SessionSnapshot) -> None:
        payload = json.dumps(snapshot.messages, ensure_ascii=False, separators=(",", ":"))
        if len(payload.encode("utf-8")) > _MAX_TRANSCRIPT_BYTES:
            raise ValueError(
                f"Session transcript exceeds {_MAX_TRANSCRIPT_BYTES} byte limit"
            )
        now = time.time()
        with self._connect() as db:
            self._owned(db, scope)
            db.execute(
                """INSERT INTO runtime_session_transcripts(
                       session_handle, messages_json, updated_at
                   ) VALUES (?, ?, ?)
                   ON CONFLICT(session_handle) DO UPDATE SET
                       messages_json=excluded.messages_json,
                       updated_at=excluded.updated_at""",
                (
                    scope.session_handle,
                    payload,
                    now,
                ),
            )
            db.execute(
                "UPDATE runtime_sessions SET updated_at=? WHERE session_handle=?",
                (now, scope.session_handle),
            )

    def load(self, scope: RuntimeScope) -> SessionSnapshot:
        with self._connect() as db:
            self._owned(db, scope)
            stored_size = db.execute(
                """SELECT length(CAST(messages_json AS BLOB))
                   FROM runtime_session_transcripts WHERE session_handle=?""",
                (scope.session_handle,),
            ).fetchone()
            if stored_size is not None and int(stored_size[0]) > _MAX_TRANSCRIPT_BYTES:
                raise RuntimeError("Session transcript exceeds storage limit")
            row = db.execute(
                """SELECT messages_json FROM runtime_session_transcripts
                   WHERE session_handle=?""",
                (scope.session_handle,),
            ).fetchone()
        if row is None:
            return SessionSnapshot()
        try:
            messages = json.loads(str(row[0]))
        except json.JSONDecodeError as exc:
            raise RuntimeError("Session transcript is corrupt") from exc
        if not isinstance(messages, list):
            raise RuntimeError("Session transcript is corrupt")
        if not all(isinstance(item, dict) for item in messages):
            raise RuntimeError("Session transcript is corrupt")
        return SessionSnapshot(messages=tuple(messages))

    def delete(self, scope: RuntimeScope) -> None:
        with self._connect() as db:
            self._owned(db, scope)
            db.execute(
                "DELETE FROM runtime_sessions WHERE session_handle=?",
                (scope.session_handle,),
            )

    def list_for_principal(self, principal_id: str) -> tuple[RuntimeScope, ...]:
        principal = self._principal(principal_id)
        with self._connect() as db:
            rows = db.execute(
                """SELECT session_handle FROM runtime_sessions
                   WHERE principal_id=? ORDER BY updated_at DESC""",
                (principal,),
            ).fetchall()
        return tuple(
            RuntimeScope(principal_id=principal, session_handle=str(row[0]))
            for row in rows
        )
