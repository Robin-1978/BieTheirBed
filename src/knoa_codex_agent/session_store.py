"""Private durable bindings owned by the Codex Agent implementation."""

from __future__ import annotations

import sqlite3
import secrets
import time
from dataclasses import dataclass
from pathlib import Path


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc, traceback) -> bool:
        try:
            return super().__exit__(exc_type, exc, traceback)
        finally:
            self.close()


@dataclass(frozen=True)
class CodexSessionRecord:
    runtime_session_ref: str
    upstream_thread_ref: str | None
    operation_id: str
    binding_epoch: int
    created_at: float
    updated_at: float


class CodexSessionRepository:
    """Persist operation/thread bindings without involving Knoa Platform."""

    def __init__(self, path: str | Path, *, clock=time.time, handle_factory=None) -> None:
        self._path = Path(path).expanduser().resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._path.parent.chmod(0o700)
        self._clock = clock
        self._handle_factory = handle_factory or (lambda: secrets.token_urlsafe(24))
        with self._connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS codex_sessions (
                    runtime_session_ref TEXT PRIMARY KEY,
                    upstream_thread_ref TEXT UNIQUE,
                    operation_id TEXT NOT NULL UNIQUE,
                    binding_epoch INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS codex_turn_operations (
                    operation_id TEXT PRIMARY KEY,
                    runtime_session_ref TEXT NOT NULL
                        REFERENCES codex_sessions(runtime_session_ref)
                        ON DELETE CASCADE,
                    runtime_turn_ref TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(runtime_session_ref, runtime_turn_ref)
                );
                """
            )
            columns = {
                str(row["name"])
                for row in db.execute("PRAGMA table_info(codex_sessions)").fetchall()
            }
            if "upstream_thread_ref" not in columns:
                db.execute(
                    "ALTER TABLE codex_sessions ADD COLUMN upstream_thread_ref TEXT"
                )
                db.execute(
                    "UPDATE codex_sessions SET upstream_thread_ref=runtime_session_ref"
                )
                db.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS codex_sessions_by_upstream_thread "
                    "ON codex_sessions(upstream_thread_ref)"
                )
        self._path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._path,
            timeout=30.0,
            factory=_ClosingConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _record(row: sqlite3.Row) -> CodexSessionRecord:
        return CodexSessionRecord(
            runtime_session_ref=str(row["runtime_session_ref"]),
            upstream_thread_ref=(
                str(row["upstream_thread_ref"])
                if row["upstream_thread_ref"] is not None
                else None
            ),
            operation_id=str(row["operation_id"]),
            binding_epoch=int(row["binding_epoch"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    def find_by_operation(self, operation_id: str) -> CodexSessionRecord | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM codex_sessions WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
        return None if row is None else self._record(row)

    def get(self, runtime_session_ref: str) -> CodexSessionRecord:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM codex_sessions WHERE runtime_session_ref=?",
                (runtime_session_ref,),
            ).fetchone()
        if row is None:
            raise LookupError("Codex Agent Session not found")
        return self._record(row)

    def create(
        self,
        *,
        operation_id: str,
        binding_epoch: int,
    ) -> CodexSessionRecord:
        now = self._clock()
        for _ in range(5):
            runtime_session_ref = self._handle_factory().strip()
            if not runtime_session_ref:
                continue
            with self._connect() as db:
                try:
                    db.execute(
                        """INSERT INTO codex_sessions(
                               runtime_session_ref, upstream_thread_ref,
                               operation_id, binding_epoch, created_at, updated_at
                           ) VALUES (?, NULL, ?, ?, ?, ?)""",
                        (
                            runtime_session_ref,
                            operation_id,
                            binding_epoch,
                            now,
                            now,
                        ),
                    )
                except sqlite3.IntegrityError:
                    existing = self.find_by_operation(operation_id)
                    if existing is not None:
                        return existing
                    continue
            return self.get(runtime_session_ref)
        raise RuntimeError("Could not allocate a unique Codex Agent Session")

    def bind_upstream_thread(
        self,
        runtime_session_ref: str,
        upstream_thread_ref: str,
    ) -> CodexSessionRecord:
        normalized = upstream_thread_ref.strip()
        if not normalized:
            raise ValueError("Codex upstream Thread ID must not be empty")
        with self._connect() as db:
            updated = db.execute(
                """UPDATE codex_sessions
                   SET upstream_thread_ref=?, updated_at=?
                   WHERE runtime_session_ref=?
                     AND (upstream_thread_ref IS NULL OR upstream_thread_ref=?)""",
                (
                    normalized,
                    self._clock(),
                    runtime_session_ref,
                    normalized,
                ),
            ).rowcount
        if updated == 0:
            current = self.get(runtime_session_ref)
            if current.upstream_thread_ref != normalized:
                raise RuntimeError("Codex Agent Session is already bound to another Thread")
        return self.get(runtime_session_ref)

    def find_turn(self, operation_id: str) -> str | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT runtime_turn_ref FROM codex_turn_operations
                   WHERE operation_id=?""",
                (operation_id,),
            ).fetchone()
        return None if row is None else str(row["runtime_turn_ref"])

    def record_turn(
        self,
        *,
        operation_id: str,
        runtime_session_ref: str,
        runtime_turn_ref: str,
    ) -> str:
        with self._connect() as db:
            try:
                db.execute(
                    """INSERT INTO codex_turn_operations(
                           operation_id, runtime_session_ref,
                           runtime_turn_ref, created_at
                       ) VALUES (?, ?, ?, ?)""",
                    (
                        operation_id,
                        runtime_session_ref,
                        runtime_turn_ref,
                        self._clock(),
                    ),
                )
            except sqlite3.IntegrityError:
                existing = self.find_turn(operation_id)
                if existing is None:
                    raise
                return existing
        return runtime_turn_ref

    def delete(self, runtime_session_ref: str) -> None:
        with self._connect() as db:
            deleted = db.execute(
                "DELETE FROM codex_sessions WHERE runtime_session_ref=?",
                (runtime_session_ref,),
            ).rowcount
        if deleted == 0:
            raise LookupError("Codex Agent Session not found")
