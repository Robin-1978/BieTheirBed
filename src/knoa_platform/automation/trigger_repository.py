"""Durable authenticated business triggers and deduplicated events."""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from knoa_platform.agent_runtime.contracts import RuntimeScope
from knoa_platform.automation.models import (
    TriggerEventRecord,
    TriggerEventState,
    TriggerRecord,
    TriggerState,
)
from knoa_platform.exceptions import SessionNotFoundError
from knoa_platform.sqlite_connection import connect_sqlite, initialize_wal
from knoa_platform.sqlite_schema import (
    require_exact_table,
    require_foreign_keys,
    require_index_columns,
)

_MAX_TRIGGER_PAYLOAD_BYTES = 128 * 1024


class TriggerNotFoundError(LookupError):
    pass


class TriggerIdempotencyConflictError(RuntimeError):
    pass


class TriggerTransitionError(RuntimeError):
    pass


class TriggerRepository:
    """Persist trigger definitions and event delivery checkpoints."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        trigger_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] = time.time,
        max_delivery_attempts: int = 5,
        retry_base_seconds: float = 5.0,
    ) -> None:
        if not 1 <= max_delivery_attempts <= 20:
            raise ValueError("Trigger delivery attempts must be between 1 and 20")
        if not 1.0 <= retry_base_seconds <= 3600.0:
            raise ValueError("Trigger retry base must be between 1 and 3600 seconds")
        self._path = Path(db_path).expanduser().resolve()
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._path.parent.chmod(0o700)
        self._trigger_id_factory = trigger_id_factory or (
            lambda: secrets.token_urlsafe(18)
        )
        self._clock = clock
        self._max_delivery_attempts = max_delivery_attempts
        self._retry_base_seconds = retry_base_seconds
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
                CREATE TABLE IF NOT EXISTS runtime_triggers (
                    trigger_id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    session_handle TEXT NOT NULL
                        REFERENCES runtime_sessions(session_handle) ON DELETE CASCADE,
                    client_request_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    tools_enabled INTEGER NOT NULL,
                    priority INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    event_count INTEGER NOT NULL,
                    last_event_at REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(principal_id, client_request_id)
                );
                CREATE TABLE IF NOT EXISTS runtime_trigger_events (
                    trigger_event_id TEXT PRIMARY KEY,
                    trigger_id TEXT NOT NULL
                        REFERENCES runtime_triggers(trigger_id) ON DELETE CASCADE,
                    principal_id TEXT NOT NULL,
                    session_handle TEXT NOT NULL,
                    external_event_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    next_attempt_at REAL,
                    lease_owner TEXT NOT NULL,
                    lease_expires_at REAL,
                    task_id TEXT NOT NULL,
                    failure_code TEXT NOT NULL,
                    received_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(trigger_id, external_event_id)
                );
                CREATE INDEX IF NOT EXISTS runtime_triggers_by_owner_state
                    ON runtime_triggers(principal_id, state, created_at, trigger_id);
                CREATE INDEX IF NOT EXISTS runtime_trigger_events_by_delivery
                    ON runtime_trigger_events(
                        state, next_attempt_at, lease_expires_at, trigger_event_id
                    );
                """
            )
            require_exact_table(
                db,
                "runtime_triggers",
                (
                    ("trigger_id", "TEXT", False, None, 1),
                    ("principal_id", "TEXT", True, None, 0),
                    ("session_handle", "TEXT", True, None, 0),
                    ("client_request_id", "TEXT", True, None, 0),
                    ("name", "TEXT", True, None, 0),
                    ("goal", "TEXT", True, None, 0),
                    ("tools_enabled", "INTEGER", True, None, 0),
                    ("priority", "INTEGER", True, None, 0),
                    ("state", "TEXT", True, None, 0),
                    ("event_count", "INTEGER", True, None, 0),
                    ("last_event_at", "REAL", False, None, 0),
                    ("created_at", "REAL", True, None, 0),
                    ("updated_at", "REAL", True, None, 0),
                ),
                label="Runtime trigger",
            )
            require_exact_table(
                db,
                "runtime_trigger_events",
                (
                    ("trigger_event_id", "TEXT", False, None, 1),
                    ("trigger_id", "TEXT", True, None, 0),
                    ("principal_id", "TEXT", True, None, 0),
                    ("session_handle", "TEXT", True, None, 0),
                    ("external_event_id", "TEXT", True, None, 0),
                    ("payload_json", "TEXT", True, None, 0),
                    ("state", "TEXT", True, None, 0),
                    ("attempt_count", "INTEGER", True, None, 0),
                    ("next_attempt_at", "REAL", False, None, 0),
                    ("lease_owner", "TEXT", True, None, 0),
                    ("lease_expires_at", "REAL", False, None, 0),
                    ("task_id", "TEXT", True, None, 0),
                    ("failure_code", "TEXT", True, None, 0),
                    ("received_at", "REAL", True, None, 0),
                    ("updated_at", "REAL", True, None, 0),
                ),
                label="Runtime trigger event",
            )
            require_foreign_keys(
                db,
                "runtime_triggers",
                (("runtime_sessions", "session_handle", "session_handle", "NO ACTION", "CASCADE"),),
                label="Runtime trigger",
            )
            require_foreign_keys(
                db,
                "runtime_trigger_events",
                (("runtime_triggers", "trigger_id", "trigger_id", "NO ACTION", "CASCADE"),),
                label="Runtime trigger event",
            )
            require_index_columns(
                db,
                "runtime_triggers_by_owner_state",
                ("principal_id", "state", "created_at", "trigger_id"),
                label="Runtime trigger owner index",
            )
            require_index_columns(
                db,
                "runtime_trigger_events_by_delivery",
                ("state", "next_attempt_at", "lease_expires_at", "trigger_event_id"),
                label="Runtime trigger delivery index",
            )

    @staticmethod
    def _identifier(value: str, *, label: str, limit: int = 128) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > limit:
            raise ValueError(f"{label} must contain 1-{limit} characters")
        return normalized

    @staticmethod
    def _event_id(trigger_id: str, external_event_id: str) -> str:
        return hashlib.sha256(
            f"{trigger_id}\0{external_event_id}".encode("utf-8")
        ).hexdigest()[:32]

    @staticmethod
    def _payload_json(payload: dict[str, Any]) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(encoded.encode("utf-8")) > _MAX_TRIGGER_PAYLOAD_BYTES:
            raise ValueError("Trigger payload exceeds 128 KiB")
        return encoded

    @staticmethod
    def _record(row: sqlite3.Row) -> TriggerRecord:
        return TriggerRecord(
            trigger_id=str(row["trigger_id"]),
            principal_id=str(row["principal_id"]),
            session_handle=str(row["session_handle"]),
            client_request_id=str(row["client_request_id"]),
            name=str(row["name"]),
            goal=str(row["goal"]),
            tools_enabled=bool(row["tools_enabled"]),
            priority=int(row["priority"]),
            state=TriggerState(str(row["state"])),
            event_count=int(row["event_count"]),
            last_event_at=(
                None if row["last_event_at"] is None else float(row["last_event_at"])
            ),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    @staticmethod
    def _event(row: sqlite3.Row) -> TriggerEventRecord:
        return TriggerEventRecord(
            trigger_event_id=str(row["trigger_event_id"]),
            trigger_id=str(row["trigger_id"]),
            principal_id=str(row["principal_id"]),
            session_handle=str(row["session_handle"]),
            external_event_id=str(row["external_event_id"]),
            payload=json.loads(row["payload_json"]),
            state=TriggerEventState(str(row["state"])),
            attempt_count=int(row["attempt_count"]),
            next_attempt_at=(
                None
                if row["next_attempt_at"] is None
                else float(row["next_attempt_at"])
            ),
            lease_owner=str(row["lease_owner"]),
            lease_expires_at=(
                None
                if row["lease_expires_at"] is None
                else float(row["lease_expires_at"])
            ),
            task_id=str(row["task_id"]),
            failure_code=str(row["failure_code"]),
            received_at=float(row["received_at"]),
            updated_at=float(row["updated_at"]),
        )

    @staticmethod
    def _owned_session(db: sqlite3.Connection, scope: RuntimeScope) -> None:
        row = db.execute(
            """SELECT 1 FROM runtime_sessions
               WHERE session_handle=? AND principal_id=?""",
            (scope.session_handle, scope.principal_id),
        ).fetchone()
        if row is None:
            raise SessionNotFoundError("Session not found")

    def create(
        self,
        scope: RuntimeScope,
        *,
        client_request_id: str,
        name: str,
        goal: str,
        tools_enabled: bool = True,
        priority: int = 0,
    ) -> tuple[TriggerRecord, bool]:
        request_id = self._identifier(client_request_id, label="client_request_id")
        normalized_name = self._identifier(name, label="name")
        normalized_goal = goal.strip()
        if not normalized_goal or len(normalized_goal) > 64_000:
            raise ValueError("Trigger goal must contain 1-64000 characters")
        if not 0 <= priority <= 9:
            raise ValueError("Trigger priority must be between 0 and 9")
        now = self._clock()
        for _ in range(5):
            trigger_id = self._identifier(
                self._trigger_id_factory(), label="trigger_id"
            )
            try:
                with self._connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    self._owned_session(db, scope)
                    existing = db.execute(
                        """SELECT * FROM runtime_triggers
                           WHERE principal_id=? AND client_request_id=?""",
                        (scope.principal_id, request_id),
                    ).fetchone()
                    if existing is not None:
                        record = self._record(existing)
                        if (
                            record.session_handle != scope.session_handle
                            or record.name != normalized_name
                            or record.goal != normalized_goal
                            or record.tools_enabled is not tools_enabled
                            or record.priority != priority
                        ):
                            raise TriggerIdempotencyConflictError(
                                "Trigger request ID conflicts with existing input"
                            )
                        return record, False
                    db.execute(
                        """INSERT INTO runtime_triggers(
                               trigger_id, principal_id, session_handle,
                               client_request_id, name, goal, tools_enabled,
                               priority, state, event_count, last_event_at,
                               created_at, updated_at
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            trigger_id,
                            scope.principal_id,
                            scope.session_handle,
                            request_id,
                            normalized_name,
                            normalized_goal,
                            int(tools_enabled),
                            priority,
                            TriggerState.ACTIVE.value,
                            0,
                            None,
                            now,
                            now,
                        ),
                    )
                    row = db.execute(
                        "SELECT * FROM runtime_triggers WHERE trigger_id=?",
                        (trigger_id,),
                    ).fetchone()
                    assert row is not None
                    return self._record(row), True
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("Trigger ID allocation failed")

    def get(self, principal_id: str, trigger_id: str) -> TriggerRecord:
        principal = self._identifier(principal_id, label="principal_id", limit=256)
        normalized_id = self._identifier(trigger_id, label="trigger_id")
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM runtime_triggers
                   WHERE trigger_id=? AND principal_id=?""",
                (normalized_id, principal),
            ).fetchone()
        if row is None:
            raise TriggerNotFoundError("Trigger not found")
        return self._record(row)

    def delete(self, principal_id: str, trigger_id: str) -> None:
        trigger = self.get(principal_id, trigger_id)
        with self._connect() as db:
            db.execute(
                "DELETE FROM runtime_triggers WHERE trigger_id=? AND principal_id=?",
                (trigger.trigger_id, trigger.principal_id),
            )

    def list(
        self,
        principal_id: str,
        *,
        state: TriggerState | None = None,
        limit: int = 50,
    ) -> tuple[TriggerRecord, ...]:
        principal = self._identifier(principal_id, label="principal_id", limit=256)
        if not 1 <= limit <= 100:
            raise ValueError("Trigger list limit must be between 1 and 100")
        query = "SELECT * FROM runtime_triggers WHERE principal_id=?"
        parameters: list[object] = [principal]
        if state is not None:
            query += " AND state=?"
            parameters.append(state.value)
        query += " ORDER BY created_at DESC, trigger_id DESC LIMIT ?"
        parameters.append(limit)
        with self._connect() as db:
            rows = db.execute(query, tuple(parameters)).fetchall()
        return tuple(self._record(row) for row in rows)

    def set_paused(
        self,
        principal_id: str,
        trigger_id: str,
        *,
        paused: bool,
    ) -> TriggerRecord:
        principal = self._identifier(principal_id, label="principal_id", limit=256)
        normalized_id = self._identifier(trigger_id, label="trigger_id")
        target = TriggerState.PAUSED if paused else TriggerState.ACTIVE
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT * FROM runtime_triggers
                   WHERE trigger_id=? AND principal_id=?""",
                (normalized_id, principal),
            ).fetchone()
            if row is None:
                raise TriggerNotFoundError("Trigger not found")
            if TriggerState(str(row["state"])) is target:
                return self._record(row)
            db.execute(
                """UPDATE runtime_triggers SET state=?, updated_at=?
                   WHERE trigger_id=?""",
                (target.value, now, normalized_id),
            )
            updated = db.execute(
                "SELECT * FROM runtime_triggers WHERE trigger_id=?",
                (normalized_id,),
            ).fetchone()
            assert updated is not None
            return self._record(updated)

    def receive(
        self,
        principal_id: str,
        trigger_id: str,
        *,
        external_event_id: str,
        payload: dict[str, Any],
    ) -> tuple[TriggerEventRecord, bool]:
        principal = self._identifier(principal_id, label="principal_id", limit=256)
        normalized_trigger = self._identifier(trigger_id, label="trigger_id")
        normalized_external = self._identifier(
            external_event_id, label="external_event_id", limit=256
        )
        payload_json = self._payload_json(payload)
        trigger_event_id = self._event_id(normalized_trigger, normalized_external)
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            trigger = db.execute(
                """SELECT * FROM runtime_triggers
                   WHERE trigger_id=? AND principal_id=?""",
                (normalized_trigger, principal),
            ).fetchone()
            if trigger is None:
                raise TriggerNotFoundError("Trigger not found")
            if TriggerState(str(trigger["state"])) is not TriggerState.ACTIVE:
                raise TriggerTransitionError("Paused trigger cannot receive events")
            existing = db.execute(
                """SELECT * FROM runtime_trigger_events
                   WHERE trigger_id=? AND external_event_id=?""",
                (normalized_trigger, normalized_external),
            ).fetchone()
            if existing is not None:
                event = self._event(existing)
                if self._payload_json(event.payload) != payload_json:
                    raise TriggerIdempotencyConflictError(
                        "External event ID conflicts with another payload"
                    )
                return event, False
            db.execute(
                """INSERT INTO runtime_trigger_events(
                       trigger_event_id, trigger_id, principal_id,
                       session_handle, external_event_id, payload_json, state,
                       attempt_count, next_attempt_at, lease_owner,
                       lease_expires_at, task_id, failure_code, received_at,
                       updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trigger_event_id,
                    normalized_trigger,
                    principal,
                    str(trigger["session_handle"]),
                    normalized_external,
                    payload_json,
                    TriggerEventState.RECEIVED.value,
                    0,
                    None,
                    "",
                    None,
                    "",
                    "",
                    now,
                    now,
                ),
            )
            db.execute(
                """UPDATE runtime_triggers SET
                       event_count=event_count+1, last_event_at=?, updated_at=?
                   WHERE trigger_id=?""",
                (now, now, normalized_trigger),
            )
            row = db.execute(
                """SELECT * FROM runtime_trigger_events
                   WHERE trigger_event_id=?""",
                (trigger_event_id,),
            ).fetchone()
            assert row is not None
            return self._event(row), True

    def baseline(
        self,
        principal_id: str,
        trigger_id: str,
        events: tuple[tuple[str, dict[str, Any]], ...] = (),
    ) -> int:
        """Mark the current Resource inventory as observed without delivery."""
        principal = self._identifier(principal_id, label="principal_id", limit=256)
        normalized_trigger = self._identifier(trigger_id, label="trigger_id")
        normalized_events = tuple(
            (
                self._identifier(external_id, label="external_event_id", limit=256),
                self._payload_json(payload),
            )
            for external_id, payload in events
        )
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            trigger = db.execute(
                """SELECT * FROM runtime_triggers
                   WHERE trigger_id=? AND principal_id=?""",
                (normalized_trigger, principal),
            ).fetchone()
            if trigger is None:
                raise TriggerNotFoundError("Trigger not found")
            if TriggerState(str(trigger["state"])) is not TriggerState.ACTIVE:
                raise TriggerTransitionError("Paused trigger cannot be baselined")
            if trigger["last_event_at"] is not None:
                return 0
            inserted = 0
            for external_id, payload_json in normalized_events:
                event_id = self._event_id(normalized_trigger, external_id)
                existing = db.execute(
                    """SELECT payload_json FROM runtime_trigger_events
                       WHERE trigger_id=? AND external_event_id=?""",
                    (normalized_trigger, external_id),
                ).fetchone()
                if existing is not None:
                    if str(existing["payload_json"]) != payload_json:
                        raise TriggerIdempotencyConflictError(
                            "External event ID conflicts with another payload"
                        )
                    continue
                db.execute(
                    """INSERT INTO runtime_trigger_events(
                           trigger_event_id, trigger_id, principal_id,
                           session_handle, external_event_id, payload_json, state,
                           attempt_count, next_attempt_at, lease_owner,
                           lease_expires_at, task_id, failure_code, received_at,
                           updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL, '', NULL, '', ?, ?, ?)""",
                    (
                        event_id,
                        normalized_trigger,
                        principal,
                        str(trigger["session_handle"]),
                        external_id,
                        payload_json,
                        TriggerEventState.BASELINED.value,
                        "initial_inventory",
                        now,
                        now,
                    ),
                )
                inserted += 1
            db.execute(
                """UPDATE runtime_triggers SET last_event_at=?, updated_at=?
                   WHERE trigger_id=? AND principal_id=?""",
                (now, now, normalized_trigger, principal),
            )
            return inserted

    def get_event(self, trigger_event_id: str) -> TriggerEventRecord:
        normalized_id = self._identifier(
            trigger_event_id, label="trigger_event_id"
        )
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM runtime_trigger_events
                   WHERE trigger_event_id=?""",
                (normalized_id,),
            ).fetchone()
        if row is None:
            raise TriggerNotFoundError("Trigger event not found")
        return self._event(row)

    def claim_next(
        self,
        worker_id: str,
        *,
        lease_seconds: float = 60.0,
    ) -> TriggerEventRecord | None:
        worker = self._identifier(worker_id, label="worker_id")
        if not 1.0 <= lease_seconds <= 3600.0:
            raise ValueError("Trigger lease must be between 1 and 3600 seconds")
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT event.* FROM runtime_trigger_events AS event
                   JOIN runtime_triggers AS trigger
                     ON trigger.trigger_id=event.trigger_id
                   WHERE trigger.state=? AND (
                       event.state=?
                       OR (event.state=? AND event.next_attempt_at<=?)
                       OR (event.state=? AND event.lease_expires_at<=?)
                   )
                   ORDER BY event.received_at, event.trigger_event_id LIMIT 1""",
                (
                    TriggerState.ACTIVE.value,
                    TriggerEventState.RECEIVED.value,
                    TriggerEventState.RETRY_WAIT.value,
                    now,
                    TriggerEventState.CLAIMED.value,
                    now,
                ),
            ).fetchone()
            if row is None:
                return None
            if (
                TriggerEventState(str(row["state"])) is TriggerEventState.CLAIMED
                and int(row["attempt_count"]) >= self._max_delivery_attempts
            ):
                db.execute(
                    """UPDATE runtime_trigger_events SET
                           state=?, lease_owner='', lease_expires_at=NULL,
                           failure_code=?, updated_at=? WHERE trigger_event_id=?""",
                    (
                        TriggerEventState.DEAD.value,
                        "delivery_lease_exhausted",
                        now,
                        str(row["trigger_event_id"]),
                    ),
                )
                return None
            db.execute(
                """UPDATE runtime_trigger_events SET
                       state=?, attempt_count=attempt_count+1,
                       next_attempt_at=NULL, lease_owner=?, lease_expires_at=?,
                       updated_at=? WHERE trigger_event_id=?""",
                (
                    TriggerEventState.CLAIMED.value,
                    worker,
                    now + lease_seconds,
                    now,
                    str(row["trigger_event_id"]),
                ),
            )
            claimed = db.execute(
                """SELECT * FROM runtime_trigger_events
                   WHERE trigger_event_id=?""",
                (str(row["trigger_event_id"]),),
            ).fetchone()
            assert claimed is not None
            return self._event(claimed)

    def seconds_until_next_dispatch(self) -> float | None:
        """Return the delay until an active trigger event needs delivery."""

        with self._connect() as db:
            row = db.execute(
                """SELECT MIN(
                       CASE event.state
                           WHEN ? THEN ?
                           WHEN ? THEN event.next_attempt_at
                           WHEN ? THEN event.lease_expires_at
                       END
                   )
                   FROM runtime_trigger_events AS event
                   JOIN runtime_triggers AS trigger
                     ON trigger.trigger_id=event.trigger_id
                   WHERE trigger.state=? AND event.state IN (?, ?, ?)""",
                (
                    TriggerEventState.RECEIVED.value,
                    float(self._clock()),
                    TriggerEventState.RETRY_WAIT.value,
                    TriggerEventState.CLAIMED.value,
                    TriggerState.ACTIVE.value,
                    TriggerEventState.RECEIVED.value,
                    TriggerEventState.RETRY_WAIT.value,
                    TriggerEventState.CLAIMED.value,
                ),
            ).fetchone()
        if row is None or row[0] is None:
            return None
        return max(0.0, float(row[0]) - float(self._clock()))

    def mark_task_created(
        self,
        trigger_event_id: str,
        task_id: str,
    ) -> TriggerEventRecord:
        event_id = self._identifier(trigger_event_id, label="trigger_event_id")
        normalized_task = self._identifier(task_id, label="task_id")
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT * FROM runtime_trigger_events
                   WHERE trigger_event_id=?""",
                (event_id,),
            ).fetchone()
            if row is None:
                raise TriggerNotFoundError("Trigger event not found")
            current = self._event(row)
            if current.state is TriggerEventState.TASK_CREATED:
                if current.task_id != normalized_task:
                    raise TriggerIdempotencyConflictError(
                        "Trigger event already created another Task"
                    )
                return current
            if current.state is not TriggerEventState.CLAIMED:
                raise TriggerTransitionError("Trigger event is not claimed")
            db.execute(
                """UPDATE runtime_trigger_events SET
                       state=?, task_id=?, lease_owner='', lease_expires_at=NULL,
                       failure_code='', updated_at=? WHERE trigger_event_id=?""",
                (
                    TriggerEventState.TASK_CREATED.value,
                    normalized_task,
                    now,
                    event_id,
                ),
            )
            updated = db.execute(
                """SELECT * FROM runtime_trigger_events
                   WHERE trigger_event_id=?""",
                (event_id,),
            ).fetchone()
            assert updated is not None
            return self._event(updated)

    def mark_delivery_failed(
        self,
        trigger_event_id: str,
        *,
        failure_code: str,
    ) -> TriggerEventRecord:
        event_id = self._identifier(trigger_event_id, label="trigger_event_id")
        normalized_failure = self._identifier(
            failure_code, label="failure_code", limit=256
        )
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT * FROM runtime_trigger_events
                   WHERE trigger_event_id=?""",
                (event_id,),
            ).fetchone()
            if row is None:
                raise TriggerNotFoundError("Trigger event not found")
            current = self._event(row)
            if current.state is not TriggerEventState.CLAIMED:
                raise TriggerTransitionError("Trigger event is not claimed")
            exhausted = current.attempt_count >= self._max_delivery_attempts
            state = (
                TriggerEventState.DEAD
                if exhausted
                else TriggerEventState.RETRY_WAIT
            )
            next_attempt = (
                None
                if exhausted
                else now
                + self._retry_base_seconds * (2 ** (current.attempt_count - 1))
            )
            db.execute(
                """UPDATE runtime_trigger_events SET
                       state=?, next_attempt_at=?, lease_owner='',
                       lease_expires_at=NULL, failure_code=?, updated_at=?
                   WHERE trigger_event_id=?""",
                (
                    state.value,
                    next_attempt,
                    normalized_failure,
                    now,
                    event_id,
                ),
            )
            updated = db.execute(
                """SELECT * FROM runtime_trigger_events
                   WHERE trigger_event_id=?""",
                (event_id,),
            ).fetchone()
            assert updated is not None
            return self._event(updated)
