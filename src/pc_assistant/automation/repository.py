"""SQLite ownership for durable schedules and idempotent occurrences."""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from pc_assistant.agent_runtime.contracts import RuntimeScope
from pc_assistant.automation.models import (
    OccurrenceState,
    ScheduleOccurrenceRecord,
    ScheduleRecord,
    ScheduleSpec,
    ScheduleState,
)
from pc_assistant.automation.recurrence import next_fire_at
from pc_assistant.exceptions import SessionNotFoundError
from pc_assistant.sqlite_schema import (
    require_exact_table,
    require_foreign_keys,
    require_index_columns,
)


class ScheduleNotFoundError(LookupError):
    pass


class ScheduleIdempotencyConflictError(RuntimeError):
    pass


class ScheduleTransitionError(RuntimeError):
    pass


class ScheduleRepository:
    """Persist schedules and claim each occurrence before Task creation."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        schedule_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] = time.time,
        max_delivery_attempts: int = 5,
        retry_base_seconds: float = 5.0,
    ) -> None:
        if not 1 <= max_delivery_attempts <= 20:
            raise ValueError("Schedule delivery attempts must be between 1 and 20")
        if not 1.0 <= retry_base_seconds <= 3600.0:
            raise ValueError("Schedule retry base must be between 1 and 3600 seconds")
        self._path = Path(db_path).expanduser().resolve()
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._path.parent.chmod(0o700)
        self._schedule_id_factory = schedule_id_factory or (
            lambda: secrets.token_urlsafe(18)
        )
        self._clock = clock
        self._max_delivery_attempts = max_delivery_attempts
        self._retry_base_seconds = retry_base_seconds
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5.0)
        self._path.chmod(0o600)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS runtime_schedules (
                    schedule_id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    session_handle TEXT NOT NULL
                        REFERENCES runtime_sessions(session_handle) ON DELETE CASCADE,
                    client_request_id TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    tools_enabled INTEGER NOT NULL,
                    priority INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    next_fire_at REAL,
                    last_fire_at REAL,
                    fire_count INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(principal_id, client_request_id)
                );
                CREATE TABLE IF NOT EXISTS runtime_schedule_occurrences (
                    occurrence_id TEXT PRIMARY KEY,
                    schedule_id TEXT NOT NULL
                        REFERENCES runtime_schedules(schedule_id) ON DELETE CASCADE,
                    principal_id TEXT NOT NULL,
                    session_handle TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    scheduled_for REAL NOT NULL,
                    state TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    next_attempt_at REAL,
                    lease_owner TEXT NOT NULL,
                    lease_expires_at REAL,
                    task_id TEXT NOT NULL,
                    failure_code TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(schedule_id, ordinal)
                );
                CREATE INDEX IF NOT EXISTS runtime_schedules_by_due
                    ON runtime_schedules(state, next_fire_at, schedule_id);
                CREATE INDEX IF NOT EXISTS runtime_schedule_occurrences_by_delivery
                    ON runtime_schedule_occurrences(
                        state, next_attempt_at, lease_expires_at, occurrence_id
                    );
                """
            )
            require_exact_table(
                db,
                "runtime_schedules",
                (
                    ("schedule_id", "TEXT", False, None, 1),
                    ("principal_id", "TEXT", True, None, 0),
                    ("session_handle", "TEXT", True, None, 0),
                    ("client_request_id", "TEXT", True, None, 0),
                    ("goal", "TEXT", True, None, 0),
                    ("spec_json", "TEXT", True, None, 0),
                    ("tools_enabled", "INTEGER", True, None, 0),
                    ("priority", "INTEGER", True, None, 0),
                    ("state", "TEXT", True, None, 0),
                    ("next_fire_at", "REAL", False, None, 0),
                    ("last_fire_at", "REAL", False, None, 0),
                    ("fire_count", "INTEGER", True, None, 0),
                    ("created_at", "REAL", True, None, 0),
                    ("updated_at", "REAL", True, None, 0),
                ),
                label="Runtime schedule",
            )
            require_exact_table(
                db,
                "runtime_schedule_occurrences",
                (
                    ("occurrence_id", "TEXT", False, None, 1),
                    ("schedule_id", "TEXT", True, None, 0),
                    ("principal_id", "TEXT", True, None, 0),
                    ("session_handle", "TEXT", True, None, 0),
                    ("ordinal", "INTEGER", True, None, 0),
                    ("scheduled_for", "REAL", True, None, 0),
                    ("state", "TEXT", True, None, 0),
                    ("attempt_count", "INTEGER", True, None, 0),
                    ("next_attempt_at", "REAL", False, None, 0),
                    ("lease_owner", "TEXT", True, None, 0),
                    ("lease_expires_at", "REAL", False, None, 0),
                    ("task_id", "TEXT", True, None, 0),
                    ("failure_code", "TEXT", True, None, 0),
                    ("created_at", "REAL", True, None, 0),
                    ("updated_at", "REAL", True, None, 0),
                ),
                label="Runtime schedule occurrence",
            )
            require_foreign_keys(
                db,
                "runtime_schedules",
                (("runtime_sessions", "session_handle", "session_handle", "NO ACTION", "CASCADE"),),
                label="Runtime schedule",
            )
            require_foreign_keys(
                db,
                "runtime_schedule_occurrences",
                (("runtime_schedules", "schedule_id", "schedule_id", "NO ACTION", "CASCADE"),),
                label="Runtime schedule occurrence",
            )
            require_index_columns(
                db,
                "runtime_schedules_by_due",
                ("state", "next_fire_at", "schedule_id"),
                label="Runtime schedule due index",
            )
            require_index_columns(
                db,
                "runtime_schedule_occurrences_by_delivery",
                ("state", "next_attempt_at", "lease_expires_at", "occurrence_id"),
                label="Runtime schedule delivery index",
            )

    @staticmethod
    def _identifier(value: str, *, label: str, limit: int = 128) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > limit:
            raise ValueError(f"{label} must contain 1-{limit} characters")
        return normalized

    @staticmethod
    def _occurrence_id(schedule_id: str, ordinal: int) -> str:
        return hashlib.sha256(
            f"{schedule_id}\0{ordinal}".encode("utf-8")
        ).hexdigest()[:32]

    @staticmethod
    def _spec_json(spec: ScheduleSpec) -> str:
        return json.dumps(
            spec.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _record(row: sqlite3.Row) -> ScheduleRecord:
        return ScheduleRecord(
            schedule_id=str(row["schedule_id"]),
            principal_id=str(row["principal_id"]),
            session_handle=str(row["session_handle"]),
            client_request_id=str(row["client_request_id"]),
            goal=str(row["goal"]),
            spec=ScheduleSpec.model_validate_json(row["spec_json"]),
            tools_enabled=bool(row["tools_enabled"]),
            priority=int(row["priority"]),
            state=ScheduleState(str(row["state"])),
            next_fire_at=(
                None if row["next_fire_at"] is None else float(row["next_fire_at"])
            ),
            last_fire_at=(
                None if row["last_fire_at"] is None else float(row["last_fire_at"])
            ),
            fire_count=int(row["fire_count"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    @staticmethod
    def _occurrence(row: sqlite3.Row) -> ScheduleOccurrenceRecord:
        return ScheduleOccurrenceRecord(
            occurrence_id=str(row["occurrence_id"]),
            schedule_id=str(row["schedule_id"]),
            principal_id=str(row["principal_id"]),
            session_handle=str(row["session_handle"]),
            ordinal=int(row["ordinal"]),
            scheduled_for=float(row["scheduled_for"]),
            state=OccurrenceState(str(row["state"])),
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
            created_at=float(row["created_at"]),
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
        goal: str,
        spec: ScheduleSpec,
        tools_enabled: bool = True,
        priority: int = 0,
    ) -> tuple[ScheduleRecord, bool]:
        request_id = self._identifier(client_request_id, label="client_request_id")
        normalized_goal = goal.strip()
        if not normalized_goal or len(normalized_goal) > 200_000:
            raise ValueError("Schedule goal must contain 1-200000 characters")
        if not 0 <= priority <= 9:
            raise ValueError("Schedule priority must be between 0 and 9")
        now = self._clock()
        due = next_fire_at(spec, after=now)
        if due is None:
            raise ValueError("Schedule has no future occurrence")
        spec_json = self._spec_json(spec)
        for _ in range(5):
            schedule_id = self._identifier(
                self._schedule_id_factory(), label="schedule_id"
            )
            try:
                with self._connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    self._owned_session(db, scope)
                    existing = db.execute(
                        """SELECT * FROM runtime_schedules
                           WHERE principal_id=? AND client_request_id=?""",
                        (scope.principal_id, request_id),
                    ).fetchone()
                    if existing is not None:
                        record = self._record(existing)
                        if (
                            record.session_handle != scope.session_handle
                            or record.goal != normalized_goal
                            or self._spec_json(record.spec) != spec_json
                            or record.tools_enabled is not tools_enabled
                            or record.priority != priority
                        ):
                            raise ScheduleIdempotencyConflictError(
                                "Schedule request ID conflicts with existing input"
                            )
                        return record, False
                    db.execute(
                        """INSERT INTO runtime_schedules(
                               schedule_id, principal_id, session_handle,
                               client_request_id, goal, spec_json, tools_enabled,
                               priority, state, next_fire_at, last_fire_at,
                               fire_count, created_at, updated_at
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            schedule_id,
                            scope.principal_id,
                            scope.session_handle,
                            request_id,
                            normalized_goal,
                            spec_json,
                            int(tools_enabled),
                            priority,
                            ScheduleState.ACTIVE.value,
                            due,
                            None,
                            0,
                            now,
                            now,
                        ),
                    )
                    row = db.execute(
                        "SELECT * FROM runtime_schedules WHERE schedule_id=?",
                        (schedule_id,),
                    ).fetchone()
                    assert row is not None
                    return self._record(row), True
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("Schedule ID allocation failed")

    def get(self, principal_id: str, schedule_id: str) -> ScheduleRecord:
        principal = self._identifier(principal_id, label="principal_id", limit=256)
        normalized_id = self._identifier(schedule_id, label="schedule_id")
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM runtime_schedules
                   WHERE schedule_id=? AND principal_id=?""",
                (normalized_id, principal),
            ).fetchone()
        if row is None:
            raise ScheduleNotFoundError("Schedule not found")
        return self._record(row)

    def get_occurrence(self, occurrence_id: str) -> ScheduleOccurrenceRecord:
        normalized_id = self._identifier(occurrence_id, label="occurrence_id")
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM runtime_schedule_occurrences
                   WHERE occurrence_id=?""",
                (normalized_id,),
            ).fetchone()
        if row is None:
            raise ScheduleNotFoundError("Schedule occurrence not found")
        return self._occurrence(row)

    def list(
        self,
        principal_id: str,
        *,
        state: ScheduleState | None = None,
        limit: int = 50,
    ) -> tuple[ScheduleRecord, ...]:
        principal = self._identifier(principal_id, label="principal_id", limit=256)
        if not 1 <= limit <= 100:
            raise ValueError("Schedule list limit must be between 1 and 100")
        query = "SELECT * FROM runtime_schedules WHERE principal_id=?"
        parameters: list[object] = [principal]
        if state is not None:
            query += " AND state=?"
            parameters.append(state.value)
        query += " ORDER BY created_at DESC, schedule_id DESC LIMIT ?"
        parameters.append(limit)
        with self._connect() as db:
            rows = db.execute(query, tuple(parameters)).fetchall()
        return tuple(self._record(row) for row in rows)

    def claim_due(
        self,
        worker_id: str,
        *,
        lease_seconds: float = 60.0,
    ) -> ScheduleOccurrenceRecord | None:
        worker = self._identifier(worker_id, label="worker_id")
        if not 1.0 <= lease_seconds <= 3600.0:
            raise ValueError("Occurrence lease must be between 1 and 3600 seconds")
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                """SELECT * FROM runtime_schedule_occurrences
                   WHERE (state=? AND next_attempt_at<=?)
                      OR (state=? AND lease_expires_at<=?)
                   ORDER BY scheduled_for, occurrence_id LIMIT 1""",
                (
                    OccurrenceState.RETRY_WAIT.value,
                    now,
                    OccurrenceState.CLAIMED.value,
                    now,
                ),
            ).fetchone()
            if existing is not None:
                if (
                    OccurrenceState(str(existing["state"]))
                    is OccurrenceState.CLAIMED
                    and int(existing["attempt_count"])
                    >= self._max_delivery_attempts
                ):
                    db.execute(
                        """UPDATE runtime_schedule_occurrences SET
                               state=?, lease_owner='', lease_expires_at=NULL,
                               failure_code=?, updated_at=? WHERE occurrence_id=?""",
                        (
                            OccurrenceState.DEAD.value,
                            "delivery_lease_exhausted",
                            now,
                            str(existing["occurrence_id"]),
                        ),
                    )
                    return None
                db.execute(
                    """UPDATE runtime_schedule_occurrences SET
                           state=?, attempt_count=attempt_count+1,
                           next_attempt_at=NULL, lease_owner=?, lease_expires_at=?,
                           updated_at=? WHERE occurrence_id=?""",
                    (
                        OccurrenceState.CLAIMED.value,
                        worker,
                        now + lease_seconds,
                        now,
                        str(existing["occurrence_id"]),
                    ),
                )
                row = db.execute(
                    """SELECT * FROM runtime_schedule_occurrences
                       WHERE occurrence_id=?""",
                    (str(existing["occurrence_id"]),),
                ).fetchone()
                assert row is not None
                return self._occurrence(row)

            schedule = db.execute(
                """SELECT * FROM runtime_schedules
                   WHERE state=? AND next_fire_at<=?
                   ORDER BY next_fire_at, schedule_id LIMIT 1""",
                (ScheduleState.ACTIVE.value, now),
            ).fetchone()
            if schedule is None:
                return None
            record = self._record(schedule)
            assert record.next_fire_at is not None
            ordinal = record.fire_count + 1
            occurrence_id = self._occurrence_id(record.schedule_id, ordinal)
            following = next_fire_at(record.spec, after=max(now, record.next_fire_at))
            next_state = (
                ScheduleState.COMPLETED if following is None else ScheduleState.ACTIVE
            )
            db.execute(
                """INSERT INTO runtime_schedule_occurrences(
                       occurrence_id, schedule_id, principal_id, session_handle,
                       ordinal, scheduled_for, state, attempt_count,
                       next_attempt_at, lease_owner, lease_expires_at, task_id,
                       failure_code, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    occurrence_id,
                    record.schedule_id,
                    record.principal_id,
                    record.session_handle,
                    ordinal,
                    record.next_fire_at,
                    OccurrenceState.CLAIMED.value,
                    1,
                    None,
                    worker,
                    now + lease_seconds,
                    "",
                    "",
                    now,
                    now,
                ),
            )
            db.execute(
                """UPDATE runtime_schedules SET
                       state=?, next_fire_at=?, last_fire_at=?,
                       fire_count=?, updated_at=? WHERE schedule_id=?""",
                (
                    next_state.value,
                    following,
                    record.next_fire_at,
                    ordinal,
                    now,
                    record.schedule_id,
                ),
            )
            row = db.execute(
                """SELECT * FROM runtime_schedule_occurrences
                   WHERE occurrence_id=?""",
                (occurrence_id,),
            ).fetchone()
            assert row is not None
            return self._occurrence(row)

    def mark_task_created(
        self,
        occurrence_id: str,
        task_id: str,
    ) -> ScheduleOccurrenceRecord:
        normalized_occurrence = self._identifier(
            occurrence_id, label="occurrence_id"
        )
        normalized_task = self._identifier(task_id, label="task_id")
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT * FROM runtime_schedule_occurrences
                   WHERE occurrence_id=?""",
                (normalized_occurrence,),
            ).fetchone()
            if row is None:
                raise ScheduleNotFoundError("Schedule occurrence not found")
            current = self._occurrence(row)
            if current.state is OccurrenceState.TASK_CREATED:
                if current.task_id != normalized_task:
                    raise ScheduleIdempotencyConflictError(
                        "Occurrence already created another Task"
                    )
                return current
            if current.state is not OccurrenceState.CLAIMED:
                raise ScheduleTransitionError("Occurrence is not claimed")
            db.execute(
                """UPDATE runtime_schedule_occurrences SET
                       state=?, task_id=?, lease_owner='', lease_expires_at=NULL,
                       failure_code='', updated_at=? WHERE occurrence_id=?""",
                (
                    OccurrenceState.TASK_CREATED.value,
                    normalized_task,
                    now,
                    normalized_occurrence,
                ),
            )
            updated = db.execute(
                """SELECT * FROM runtime_schedule_occurrences
                   WHERE occurrence_id=?""",
                (normalized_occurrence,),
            ).fetchone()
            assert updated is not None
            return self._occurrence(updated)

    def mark_delivery_failed(
        self,
        occurrence_id: str,
        *,
        failure_code: str,
    ) -> ScheduleOccurrenceRecord:
        normalized_occurrence = self._identifier(
            occurrence_id, label="occurrence_id"
        )
        normalized_failure = self._identifier(
            failure_code, label="failure_code", limit=256
        )
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT * FROM runtime_schedule_occurrences
                   WHERE occurrence_id=?""",
                (normalized_occurrence,),
            ).fetchone()
            if row is None:
                raise ScheduleNotFoundError("Schedule occurrence not found")
            current = self._occurrence(row)
            if current.state is not OccurrenceState.CLAIMED:
                raise ScheduleTransitionError("Occurrence is not claimed")
            exhausted = current.attempt_count >= self._max_delivery_attempts
            state = OccurrenceState.DEAD if exhausted else OccurrenceState.RETRY_WAIT
            next_attempt = (
                None
                if exhausted
                else now
                + self._retry_base_seconds * (2 ** (current.attempt_count - 1))
            )
            db.execute(
                """UPDATE runtime_schedule_occurrences SET
                       state=?, next_attempt_at=?, lease_owner='',
                       lease_expires_at=NULL, failure_code=?, updated_at=?
                   WHERE occurrence_id=?""",
                (
                    state.value,
                    next_attempt,
                    normalized_failure,
                    now,
                    normalized_occurrence,
                ),
            )
            updated = db.execute(
                """SELECT * FROM runtime_schedule_occurrences
                   WHERE occurrence_id=?""",
                (normalized_occurrence,),
            ).fetchone()
            assert updated is not None
            return self._occurrence(updated)
