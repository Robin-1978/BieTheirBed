"""Platform-owned opaque binding between Product Session and Agent Session."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from knoa_agent_contracts import RuntimeSession
from knoa_platform.agent_runtime.contracts import RuntimeScope
from knoa_platform.sqlite_connection import connect_sqlite, initialize_wal


class AgentSessionBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    session_handle: str
    principal_id: str
    agent_id: str
    agent_config_digest: str
    runtime_session_ref: str
    runtime_protocol_version: str
    binding_epoch: int = Field(ge=1)
    state: str
    created_at: float = Field(ge=0.0)
    updated_at: float = Field(ge=0.0)
    revision: int = Field(ge=1)

    def runtime_session(self) -> RuntimeSession:
        return RuntimeSession(
            agent_id=self.agent_id,
            runtime_session_ref=self.runtime_session_ref,
            runtime_protocol_version=self.runtime_protocol_version,
            binding_epoch=self.binding_epoch,
        )


class AgentSessionBindingRepository:
    def __init__(self, db_path: str | Path, *, clock=time.time) -> None:
        self._path = Path(db_path).expanduser().resolve()
        self._clock = clock
        initialize_wal(self._path)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_session_bindings (
                    session_handle TEXT PRIMARY KEY
                        REFERENCES runtime_sessions(session_handle) ON DELETE CASCADE,
                    principal_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    agent_config_digest TEXT NOT NULL,
                    runtime_session_ref TEXT NOT NULL,
                    runtime_protocol_version TEXT NOT NULL,
                    binding_epoch INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    revision INTEGER NOT NULL,
                    UNIQUE(agent_id, runtime_session_ref)
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self._path, foreign_keys=True)

    def get(self, scope: RuntimeScope) -> AgentSessionBinding | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM agent_session_bindings
                   WHERE session_handle=? AND principal_id=?""",
                (scope.session_handle, scope.principal_id),
            ).fetchone()
        return AgentSessionBinding.model_validate(dict(row)) if row is not None else None

    def create(
        self,
        scope: RuntimeScope,
        session: RuntimeSession,
        *,
        agent_config_digest: str,
    ) -> AgentSessionBinding:
        now = self._clock()
        with self._connect() as db:
            try:
                db.execute(
                    """INSERT INTO agent_session_bindings(
                           session_handle, principal_id, agent_id,
                           agent_config_digest, runtime_session_ref,
                           runtime_protocol_version, binding_epoch, state,
                           created_at, updated_at, revision
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?, 1)""",
                    (
                        scope.session_handle,
                        scope.principal_id,
                        session.agent_id,
                        agent_config_digest,
                        session.runtime_session_ref,
                        session.runtime_protocol_version,
                        session.binding_epoch,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                existing = self.get(scope)
                if existing is None:
                    raise
                return existing
        created = self.get(scope)
        if created is None:
            raise RuntimeError("Agent Session binding was not persisted")
        return created

    def delete(self, scope: RuntimeScope) -> None:
        with self._connect() as db:
            db.execute(
                """DELETE FROM agent_session_bindings
                   WHERE session_handle=? AND principal_id=?""",
                (scope.session_handle, scope.principal_id),
            )
