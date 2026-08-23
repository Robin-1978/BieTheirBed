"""Durable, non-authoritative projection for the Event Source facade."""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from knoa_platform.sqlite_connection import connect_sqlite, initialize_wal


@dataclass(frozen=True)
class EventSourceProjection:
    source_id: str
    principal_id: str
    task_id: str
    trigger_id: str
    kind: str
    route_id: str
    public_url: str
    secret_version: int
    created_at: float
    updated_at: float


class EventSourceRepository:
    """Keep friendly route metadata; Task/Trigger remain execution authorities."""

    def __init__(self, path: str | Path, *, clock=time.time) -> None:
        self.path = Path(path)
        self._clock = clock
        initialize_wal(self.path)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS event_source_projections(
                    source_id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    trigger_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    route_id TEXT NOT NULL,
                    public_url TEXT NOT NULL,
                    secret_version INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(principal_id, task_id)
                );
                CREATE INDEX IF NOT EXISTS event_sources_by_principal
                    ON event_source_projections(principal_id, updated_at DESC);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.path)

    @staticmethod
    def _row(row: sqlite3.Row) -> EventSourceProjection:
        return EventSourceProjection(**dict(row))

    def put(
        self,
        principal_id: str,
        task_id: str,
        trigger_id: str,
        *,
        kind: str,
        route_id: str = "",
        public_url: str = "",
        secret_version: int = 0,
    ) -> EventSourceProjection:
        now = self._clock()
        with self._connect() as db:
            db.execute(
                """INSERT INTO event_source_projections VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(source_id) DO UPDATE SET
                     trigger_id=excluded.trigger_id, kind=excluded.kind,
                     route_id=excluded.route_id, public_url=excluded.public_url,
                     secret_version=excluded.secret_version, updated_at=excluded.updated_at""",
                (task_id, principal_id, task_id, trigger_id, kind, route_id,
                 public_url, secret_version, now, now),
            )
        return self.get(principal_id, task_id)

    def get(self, principal_id: str, source_id: str) -> EventSourceProjection:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM event_source_projections WHERE source_id=? AND principal_id=?",
                (source_id, principal_id),
            ).fetchone()
        if row is None:
            raise LookupError("Event source not found")
        return self._row(row)

    def list(self, principal_id: str, *, limit: int = 100) -> tuple[EventSourceProjection, ...]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM event_source_projections WHERE principal_id=?
                   ORDER BY updated_at DESC LIMIT ?""",
                (principal_id, max(1, min(limit, 200))),
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def update_secret_version(self, principal_id: str, source_id: str, version: int) -> EventSourceProjection:
        with self._connect() as db:
            cursor = db.execute(
                """UPDATE event_source_projections SET secret_version=?, updated_at=?
                   WHERE source_id=? AND principal_id=?""",
                (version, self._clock(), source_id, principal_id),
            )
        if cursor.rowcount != 1:
            raise LookupError("Event source not found")
        return self.get(principal_id, source_id)

    def delete(self, principal_id: str, source_id: str) -> None:
        with self._connect() as db:
            db.execute(
                "DELETE FROM event_source_projections WHERE source_id=? AND principal_id=?",
                (source_id, principal_id),
            )
