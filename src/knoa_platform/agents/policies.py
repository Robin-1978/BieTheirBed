"""Durable immutable invocation-policy snapshots."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from knoa_platform.agents.definitions import ResolvedInvocationPolicy
from knoa_platform.sqlite_connection import connect_sqlite, initialize_wal


class InvocationPolicyRepository:
    def __init__(self, db_path: str | Path, *, clock=time.time) -> None:
        self._path = Path(db_path).expanduser().resolve()
        self._clock = clock
        initialize_wal(self._path)
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS invocation_policy_snapshots (
                       turn_id TEXT PRIMARY KEY,
                       principal_id TEXT NOT NULL,
                       session_handle TEXT NOT NULL,
                       policy_digest TEXT NOT NULL,
                       policy_json TEXT NOT NULL,
                       created_at REAL NOT NULL
                   )"""
            )

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self._path, foreign_keys=True)

    def record(
        self,
        turn_id: str,
        principal_id: str,
        session_handle: str,
        policy: ResolvedInvocationPolicy,
    ) -> None:
        with self._connect() as db:
            try:
                db.execute(
                    """INSERT INTO invocation_policy_snapshots(
                           turn_id, principal_id, session_handle, policy_digest,
                           policy_json, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        turn_id,
                        principal_id,
                        session_handle,
                        policy.policy_digest,
                        policy.model_dump_json(),
                        self._clock(),
                    ),
                )
            except sqlite3.IntegrityError:
                existing = self.get(turn_id)
                if existing.agent_id != policy.agent_id:
                    raise RuntimeError("Invocation Agent changed for an existing Turn")

    def get(self, turn_id: str) -> ResolvedInvocationPolicy:
        with self._connect() as db:
            row = db.execute(
                """SELECT policy_json FROM invocation_policy_snapshots
                   WHERE turn_id=?""",
                (turn_id,),
            ).fetchone()
        if row is None:
            raise LookupError("Invocation policy snapshot not found")
        return ResolvedInvocationPolicy.model_validate_json(str(row["policy_json"]))
