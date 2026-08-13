"""Private persistent Session and checkpoint storage owned by Knoa Agent."""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_MAX_CHECKPOINT_BYTES = 8 * 1024 * 1024


class ContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class KnoaAgentSession(ContextModel):
    runtime_session_ref: NonEmpty
    operation_id: NonEmpty
    state_version: NonEmpty
    created_at: float = Field(ge=0.0)
    updated_at: float = Field(ge=0.0)
    revision: int = Field(ge=1)


class ContextCheckpoint(ContextModel):
    runtime_session_ref: NonEmpty
    state_version: NonEmpty
    source_cursor: int = Field(ge=0)
    agent_config_digest: NonEmpty
    model_context_digest: NonEmpty
    payload: dict[str, Any] = Field(default_factory=dict)
    revision: int = Field(ge=1)
    created_at: float = Field(ge=0.0)
    updated_at: float = Field(ge=0.0)


class ContextCheckpointConflictError(RuntimeError):
    """A stale Turn attempted to overwrite a newer Agent checkpoint."""


class ContextCheckpointRepository:
    """SQLite implementation private to Knoa Agent, never Platform-owned."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        session_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = Path(db_path).expanduser().resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._path.parent.chmod(0o700)
        self._session_id_factory = session_id_factory or (
            lambda: secrets.token_urlsafe(24)
        )
        self._clock = clock
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        self._path.chmod(0o600)
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_sessions (
                    runtime_session_ref TEXT PRIMARY KEY,
                    operation_id TEXT NOT NULL UNIQUE,
                    state_version TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    revision INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS context_checkpoints (
                    runtime_session_ref TEXT PRIMARY KEY
                        REFERENCES agent_sessions(runtime_session_ref)
                        ON DELETE CASCADE,
                    state_version TEXT NOT NULL,
                    source_cursor INTEGER NOT NULL,
                    agent_config_digest TEXT NOT NULL,
                    model_context_digest TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )

    def create_session(
        self,
        *,
        operation_id: str,
        state_version: str,
    ) -> KnoaAgentSession:
        normalized_operation = operation_id.strip()
        normalized_version = state_version.strip()
        if not normalized_operation or not normalized_version:
            raise ValueError("operation_id and state_version must not be empty")
        with self._connect() as db:
            existing = db.execute(
                "SELECT * FROM agent_sessions WHERE operation_id=?",
                (normalized_operation,),
            ).fetchone()
        if existing is not None:
            return KnoaAgentSession.model_validate(dict(existing))
        for _ in range(5):
            runtime_session_ref = self._session_id_factory().strip()
            if not runtime_session_ref:
                continue
            now = self._clock()
            try:
                with self._connect() as db:
                    db.execute(
                        """INSERT INTO agent_sessions(
                               runtime_session_ref, operation_id, state_version,
                               created_at, updated_at, revision
                           ) VALUES (?, ?, ?, ?, ?, 1)""",
                        (
                            runtime_session_ref,
                            normalized_operation,
                            normalized_version,
                            now,
                            now,
                        ),
                    )
                return KnoaAgentSession(
                    runtime_session_ref=runtime_session_ref,
                    operation_id=normalized_operation,
                    state_version=normalized_version,
                    created_at=now,
                    updated_at=now,
                    revision=1,
                )
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("Could not allocate a unique Knoa Agent Session")

    def get_session(self, runtime_session_ref: str) -> KnoaAgentSession:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM agent_sessions WHERE runtime_session_ref=?",
                (runtime_session_ref,),
            ).fetchone()
        if row is None:
            raise LookupError("Knoa Agent Session not found")
        return KnoaAgentSession.model_validate(dict(row))

    def load_checkpoint(self, runtime_session_ref: str) -> ContextCheckpoint | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM context_checkpoints WHERE runtime_session_ref=?",
                (runtime_session_ref,),
            ).fetchone()
        if row is None:
            self.get_session(runtime_session_ref)
            return None
        values = dict(row)
        payload_json = str(values.pop("payload_json"))
        if len(payload_json.encode("utf-8")) > _MAX_CHECKPOINT_BYTES:
            raise RuntimeError("Knoa Agent checkpoint exceeds storage limit")
        payload = json.loads(payload_json)
        if not isinstance(payload, dict):
            raise RuntimeError("Knoa Agent checkpoint payload is invalid")
        return ContextCheckpoint.model_validate({**values, "payload": payload})

    def save_checkpoint(
        self,
        checkpoint: ContextCheckpoint,
        *,
        expected_revision: int | None,
    ) -> ContextCheckpoint:
        payload_json = json.dumps(
            checkpoint.payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(payload_json.encode("utf-8")) > _MAX_CHECKPOINT_BYTES:
            raise ValueError("Knoa Agent checkpoint exceeds storage limit")
        with self._connect() as db:
            session = db.execute(
                "SELECT state_version FROM agent_sessions WHERE runtime_session_ref=?",
                (checkpoint.runtime_session_ref,),
            ).fetchone()
            if session is None:
                raise LookupError("Knoa Agent Session not found")
            if str(session["state_version"]) != checkpoint.state_version:
                raise ValueError("Knoa Agent checkpoint state version mismatch")
            current = db.execute(
                """SELECT revision, created_at FROM context_checkpoints
                   WHERE runtime_session_ref=?""",
                (checkpoint.runtime_session_ref,),
            ).fetchone()
            actual_revision = int(current["revision"]) if current is not None else None
            if actual_revision != expected_revision:
                raise ContextCheckpointConflictError(
                    "Knoa Agent checkpoint revision changed"
                )
            now = self._clock()
            revision = (actual_revision or 0) + 1
            created_at = float(current["created_at"]) if current is not None else now
            db.execute(
                """INSERT INTO context_checkpoints(
                       runtime_session_ref, state_version, source_cursor,
                       agent_config_digest, model_context_digest, payload_json,
                       revision, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(runtime_session_ref) DO UPDATE SET
                       state_version=excluded.state_version,
                       source_cursor=excluded.source_cursor,
                       agent_config_digest=excluded.agent_config_digest,
                       model_context_digest=excluded.model_context_digest,
                       payload_json=excluded.payload_json,
                       revision=excluded.revision,
                       updated_at=excluded.updated_at""",
                (
                    checkpoint.runtime_session_ref,
                    checkpoint.state_version,
                    checkpoint.source_cursor,
                    checkpoint.agent_config_digest,
                    checkpoint.model_context_digest,
                    payload_json,
                    revision,
                    created_at,
                    now,
                ),
            )
            db.execute(
                """UPDATE agent_sessions
                   SET updated_at=?, revision=revision+1
                   WHERE runtime_session_ref=?""",
                (now, checkpoint.runtime_session_ref),
            )
        return checkpoint.model_copy(
            update={
                "revision": revision,
                "created_at": created_at,
                "updated_at": now,
            }
        )

    def delete_session(self, runtime_session_ref: str) -> None:
        with self._connect() as db:
            deleted = db.execute(
                "DELETE FROM agent_sessions WHERE runtime_session_ref=?",
                (runtime_session_ref,),
            ).rowcount
        if deleted == 0:
            raise LookupError("Knoa Agent Session not found")
