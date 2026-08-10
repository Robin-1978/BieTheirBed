"""SQLite repository owning durable Task state and ordered events."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import secrets
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from pc_assistant.agent_runtime.contracts import ArtifactAttachment, RuntimeScope
from pc_assistant.sqlite_schema import (
    require_exact_table,
    require_foreign_keys,
    require_index_columns,
)
from pc_assistant.tasks.models import (
    ApprovalState,
    PrincipalTaskEvent,
    TERMINAL_TASK_STATES,
    TaskApprovalRecord,
    TaskAttemptRecord,
    TaskAttemptState,
    TaskCancelResult,
    TaskEvent,
    TaskEventPayload,
    TaskEventType,
    TaskPauseResult,
    TaskOrigin,
    TaskRecord,
    TaskState,
    TaskToolStepRecord,
    TaskToolStepState,
)


_MAX_EVENT_BYTES = 512 * 1024
_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.QUEUED: frozenset({TaskState.RUNNING, TaskState.CANCELLED}),
    TaskState.RUNNING: frozenset(
        {
            TaskState.WAITING_APPROVAL,
            TaskState.PAUSED,
            TaskState.COMPLETED,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.WAITING_APPROVAL: frozenset(
        {TaskState.RUNNING, TaskState.PAUSED, TaskState.CANCELLED}
    ),
    TaskState.PAUSED: frozenset(
        {TaskState.QUEUED, TaskState.FAILED, TaskState.CANCELLED}
    ),
    TaskState.COMPLETED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


class TaskNotFoundError(LookupError):
    pass


class TaskIdempotencyConflictError(RuntimeError):
    pass


class TaskTransitionError(RuntimeError):
    pass


class TaskCapacityError(RuntimeError):
    pass


class TaskRepository:
    """Persist Task aggregates and journal events with principal ownership."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        task_id_factory: Callable[[], str] | None = None,
        approval_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] = time.time,
        max_active_tasks: int = 128,
        max_active_tasks_per_principal: int = 32,
    ) -> None:
        if not 1 <= max_active_tasks <= 10_000:
            raise ValueError("Global active Task limit must be between 1 and 10000")
        if not 1 <= max_active_tasks_per_principal <= max_active_tasks:
            raise ValueError(
                "Per-principal active Task limit must be between 1 and the global limit"
            )
        self._path = Path(db_path).expanduser().resolve()
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._path.parent.chmod(0o700)
        self._task_id_factory = task_id_factory or (
            lambda: secrets.token_urlsafe(18)
        )
        self._approval_id_factory = approval_id_factory or (
            lambda: secrets.token_urlsafe(18)
        )
        self._clock = clock
        self._max_active_tasks = max_active_tasks
        self._max_active_tasks_per_principal = max_active_tasks_per_principal
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
                CREATE TABLE IF NOT EXISTS runtime_tasks (
                    task_id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    session_handle TEXT NOT NULL
                        REFERENCES runtime_sessions(session_handle) ON DELETE CASCADE,
                    client_request_id TEXT NOT NULL,
                    parent_task_id TEXT
                        REFERENCES runtime_tasks(task_id) ON DELETE RESTRICT,
                    goal TEXT NOT NULL,
                    attachments_json TEXT NOT NULL,
                    tools_enabled INTEGER NOT NULL,
                    priority INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    cancel_requested INTEGER NOT NULL,
                    final_summary TEXT NOT NULL,
                    failure_code TEXT NOT NULL,
                    lease_owner TEXT NOT NULL,
                    lease_expires_at REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL,
                    next_event_seq INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    origin TEXT NOT NULL DEFAULT 'chat',
                    UNIQUE(principal_id, client_request_id)
                );
                CREATE TABLE IF NOT EXISTS runtime_task_events (
                    task_id TEXT NOT NULL
                        REFERENCES runtime_tasks(task_id) ON DELETE CASCADE,
                    event_seq INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    occurred_at REAL NOT NULL,
                    PRIMARY KEY(task_id, event_seq)
                );
                CREATE TABLE IF NOT EXISTS runtime_principal_task_events (
                    feed_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    principal_id TEXT NOT NULL,
                    task_id TEXT NOT NULL
                        REFERENCES runtime_tasks(task_id) ON DELETE CASCADE,
                    task_event_seq INTEGER NOT NULL,
                    occurred_at REAL NOT NULL,
                    UNIQUE(task_id, task_event_seq)
                );
                CREATE TABLE IF NOT EXISTS runtime_task_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL
                        REFERENCES runtime_tasks(task_id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    failure_code TEXT NOT NULL,
                    UNIQUE(task_id, ordinal)
                );
                CREATE TABLE IF NOT EXISTS runtime_task_tool_steps (
                    tool_step_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL
                        REFERENCES runtime_tasks(task_id) ON DELETE CASCADE,
                    principal_id TEXT NOT NULL,
                    tool_call_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    effect TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    state TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(task_id, tool_step_id)
                );
                CREATE TABLE IF NOT EXISTS runtime_task_approvals (
                    approval_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL
                        REFERENCES runtime_tasks(task_id) ON DELETE CASCADE,
                    principal_id TEXT NOT NULL,
                    tool_step_id TEXT NOT NULL,
                    tool_call_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    state TEXT NOT NULL,
                    request_event_seq INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    resolved_at REAL,
                    expires_at REAL,
                    resolved_by TEXT NOT NULL,
                    UNIQUE(task_id, tool_step_id)
                );
                CREATE INDEX IF NOT EXISTS runtime_tasks_by_owner_state
                    ON runtime_tasks(
                        principal_id, state, priority DESC, created_at, task_id
                    );
                CREATE INDEX IF NOT EXISTS runtime_tasks_by_session_state
                    ON runtime_tasks(session_handle, state, created_at, task_id);
                CREATE INDEX IF NOT EXISTS runtime_task_approvals_by_owner_state
                    ON runtime_task_approvals(
                        principal_id, state, created_at, approval_id
                    );
                CREATE INDEX IF NOT EXISTS runtime_task_attempts_by_task
                    ON runtime_task_attempts(task_id, ordinal);
                CREATE INDEX IF NOT EXISTS runtime_task_tool_steps_by_task_state
                    ON runtime_task_tool_steps(task_id, state, created_at);
                CREATE INDEX IF NOT EXISTS runtime_principal_task_events_by_owner
                    ON runtime_principal_task_events(
                        principal_id, feed_event_id
                    );
                """
            )
            task_columns = {
                str(row["name"])
                for row in db.execute("PRAGMA table_info(runtime_tasks)")
            }
            if "origin" not in task_columns:
                db.execute(
                    "ALTER TABLE runtime_tasks "
                    "ADD COLUMN origin TEXT NOT NULL DEFAULT 'chat'"
                )
                db.execute(
                    """UPDATE runtime_tasks SET origin=?
                       WHERE client_request_id LIKE 'schedule:%'""",
                    (TaskOrigin.SCHEDULED.value,),
                )
                db.execute(
                    """UPDATE runtime_tasks SET origin=?
                       WHERE client_request_id LIKE 'trigger:%'""",
                    (TaskOrigin.EVENT.value,),
                )
            require_exact_table(
                db,
                "runtime_tasks",
                (
                    ("task_id", "TEXT", False, None, 1),
                    ("principal_id", "TEXT", True, None, 0),
                    ("session_handle", "TEXT", True, None, 0),
                    ("client_request_id", "TEXT", True, None, 0),
                    ("parent_task_id", "TEXT", False, None, 0),
                    ("goal", "TEXT", True, None, 0),
                    ("attachments_json", "TEXT", True, None, 0),
                    ("tools_enabled", "INTEGER", True, None, 0),
                    ("priority", "INTEGER", True, None, 0),
                    ("state", "TEXT", True, None, 0),
                    ("phase", "TEXT", True, None, 0),
                    ("attempt_count", "INTEGER", True, None, 0),
                    ("cancel_requested", "INTEGER", True, None, 0),
                    ("final_summary", "TEXT", True, None, 0),
                    ("failure_code", "TEXT", True, None, 0),
                    ("lease_owner", "TEXT", True, None, 0),
                    ("lease_expires_at", "REAL", False, None, 0),
                    ("created_at", "REAL", True, None, 0),
                    ("updated_at", "REAL", True, None, 0),
                    ("started_at", "REAL", False, None, 0),
                    ("finished_at", "REAL", False, None, 0),
                    ("next_event_seq", "INTEGER", True, None, 0),
                    ("revision", "INTEGER", True, None, 0),
                    ("origin", "TEXT", True, "'chat'", 0),
                ),
                label="Runtime Task",
            )
            require_exact_table(
                db,
                "runtime_task_events",
                (
                    ("task_id", "TEXT", True, None, 1),
                    ("event_seq", "INTEGER", True, None, 2),
                    ("event_type", "TEXT", True, None, 0),
                    ("payload_json", "TEXT", True, None, 0),
                    ("occurred_at", "REAL", True, None, 0),
                ),
                label="Runtime Task event",
            )
            require_exact_table(
                db,
                "runtime_task_attempts",
                (
                    ("attempt_id", "TEXT", False, None, 1),
                    ("task_id", "TEXT", True, None, 0),
                    ("ordinal", "INTEGER", True, None, 0),
                    ("state", "TEXT", True, None, 0),
                    ("started_at", "REAL", True, None, 0),
                    ("finished_at", "REAL", False, None, 0),
                    ("failure_code", "TEXT", True, None, 0),
                ),
                label="Runtime Task attempt",
            )
            require_exact_table(
                db,
                "runtime_principal_task_events",
                (
                    ("feed_event_id", "INTEGER", False, None, 1),
                    ("principal_id", "TEXT", True, None, 0),
                    ("task_id", "TEXT", True, None, 0),
                    ("task_event_seq", "INTEGER", True, None, 0),
                    ("occurred_at", "REAL", True, None, 0),
                ),
                label="Runtime principal Task event",
            )
            require_exact_table(
                db,
                "runtime_task_tool_steps",
                (
                    ("tool_step_id", "TEXT", False, None, 1),
                    ("task_id", "TEXT", True, None, 0),
                    ("principal_id", "TEXT", True, None, 0),
                    ("tool_call_id", "TEXT", True, None, 0),
                    ("tool_name", "TEXT", True, None, 0),
                    ("arguments_json", "TEXT", True, None, 0),
                    ("effect", "TEXT", True, None, 0),
                    ("risk", "TEXT", True, None, 0),
                    ("state", "TEXT", True, None, 0),
                    ("result_json", "TEXT", True, None, 0),
                    ("created_at", "REAL", True, None, 0),
                    ("updated_at", "REAL", True, None, 0),
                ),
                label="Runtime Task tool step",
            )
            require_exact_table(
                db,
                "runtime_task_approvals",
                (
                    ("approval_id", "TEXT", False, None, 1),
                    ("task_id", "TEXT", True, None, 0),
                    ("principal_id", "TEXT", True, None, 0),
                    ("tool_step_id", "TEXT", True, None, 0),
                    ("tool_call_id", "TEXT", True, None, 0),
                    ("tool_name", "TEXT", True, None, 0),
                    ("arguments_json", "TEXT", True, None, 0),
                    ("reason", "TEXT", True, None, 0),
                    ("state", "TEXT", True, None, 0),
                    ("request_event_seq", "INTEGER", True, None, 0),
                    ("created_at", "REAL", True, None, 0),
                    ("resolved_at", "REAL", False, None, 0),
                    ("expires_at", "REAL", False, None, 0),
                    ("resolved_by", "TEXT", True, None, 0),
                ),
                label="Runtime Task approval",
            )
            require_foreign_keys(
                db,
                "runtime_tasks",
                (
                    (
                        "runtime_tasks",
                        "parent_task_id",
                        "task_id",
                        "NO ACTION",
                        "RESTRICT",
                    ),
                    (
                        "runtime_sessions",
                        "session_handle",
                        "session_handle",
                        "NO ACTION",
                        "CASCADE",
                    ),
                ),
                label="Runtime Task",
            )
            require_foreign_keys(
                db,
                "runtime_task_events",
                (
                    (
                        "runtime_tasks",
                        "task_id",
                        "task_id",
                        "NO ACTION",
                        "CASCADE",
                    ),
                ),
                label="Runtime Task event",
            )
            require_foreign_keys(
                db,
                "runtime_task_attempts",
                (
                    (
                        "runtime_tasks",
                        "task_id",
                        "task_id",
                        "NO ACTION",
                        "CASCADE",
                    ),
                ),
                label="Runtime Task attempt",
            )
            require_foreign_keys(
                db,
                "runtime_principal_task_events",
                (
                    (
                        "runtime_tasks",
                        "task_id",
                        "task_id",
                        "NO ACTION",
                        "CASCADE",
                    ),
                ),
                label="Runtime principal Task event",
            )
            require_foreign_keys(
                db,
                "runtime_task_tool_steps",
                (
                    (
                        "runtime_tasks",
                        "task_id",
                        "task_id",
                        "NO ACTION",
                        "CASCADE",
                    ),
                ),
                label="Runtime Task tool step",
            )
            require_foreign_keys(
                db,
                "runtime_task_approvals",
                (
                    (
                        "runtime_tasks",
                        "task_id",
                        "task_id",
                        "NO ACTION",
                        "CASCADE",
                    ),
                ),
                label="Runtime Task approval",
            )
            require_index_columns(
                db,
                "runtime_tasks_by_owner_state",
                ("principal_id", "state", "priority", "created_at", "task_id"),
                label="Runtime Task",
            )
            require_index_columns(
                db,
                "runtime_tasks_by_session_state",
                ("session_handle", "state", "created_at", "task_id"),
                label="Runtime Task",
            )
            require_index_columns(
                db,
                "runtime_task_approvals_by_owner_state",
                ("principal_id", "state", "created_at", "approval_id"),
                label="Runtime Task approval",
            )
            require_index_columns(
                db,
                "runtime_task_attempts_by_task",
                ("task_id", "ordinal"),
                label="Runtime Task attempt",
            )
            require_index_columns(
                db,
                "runtime_task_tool_steps_by_task_state",
                ("task_id", "state", "created_at"),
                label="Runtime Task tool step",
            )
            require_index_columns(
                db,
                "runtime_principal_task_events_by_owner",
                ("principal_id", "feed_event_id"),
                label="Runtime principal Task event",
            )

    @staticmethod
    def _normalize_identifier(value: str, *, label: str, limit: int) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > limit:
            raise ValueError(f"{label} must contain 1-{limit} characters")
        return normalized

    @staticmethod
    def _attachments_payload(
        attachments: tuple[ArtifactAttachment, ...],
    ) -> str:
        if len(attachments) > 8:
            raise ValueError("Task accepts at most eight attachments")
        return json.dumps(
            [attachment.model_dump(mode="json") for attachment in attachments],
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _decode_attachments(raw: str) -> tuple[ArtifactAttachment, ...]:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Task attachments are corrupt") from exc
        if not isinstance(payload, list):
            raise RuntimeError("Task attachments are corrupt")
        try:
            return tuple(ArtifactAttachment.model_validate(item) for item in payload)
        except Exception as exc:
            raise RuntimeError("Task attachments are corrupt") from exc

    @classmethod
    def _record(cls, row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            task_id=str(row["task_id"]),
            principal_id=str(row["principal_id"]),
            session_handle=str(row["session_handle"]),
            client_request_id=str(row["client_request_id"]),
            origin=TaskOrigin(str(row["origin"])),
            parent_task_id=str(row["parent_task_id"] or ""),
            goal=str(row["goal"]),
            attachments=cls._decode_attachments(str(row["attachments_json"])),
            tools_enabled=bool(row["tools_enabled"]),
            priority=int(row["priority"]),
            state=TaskState(str(row["state"])),
            phase=str(row["phase"]),
            attempt_count=int(row["attempt_count"]),
            cancel_requested=bool(row["cancel_requested"]),
            final_summary=str(row["final_summary"]),
            failure_code=str(row["failure_code"]),
            lease_owner=str(row["lease_owner"]),
            lease_expires_at=(
                None
                if row["lease_expires_at"] is None
                else float(row["lease_expires_at"])
            ),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            started_at=(
                None if row["started_at"] is None else float(row["started_at"])
            ),
            finished_at=(
                None if row["finished_at"] is None else float(row["finished_at"])
            ),
            next_event_seq=int(row["next_event_seq"]),
            revision=int(row["revision"]),
        )

    @staticmethod
    def _approval_record(row: sqlite3.Row) -> TaskApprovalRecord:
        try:
            arguments = json.loads(str(row["arguments_json"]))
        except json.JSONDecodeError as exc:
            raise RuntimeError("Task approval arguments are corrupt") from exc
        if not isinstance(arguments, dict):
            raise RuntimeError("Task approval arguments are corrupt")
        return TaskApprovalRecord(
            approval_id=str(row["approval_id"]),
            task_id=str(row["task_id"]),
            principal_id=str(row["principal_id"]),
            tool_step_id=str(row["tool_step_id"]),
            tool_call_id=str(row["tool_call_id"]),
            tool_name=str(row["tool_name"]),
            arguments=arguments,
            reason=str(row["reason"]),
            state=ApprovalState(str(row["state"])),
            request_event_seq=int(row["request_event_seq"]),
            created_at=float(row["created_at"]),
            resolved_at=(
                None if row["resolved_at"] is None else float(row["resolved_at"])
            ),
            expires_at=(
                None if row["expires_at"] is None else float(row["expires_at"])
            ),
            resolved_by=str(row["resolved_by"]),
        )

    @staticmethod
    def _attempt_id(task_id: str, ordinal: int) -> str:
        return hashlib.sha256(
            f"{task_id}\0{ordinal}".encode("utf-8")
        ).hexdigest()[:32]

    @staticmethod
    def _attempt_record(row: sqlite3.Row) -> TaskAttemptRecord:
        return TaskAttemptRecord(
            attempt_id=str(row["attempt_id"]),
            task_id=str(row["task_id"]),
            ordinal=int(row["ordinal"]),
            state=TaskAttemptState(str(row["state"])),
            started_at=float(row["started_at"]),
            finished_at=(
                None if row["finished_at"] is None else float(row["finished_at"])
            ),
            failure_code=str(row["failure_code"]),
        )

    @staticmethod
    def _json_object(raw: str, *, label: str) -> dict[str, object]:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{label} is corrupt") from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"{label} is corrupt")
        return value

    @classmethod
    def _tool_step_record(cls, row: sqlite3.Row) -> TaskToolStepRecord:
        return TaskToolStepRecord(
            tool_step_id=str(row["tool_step_id"]),
            task_id=str(row["task_id"]),
            principal_id=str(row["principal_id"]),
            tool_call_id=str(row["tool_call_id"]),
            tool_name=str(row["tool_name"]),
            arguments=cls._json_object(
                str(row["arguments_json"]),
                label="Task tool step arguments",
            ),
            effect=str(row["effect"]),
            risk=str(row["risk"]),
            state=TaskToolStepState(str(row["state"])),
            result=cls._json_object(
                str(row["result_json"]),
                label="Task tool step result",
            ),
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
            raise TaskNotFoundError("Session not found")

    @staticmethod
    def _owned_task(
        db: sqlite3.Connection,
        principal_id: str,
        task_id: str,
    ) -> sqlite3.Row:
        row = db.execute(
            """SELECT * FROM runtime_tasks
               WHERE task_id=? AND principal_id=?""",
            (task_id, principal_id),
        ).fetchone()
        if row is None:
            raise TaskNotFoundError("Task not found")
        return row

    @staticmethod
    def _owned_approval(
        db: sqlite3.Connection,
        principal_id: str,
        approval_id: str,
    ) -> sqlite3.Row:
        row = db.execute(
            """SELECT * FROM runtime_task_approvals
               WHERE approval_id=? AND principal_id=?""",
            (approval_id, principal_id),
        ).fetchone()
        if row is None:
            raise TaskNotFoundError("Approval not found")
        return row

    @staticmethod
    def _event_json(payload: TaskEventPayload) -> str:
        encoded = payload.model_dump_json()
        if len(encoded.encode("utf-8")) > _MAX_EVENT_BYTES:
            raise ValueError("Task event payload exceeds the size limit")
        return encoded

    @classmethod
    def _append_event(
        cls,
        db: sqlite3.Connection,
        row: sqlite3.Row,
        event_type: TaskEventType,
        payload: TaskEventPayload,
        occurred_at: float,
    ) -> TaskEvent:
        event_seq = int(row["next_event_seq"])
        db.execute(
            """INSERT INTO runtime_task_events(
                   task_id, event_seq, event_type, payload_json, occurred_at
               ) VALUES (?, ?, ?, ?, ?)""",
            (
                str(row["task_id"]),
                event_seq,
                event_type,
                cls._event_json(payload),
                occurred_at,
            ),
        )
        db.execute(
            """INSERT INTO runtime_principal_task_events(
                   principal_id, task_id, task_event_seq, occurred_at
               ) VALUES (?, ?, ?, ?)""",
            (
                str(row["principal_id"]),
                str(row["task_id"]),
                event_seq,
                occurred_at,
            ),
        )
        return TaskEvent(
            task_id=str(row["task_id"]),
            event_seq=event_seq,
            event_type=event_type,
            payload=payload,
            occurred_at=occurred_at,
        )

    @staticmethod
    def _finish_active_attempt(
        db: sqlite3.Connection,
        task_id: str,
        state: TaskAttemptState,
        now: float,
        *,
        failure_code: str = "",
    ) -> None:
        db.execute(
            """UPDATE runtime_task_attempts SET
                   state=?, finished_at=?, failure_code=?
               WHERE task_id=? AND state=?""",
            (
                state.value,
                now,
                failure_code,
                task_id,
                TaskAttemptState.RUNNING.value,
            ),
        )

    def create(
        self,
        scope: RuntimeScope,
        *,
        client_request_id: str,
        goal: str,
        attachments: tuple[ArtifactAttachment, ...] = (),
        tools_enabled: bool = True,
        priority: int = 0,
        parent_task_id: str = "",
        origin: TaskOrigin = TaskOrigin.CHAT,
    ) -> tuple[TaskRecord, bool]:
        request_id = self._normalize_identifier(
            client_request_id,
            label="client_request_id",
            limit=128,
        )
        normalized_goal = goal.strip()
        if not normalized_goal or len(normalized_goal) > 200_000:
            raise ValueError("Task goal must contain 1-200000 characters")
        if not 0 <= priority <= 9:
            raise ValueError("Task priority must be between 0 and 9")
        normalized_parent = parent_task_id.strip()
        attachment_json = self._attachments_payload(attachments)
        now = self._clock()

        for _ in range(5):
            try:
                with self._connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    self._owned_session(db, scope)
                    existing = db.execute(
                        """SELECT * FROM runtime_tasks
                           WHERE principal_id=? AND client_request_id=?""",
                        (scope.principal_id, request_id),
                    ).fetchone()
                    if existing is not None:
                        same_request = (
                            str(existing["session_handle"]) == scope.session_handle
                            and str(existing["goal"]) == normalized_goal
                            and str(existing["attachments_json"]) == attachment_json
                            and bool(existing["tools_enabled"]) is bool(tools_enabled)
                            and int(existing["priority"]) == priority
                            and str(existing["origin"]) == origin.value
                            and str(existing["parent_task_id"] or "")
                            == normalized_parent
                        )
                        if not same_request:
                            raise TaskIdempotencyConflictError(
                                "client_request_id already belongs to another Task request"
                            )
                        return self._record(existing), False
                    active_states = tuple(
                        state.value
                        for state in TaskState
                        if state not in TERMINAL_TASK_STATES
                    )
                    placeholders = ",".join("?" for _ in active_states)
                    active_global = int(
                        db.execute(
                            f"""SELECT COUNT(*) FROM runtime_tasks
                                WHERE state IN ({placeholders})""",
                            active_states,
                        ).fetchone()[0]
                    )
                    if active_global >= self._max_active_tasks:
                        raise TaskCapacityError("Global active Task limit reached")
                    active_for_principal = int(
                        db.execute(
                            f"""SELECT COUNT(*) FROM runtime_tasks
                                WHERE principal_id=?
                                  AND state IN ({placeholders})""",
                            (scope.principal_id, *active_states),
                        ).fetchone()[0]
                    )
                    if active_for_principal >= self._max_active_tasks_per_principal:
                        raise TaskCapacityError(
                            "Per-principal active Task limit reached"
                        )
                    if normalized_parent:
                        self._owned_task(db, scope.principal_id, normalized_parent)
                    task_id = self._normalize_identifier(
                        self._task_id_factory(),
                        label="task_id",
                        limit=128,
                    )
                    db.execute(
                        """INSERT INTO runtime_tasks(
                               task_id, principal_id, session_handle,
                               client_request_id, origin, parent_task_id, goal,
                               attachments_json, tools_enabled, priority, state,
                               phase, attempt_count, cancel_requested,
                               final_summary, failure_code, lease_owner,
                               lease_expires_at, created_at, updated_at,
                               started_at, finished_at, next_event_seq, revision
                           ) VALUES (
                               ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               ?, ?, ?, ?, ?, ?, ?, ?
                           )""",
                        (
                            task_id,
                            scope.principal_id,
                            scope.session_handle,
                            request_id,
                            origin.value,
                            normalized_parent or None,
                            normalized_goal,
                            attachment_json,
                            int(tools_enabled),
                            priority,
                            TaskState.QUEUED.value,
                            "",
                            0,
                            0,
                            "",
                            "",
                            "",
                            None,
                            now,
                            now,
                            None,
                            None,
                            1,
                            0,
                        ),
                    )
                    row = self._owned_task(db, scope.principal_id, task_id)
                    self._append_event(
                        db,
                        row,
                        "task_created",
                        TaskEventPayload(state=TaskState.QUEUED),
                        now,
                    )
                    db.execute(
                        """UPDATE runtime_tasks
                           SET next_event_seq=2 WHERE task_id=?""",
                        (task_id,),
                    )
                    created = self._owned_task(db, scope.principal_id, task_id)
                    return self._record(created), True
            except sqlite3.IntegrityError as exc:
                if "runtime_tasks.task_id" in str(exc):
                    continue
                raise
        raise RuntimeError("Could not allocate a unique Task ID")

    def get(self, principal_id: str, task_id: str) -> TaskRecord:
        principal = self._normalize_identifier(
            principal_id,
            label="principal_id",
            limit=256,
        )
        normalized_task_id = self._normalize_identifier(
            task_id,
            label="task_id",
            limit=128,
        )
        with self._connect() as db:
            return self._record(self._owned_task(db, principal, normalized_task_id))

    @staticmethod
    def _encode_cursor(created_at: float, task_id: str) -> str:
        raw = json.dumps(
            [created_at, task_id],
            separators=(",", ":"),
        ).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @classmethod
    def _decode_cursor(cls, cursor: str) -> tuple[float, str]:
        normalized = cursor.strip()
        if not normalized or len(normalized) > 512:
            raise ValueError("Task cursor is invalid")
        try:
            padded = normalized + "=" * (-len(normalized) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
            if not isinstance(payload, list) or len(payload) != 2:
                raise ValueError
            created_at = float(payload[0])
            task_id = cls._normalize_identifier(
                str(payload[1]),
                label="cursor task_id",
                limit=128,
            )
        except (ValueError, TypeError, UnicodeDecodeError, binascii.Error) as exc:
            raise ValueError("Task cursor is invalid") from exc
        if not math.isfinite(created_at) or created_at < 0:
            raise ValueError("Task cursor is invalid")
        return created_at, task_id

    def list_tasks(
        self,
        principal_id: str,
        *,
        session_handle: str = "",
        state: TaskState | None = None,
        origins: tuple[TaskOrigin, ...] = (),
        limit: int = 50,
        cursor: str = "",
    ) -> tuple[tuple[TaskRecord, ...], str]:
        principal = self._normalize_identifier(
            principal_id,
            label="principal_id",
            limit=256,
        )
        if not 1 <= limit <= 100:
            raise ValueError("Task list limit must be between 1 and 100")
        session = session_handle.strip()
        if session:
            session = self._normalize_identifier(
                session,
                label="session_handle",
                limit=256,
            )
        clauses = ["principal_id=?"]
        parameters: list[object] = [principal]
        if session:
            clauses.append("session_handle=?")
            parameters.append(session)
        if state is not None:
            clauses.append("state=?")
            parameters.append(state.value)
        if origins:
            origin_values = tuple(dict.fromkeys(origin.value for origin in origins))
            placeholders = ",".join("?" for _ in origin_values)
            clauses.append(f"origin IN ({placeholders})")
            parameters.extend(origin_values)
        if cursor:
            created_at, cursor_task_id = self._decode_cursor(cursor)
            clauses.append("(created_at<? OR (created_at=? AND task_id<?))")
            parameters.extend((created_at, created_at, cursor_task_id))
        parameters.append(limit + 1)
        query = (
            "SELECT * FROM runtime_tasks WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at DESC, task_id DESC LIMIT ?"
        )
        with self._connect() as db:
            rows = db.execute(query, tuple(parameters)).fetchall()
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        tasks = tuple(self._record(row) for row in page_rows)
        next_cursor = ""
        if has_more and page_rows:
            last = page_rows[-1]
            next_cursor = self._encode_cursor(
                float(last["created_at"]),
                str(last["task_id"]),
            )
        return tasks, next_cursor

    def list_attempts(
        self,
        principal_id: str,
        task_id: str,
    ) -> tuple[TaskAttemptRecord, ...]:
        task = self.get(principal_id, task_id)
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM runtime_task_attempts
                   WHERE task_id=? ORDER BY ordinal""",
                (task.task_id,),
            ).fetchall()
        return tuple(self._attempt_record(row) for row in rows)

    def begin_tool_step(
        self,
        principal_id: str,
        task_id: str,
        *,
        tool_step_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, object],
        effect: str,
        risk: str,
    ) -> tuple[TaskToolStepRecord, bool]:
        principal = self._normalize_identifier(
            principal_id,
            label="principal_id",
            limit=256,
        )
        normalized_task_id = self._normalize_identifier(
            task_id,
            label="task_id",
            limit=128,
        )
        normalized_step_id = self._normalize_identifier(
            tool_step_id,
            label="tool_step_id",
            limit=128,
        )
        normalized_call_id = self._normalize_identifier(
            tool_call_id,
            label="tool_call_id",
            limit=256,
        )
        normalized_tool_name = self._normalize_identifier(
            tool_name,
            label="tool_name",
            limit=256,
        )
        if effect not in {
            "read_only",
            "local_write",
            "external_side_effect",
            "desktop_control",
            "unknown",
        }:
            raise ValueError("Task tool effect is invalid")
        if risk not in {"low", "medium", "high"}:
            raise ValueError("Task tool risk is invalid")
        arguments_json = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(arguments_json.encode("utf-8")) > _MAX_EVENT_BYTES:
            raise ValueError("Task tool arguments exceed the size limit")
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = self._owned_task(db, principal, normalized_task_id)
            if TaskState(str(task["state"])) is not TaskState.RUNNING:
                raise TaskTransitionError(
                    "Task must be running before tool commit"
                )
            existing = db.execute(
                """SELECT * FROM runtime_task_tool_steps
                   WHERE tool_step_id=? AND task_id=? AND principal_id=?""",
                (normalized_step_id, normalized_task_id, principal),
            ).fetchone()
            if existing is not None:
                record = self._tool_step_record(existing)
                if (
                    record.tool_call_id != normalized_call_id
                    or record.tool_name != normalized_tool_name
                    or record.arguments != arguments
                    or record.effect != effect
                    or record.risk != risk
                ):
                    raise TaskIdempotencyConflictError(
                        "tool_step_id already belongs to another tool commit"
                    )
                return record, False
            db.execute(
                """INSERT INTO runtime_task_tool_steps(
                       tool_step_id, task_id, principal_id, tool_call_id,
                       tool_name, arguments_json, effect, risk, state,
                       result_json, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    normalized_step_id,
                    normalized_task_id,
                    principal,
                    normalized_call_id,
                    normalized_tool_name,
                    arguments_json,
                    effect,
                    risk,
                    TaskToolStepState.COMMITTING.value,
                    "{}",
                    now,
                    now,
                ),
            )
            row = db.execute(
                """SELECT * FROM runtime_task_tool_steps
                   WHERE tool_step_id=?""",
                (normalized_step_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Task tool step could not be persisted")
            return self._tool_step_record(row), True

    def finish_tool_step(
        self,
        principal_id: str,
        task_id: str,
        tool_step_id: str,
        *,
        state: TaskToolStepState,
        result: dict[str, object],
    ) -> tuple[TaskToolStepRecord, bool]:
        if state not in {
            TaskToolStepState.COMPLETED,
            TaskToolStepState.FAILED,
        }:
            raise ValueError("Task tool step may finish only completed or failed")
        principal = self._normalize_identifier(
            principal_id,
            label="principal_id",
            limit=256,
        )
        normalized_task_id = self._normalize_identifier(
            task_id,
            label="task_id",
            limit=128,
        )
        normalized_step_id = self._normalize_identifier(
            tool_step_id,
            label="tool_step_id",
            limit=128,
        )
        result_json = json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(result_json.encode("utf-8")) > _MAX_EVENT_BYTES:
            raise ValueError("Task tool result exceeds the size limit")
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._owned_task(db, principal, normalized_task_id)
            row = db.execute(
                """SELECT * FROM runtime_task_tool_steps
                   WHERE tool_step_id=? AND task_id=? AND principal_id=?""",
                (normalized_step_id, normalized_task_id, principal),
            ).fetchone()
            if row is None:
                raise TaskNotFoundError("Task tool step not found")
            current = self._tool_step_record(row)
            if current.state is not TaskToolStepState.COMMITTING:
                if current.state is state and current.result == result:
                    return current, False
                raise TaskTransitionError("Task tool step is already terminal")
            db.execute(
                """UPDATE runtime_task_tool_steps SET
                       state=?, result_json=?, updated_at=?
                   WHERE tool_step_id=? AND state=?""",
                (
                    state.value,
                    result_json,
                    now,
                    normalized_step_id,
                    TaskToolStepState.COMMITTING.value,
                ),
            )
            updated = db.execute(
                """SELECT * FROM runtime_task_tool_steps
                   WHERE tool_step_id=?""",
                (normalized_step_id,),
            ).fetchone()
            if updated is None:
                raise RuntimeError("Task tool step disappeared")
            return self._tool_step_record(updated), True

    def pause_for_unknown_tool_outcome(
        self,
        principal_id: str,
        task_id: str,
        *,
        reason: str,
    ) -> tuple[TaskRecord, TaskEvent]:
        """Atomically quarantine unfinished tool commits and pause their Task."""

        principal = self._normalize_identifier(
            principal_id,
            label="principal_id",
            limit=256,
        )
        normalized_task_id = self._normalize_identifier(
            task_id,
            label="task_id",
            limit=128,
        )
        normalized_reason = reason.strip()
        if not normalized_reason or len(normalized_reason) > 2000:
            raise ValueError(
                "Unknown tool outcome reason must contain 1-2000 characters"
            )
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._owned_task(db, principal, normalized_task_id)
            current = TaskState(str(row["state"]))
            if current is not TaskState.RUNNING:
                raise TaskTransitionError(
                    "Only a running Task can report an unknown tool outcome"
                )
            db.execute(
                """UPDATE runtime_task_tool_steps SET
                       state=?, updated_at=?
                   WHERE task_id=? AND principal_id=? AND state=?""",
                (
                    TaskToolStepState.OUTCOME_UNKNOWN.value,
                    now,
                    normalized_task_id,
                    principal,
                    TaskToolStepState.COMMITTING.value,
                ),
            )
            self._finish_active_attempt(
                db,
                normalized_task_id,
                TaskAttemptState.INTERRUPTED,
                now,
                failure_code="tool_outcome_unknown",
            )
            event = self._append_event(
                db,
                row,
                "state_changed",
                TaskEventPayload(
                    previous_state=TaskState.RUNNING,
                    state=TaskState.PAUSED,
                    phase="outcome_unknown",
                    reason=normalized_reason,
                ),
                now,
            )
            db.execute(
                """UPDATE runtime_tasks SET
                       state=?, phase=?, lease_owner='', lease_expires_at=NULL,
                       updated_at=?, next_event_seq=?, revision=revision+1
                   WHERE task_id=?""",
                (
                    TaskState.PAUSED.value,
                    "outcome_unknown",
                    now,
                    event.event_seq + 1,
                    normalized_task_id,
                ),
            )
            updated = self._owned_task(db, principal, normalized_task_id)
            return self._record(updated), event

    def list_tool_steps(
        self,
        principal_id: str,
        task_id: str,
    ) -> tuple[TaskToolStepRecord, ...]:
        task = self.get(principal_id, task_id)
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM runtime_task_tool_steps
                   WHERE task_id=? ORDER BY created_at, tool_step_id""",
                (task.task_id,),
            ).fetchall()
        return tuple(self._tool_step_record(row) for row in rows)

    def list_events(
        self,
        principal_id: str,
        task_id: str,
        *,
        after_seq: int = 0,
        limit: int = 200,
    ) -> tuple[TaskEvent, ...]:
        if after_seq < 0:
            raise ValueError("after_seq must not be negative")
        if not 1 <= limit <= 1000:
            raise ValueError("Task event limit must be between 1 and 1000")
        task = self.get(principal_id, task_id)
        with self._connect() as db:
            rows = db.execute(
                """SELECT event_seq, event_type, payload_json, occurred_at
                   FROM runtime_task_events
                   WHERE task_id=? AND event_seq>?
                   ORDER BY event_seq LIMIT ?""",
                (task.task_id, after_seq, limit),
            ).fetchall()
        events: list[TaskEvent] = []
        for row in rows:
            try:
                payload = TaskEventPayload.model_validate_json(row["payload_json"])
                event = TaskEvent(
                    task_id=task.task_id,
                    event_seq=int(row["event_seq"]),
                    event_type=str(row["event_type"]),
                    payload=payload,
                    occurred_at=float(row["occurred_at"]),
                )
            except Exception as exc:
                raise RuntimeError("Task event journal is corrupt") from exc
            events.append(event)
        return tuple(events)

    def list_principal_events(
        self,
        principal_id: str,
        *,
        after_id: int = 0,
        limit: int = 200,
    ) -> tuple[PrincipalTaskEvent, ...]:
        principal = self._normalize_identifier(
            principal_id,
            label="principal_id",
            limit=256,
        )
        if after_id < 0:
            raise ValueError("after_id must not be negative")
        if not 1 <= limit <= 1000:
            raise ValueError("Principal Task event limit must be between 1 and 1000")
        with self._connect() as db:
            rows = db.execute(
                """SELECT feed.feed_event_id, feed.principal_id,
                          event.task_id, event.event_seq, event.event_type,
                          event.payload_json, event.occurred_at
                   FROM runtime_principal_task_events AS feed
                   JOIN runtime_task_events AS event
                     ON event.task_id=feed.task_id
                    AND event.event_seq=feed.task_event_seq
                   WHERE feed.principal_id=? AND feed.feed_event_id>?
                   ORDER BY feed.feed_event_id LIMIT ?""",
                (principal, after_id, limit),
            ).fetchall()
        feed_events: list[PrincipalTaskEvent] = []
        for row in rows:
            try:
                event = TaskEvent(
                    task_id=str(row["task_id"]),
                    event_seq=int(row["event_seq"]),
                    event_type=str(row["event_type"]),
                    payload=TaskEventPayload.model_validate_json(row["payload_json"]),
                    occurred_at=float(row["occurred_at"]),
                )
                feed_event = PrincipalTaskEvent(
                    feed_event_id=int(row["feed_event_id"]),
                    principal_id=str(row["principal_id"]),
                    event=event,
                )
            except Exception as exc:
                raise RuntimeError("Principal Task event feed is corrupt") from exc
            feed_events.append(feed_event)
        return tuple(feed_events)

    def append_event(
        self,
        principal_id: str,
        task_id: str,
        event_type: TaskEventType,
        payload: TaskEventPayload,
    ) -> TaskEvent:
        principal = self._normalize_identifier(
            principal_id,
            label="principal_id",
            limit=256,
        )
        normalized_task_id = self._normalize_identifier(
            task_id,
            label="task_id",
            limit=128,
        )
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._owned_task(db, principal, normalized_task_id)
            if TaskState(str(row["state"])) in TERMINAL_TASK_STATES:
                raise TaskTransitionError("Terminal Task cannot accept new events")
            event = self._append_event(db, row, event_type, payload, now)
            db.execute(
                """UPDATE runtime_tasks
                   SET next_event_seq=?, updated_at=?, revision=revision+1
                   WHERE task_id=?""",
                (event.event_seq + 1, now, normalized_task_id),
            )
            return event

    def request_cancel(
        self,
        principal_id: str,
        task_id: str,
        *,
        reason: str = "",
    ) -> tuple[TaskCancelResult, TaskEvent | None]:
        principal = self._normalize_identifier(
            principal_id,
            label="principal_id",
            limit=256,
        )
        normalized_task_id = self._normalize_identifier(
            task_id,
            label="task_id",
            limit=128,
        )
        normalized_reason = reason.strip()
        if len(normalized_reason) > 2000:
            raise ValueError("Task cancellation reason exceeds 2000 characters")
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._owned_task(db, principal, normalized_task_id)
            state = TaskState(str(row["state"]))
            if state in TERMINAL_TASK_STATES:
                return TaskCancelResult(accepted=True, state=state), None
            if bool(row["cancel_requested"]):
                return TaskCancelResult(accepted=True, state=state), None
            if state in {
                TaskState.QUEUED,
                TaskState.WAITING_APPROVAL,
                TaskState.PAUSED,
            }:
                if state is TaskState.WAITING_APPROVAL:
                    db.execute(
                        """UPDATE runtime_task_approvals SET
                               state=?, resolved_at=?, resolved_by=?
                           WHERE task_id=? AND state=?""",
                        (
                            ApprovalState.CANCELLED.value,
                            now,
                            "task_cancelled",
                            normalized_task_id,
                            ApprovalState.PENDING.value,
                        ),
                    )
                    self._finish_active_attempt(
                        db,
                        normalized_task_id,
                        TaskAttemptState.CANCELLED,
                        now,
                        failure_code="cancelled",
                    )
                event = self._append_event(
                    db,
                    row,
                    "cancelled",
                    TaskEventPayload(
                        previous_state=state,
                        state=TaskState.CANCELLED,
                        reason=normalized_reason,
                    ),
                    now,
                )
                db.execute(
                    """UPDATE runtime_tasks SET
                           state=?, cancel_requested=1, lease_owner='',
                           lease_expires_at=NULL, updated_at=?, finished_at=?,
                           next_event_seq=?, revision=revision+1
                       WHERE task_id=?""",
                    (
                        TaskState.CANCELLED.value,
                        now,
                        now,
                        event.event_seq + 1,
                        normalized_task_id,
                    ),
                )
                return (
                    TaskCancelResult(
                        accepted=True,
                        state=TaskState.CANCELLED,
                    ),
                    event,
                )
            event = self._append_event(
                db,
                row,
                "warning",
                TaskEventPayload(
                    state=state,
                    reason=normalized_reason or "Cancellation requested",
                ),
                now,
            )
            db.execute(
                """UPDATE runtime_tasks SET
                       cancel_requested=1, updated_at=?, next_event_seq=?,
                       revision=revision+1
                   WHERE task_id=?""",
                (now, event.event_seq + 1, normalized_task_id),
            )
            return TaskCancelResult(accepted=True, state=state), event

    def request_pause(
        self,
        principal_id: str,
        task_id: str,
        *,
        reason: str = "",
    ) -> tuple[TaskPauseResult, TaskEvent | None]:
        """Persist a pause request and stop immediately only at a safe boundary."""

        principal = self._normalize_identifier(
            principal_id,
            label="principal_id",
            limit=256,
        )
        normalized_task_id = self._normalize_identifier(
            task_id,
            label="task_id",
            limit=128,
        )
        normalized_reason = reason.strip()
        if len(normalized_reason) > 2000:
            raise ValueError("Task pause reason exceeds 2000 characters")
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._owned_task(db, principal, normalized_task_id)
            state = TaskState(str(row["state"]))
            if state in TERMINAL_TASK_STATES:
                raise TaskTransitionError("Terminal Task cannot be paused")
            if state is TaskState.PAUSED:
                return TaskPauseResult(accepted=True, state=state), None
            if state is TaskState.RUNNING:
                if str(row["phase"]) == "pause_requested":
                    return TaskPauseResult(accepted=True, state=state), None
                event = self._append_event(
                    db,
                    row,
                    "warning",
                    TaskEventPayload(
                        state=state,
                        phase="pause_requested",
                        reason=normalized_reason or "Pause requested",
                    ),
                    now,
                )
                db.execute(
                    """UPDATE runtime_tasks SET
                           phase=?, updated_at=?, next_event_seq=?, revision=revision+1
                       WHERE task_id=?""",
                    (
                        "pause_requested",
                        now,
                        event.event_seq + 1,
                        normalized_task_id,
                    ),
                )
                return TaskPauseResult(accepted=True, state=state), event
            if state is TaskState.WAITING_APPROVAL:
                db.execute(
                    """UPDATE runtime_task_approvals SET
                           state=?, resolved_at=?, resolved_by=?
                       WHERE task_id=? AND state=?""",
                    (
                        ApprovalState.CANCELLED.value,
                        now,
                        "task_paused",
                        normalized_task_id,
                        ApprovalState.PENDING.value,
                    ),
                )
                self._finish_active_attempt(
                    db,
                    normalized_task_id,
                    TaskAttemptState.INTERRUPTED,
                    now,
                    failure_code="paused",
                )
            event = self._append_event(
                db,
                row,
                "state_changed",
                TaskEventPayload(
                    previous_state=state,
                    state=TaskState.PAUSED,
                    phase="manual_pause",
                    reason=normalized_reason or "Task paused",
                ),
                now,
            )
            db.execute(
                """UPDATE runtime_tasks SET
                       state=?, phase=?, lease_owner='', lease_expires_at=NULL,
                       updated_at=?, next_event_seq=?, revision=revision+1
                   WHERE task_id=?""",
                (
                    TaskState.PAUSED.value,
                    "manual_pause",
                    now,
                    event.event_seq + 1,
                    normalized_task_id,
                ),
            )
            return (
                TaskPauseResult(accepted=True, state=TaskState.PAUSED),
                event,
            )

    def resume(
        self,
        principal_id: str,
        task_id: str,
        *,
        reason: str = "",
        acknowledge_outcome_unknown: bool = False,
    ) -> tuple[TaskRecord, TaskEvent]:
        principal = self._normalize_identifier(
            principal_id,
            label="principal_id",
            limit=256,
        )
        normalized_task_id = self._normalize_identifier(
            task_id,
            label="task_id",
            limit=128,
        )
        normalized_reason = reason.strip()
        if len(normalized_reason) > 2000:
            raise ValueError("Task resume reason exceeds 2000 characters")
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._owned_task(db, principal, normalized_task_id)
            state = TaskState(str(row["state"]))
            if state is not TaskState.PAUSED:
                raise TaskTransitionError("Only a paused Task can be resumed")
            if (
                str(row["phase"]) == "outcome_unknown"
                and not acknowledge_outcome_unknown
            ):
                raise TaskTransitionError(
                    "Unknown tool outcome must be explicitly acknowledged"
                )
            event = self._append_event(
                db,
                row,
                "state_changed",
                TaskEventPayload(
                    previous_state=TaskState.PAUSED,
                    state=TaskState.QUEUED,
                    reason=normalized_reason or "Task explicitly resumed",
                ),
                now,
            )
            db.execute(
                """UPDATE runtime_tasks SET
                       state=?, cancel_requested=0, lease_owner='',
                       lease_expires_at=NULL, phase='', updated_at=?, next_event_seq=?,
                       revision=revision+1
                   WHERE task_id=?""",
                (
                    TaskState.QUEUED.value,
                    now,
                    event.event_seq + 1,
                    normalized_task_id,
                ),
            )
            updated = self._owned_task(db, principal, normalized_task_id)
            return self._record(updated), event

    def request_approval(
        self,
        principal_id: str,
        task_id: str,
        *,
        tool_step_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, object],
        reason: str = "",
        expires_at: float | None = None,
    ) -> tuple[TaskApprovalRecord, TaskEvent, bool]:
        principal = self._normalize_identifier(
            principal_id,
            label="principal_id",
            limit=256,
        )
        normalized_task_id = self._normalize_identifier(
            task_id,
            label="task_id",
            limit=128,
        )
        normalized_step_id = self._normalize_identifier(
            tool_step_id,
            label="tool_step_id",
            limit=128,
        )
        normalized_call_id = self._normalize_identifier(
            tool_call_id,
            label="tool_call_id",
            limit=256,
        )
        normalized_tool_name = self._normalize_identifier(
            tool_name,
            label="tool_name",
            limit=256,
        )
        normalized_reason = reason.strip()
        if len(normalized_reason) > 2000:
            raise ValueError("Task approval reason exceeds 2000 characters")
        arguments_json = json.dumps(
            arguments,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(arguments_json.encode("utf-8")) > _MAX_EVENT_BYTES:
            raise ValueError("Task approval arguments exceed the size limit")
        now = self._clock()
        for _ in range(5):
            approval_id = self._normalize_identifier(
                self._approval_id_factory(),
                label="approval_id",
                limit=128,
            )
            try:
                with self._connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    task = self._owned_task(db, principal, normalized_task_id)
                    existing = db.execute(
                        """SELECT * FROM runtime_task_approvals
                           WHERE task_id=? AND tool_step_id=?""",
                        (normalized_task_id, normalized_step_id),
                    ).fetchone()
                    if existing is not None:
                        approval = self._approval_record(existing)
                        if (
                            approval.tool_call_id != normalized_call_id
                            or approval.tool_name != normalized_tool_name
                            or approval.arguments != arguments
                            or approval.reason != normalized_reason
                        ):
                            raise TaskIdempotencyConflictError(
                                "tool_step_id already belongs to another approval"
                            )
                        requested_event = db.execute(
                            """SELECT event_seq, event_type, payload_json, occurred_at
                               FROM runtime_task_events
                               WHERE task_id=? AND event_seq=?""",
                            (normalized_task_id, approval.request_event_seq),
                        ).fetchone()
                        if requested_event is None:
                            raise RuntimeError("Task approval event is missing")
                        event = TaskEvent(
                            task_id=normalized_task_id,
                            event_seq=int(requested_event["event_seq"]),
                            event_type="approval_requested",
                            payload=TaskEventPayload.model_validate_json(
                                requested_event["payload_json"]
                            ),
                            occurred_at=float(requested_event["occurred_at"]),
                        )
                        return approval, event, False
                    state = TaskState(str(task["state"]))
                    if state is not TaskState.RUNNING:
                        raise TaskTransitionError(
                            "Task must be running before requesting approval"
                        )
                    payload = TaskEventPayload(
                        previous_state=TaskState.RUNNING,
                        state=TaskState.WAITING_APPROVAL,
                        approval_id=approval_id,
                        tool_step_id=normalized_step_id,
                        tool_call_id=normalized_call_id,
                        tool_name=normalized_tool_name,
                        tool_args=arguments,
                        reason=normalized_reason,
                    )
                    event = self._append_event(
                        db,
                        task,
                        "approval_requested",
                        payload,
                        now,
                    )
                    db.execute(
                        """INSERT INTO runtime_task_approvals(
                               approval_id, task_id, principal_id, tool_step_id,
                               tool_call_id, tool_name, arguments_json, reason,
                               state, request_event_seq, created_at, resolved_at,
                               expires_at, resolved_by
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            approval_id,
                            normalized_task_id,
                            principal,
                            normalized_step_id,
                            normalized_call_id,
                            normalized_tool_name,
                            arguments_json,
                            normalized_reason,
                            ApprovalState.PENDING.value,
                            event.event_seq,
                            now,
                            None,
                            expires_at,
                            "",
                        ),
                    )
                    db.execute(
                        """UPDATE runtime_tasks SET
                               state=?, updated_at=?, next_event_seq=?,
                               revision=revision+1
                           WHERE task_id=?""",
                        (
                            TaskState.WAITING_APPROVAL.value,
                            now,
                            event.event_seq + 1,
                            normalized_task_id,
                        ),
                    )
                    approval_row = self._owned_approval(db, principal, approval_id)
                    return self._approval_record(approval_row), event, True
            except sqlite3.IntegrityError as exc:
                if "runtime_task_approvals.approval_id" in str(exc):
                    continue
                raise
        raise RuntimeError("Could not allocate a unique approval ID")

    def resolve_approval(
        self,
        principal_id: str,
        approval_id: str,
        *,
        approved: bool,
        resume_state: TaskState = TaskState.RUNNING,
        resolved_by: str = "",
    ) -> tuple[TaskApprovalRecord, TaskEvent | None, bool]:
        if resume_state not in {TaskState.RUNNING, TaskState.QUEUED}:
            raise ValueError("Approval may resume only a running or queued Task")
        principal = self._normalize_identifier(
            principal_id,
            label="principal_id",
            limit=256,
        )
        normalized_approval_id = self._normalize_identifier(
            approval_id,
            label="approval_id",
            limit=128,
        )
        resolver = resolved_by.strip()
        if len(resolver) > 256:
            raise ValueError("Approval resolver exceeds 256 characters")
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            approval_row = self._owned_approval(
                db,
                principal,
                normalized_approval_id,
            )
            approval = self._approval_record(approval_row)
            if approval.state is not ApprovalState.PENDING:
                return approval, None, False
            task = self._owned_task(db, principal, approval.task_id)
            if TaskState(str(task["state"])) is not TaskState.WAITING_APPROVAL:
                raise TaskTransitionError(
                    "Pending approval does not belong to a waiting Task"
                )
            resolved_state = (
                ApprovalState.APPROVED if approved else ApprovalState.DENIED
            )
            db.execute(
                """UPDATE runtime_task_approvals SET
                       state=?, resolved_at=?, resolved_by=?
                   WHERE approval_id=? AND state=?""",
                (
                    resolved_state.value,
                    now,
                    resolver,
                    normalized_approval_id,
                    ApprovalState.PENDING.value,
                ),
            )
            payload = TaskEventPayload(
                previous_state=TaskState.WAITING_APPROVAL,
                state=resume_state,
                approval_id=normalized_approval_id,
                tool_step_id=approval.tool_step_id,
                tool_call_id=approval.tool_call_id,
                tool_name=approval.tool_name,
                reason=resolved_state.value,
            )
            event = self._append_event(
                db,
                task,
                "approval_resolved",
                payload,
                now,
            )
            lease_owner = str(task["lease_owner"])
            lease_expires_at = task["lease_expires_at"]
            if resume_state is TaskState.QUEUED:
                lease_owner = ""
                lease_expires_at = None
                self._finish_active_attempt(
                    db,
                    approval.task_id,
                    TaskAttemptState.INTERRUPTED,
                    now,
                    failure_code="approval_wait_interrupted",
                )
            db.execute(
                """UPDATE runtime_tasks SET
                       state=?, lease_owner=?, lease_expires_at=?, updated_at=?,
                       next_event_seq=?, revision=revision+1
                   WHERE task_id=?""",
                (
                    resume_state.value,
                    lease_owner,
                    lease_expires_at,
                    now,
                    event.event_seq + 1,
                    approval.task_id,
                ),
            )
            updated = self._owned_approval(
                db,
                principal,
                normalized_approval_id,
            )
            return self._approval_record(updated), event, True

    def get_approval(
        self,
        principal_id: str,
        approval_id: str,
    ) -> TaskApprovalRecord:
        principal = self._normalize_identifier(
            principal_id,
            label="principal_id",
            limit=256,
        )
        normalized_approval_id = self._normalize_identifier(
            approval_id,
            label="approval_id",
            limit=128,
        )
        with self._connect() as db:
            return self._approval_record(
                self._owned_approval(db, principal, normalized_approval_id)
            )

    def transition(
        self,
        principal_id: str,
        task_id: str,
        target: TaskState,
        *,
        phase: str = "",
        reason: str = "",
        final_summary: str = "",
        failure_code: str = "",
        expected_revision: int | None = None,
    ) -> tuple[TaskRecord, TaskEvent]:
        principal = self._normalize_identifier(
            principal_id,
            label="principal_id",
            limit=256,
        )
        normalized_task_id = self._normalize_identifier(
            task_id,
            label="task_id",
            limit=128,
        )
        normalized_phase = phase.strip()
        normalized_reason = reason.strip()
        if len(normalized_phase) > 256 or len(normalized_reason) > 2000:
            raise ValueError("Task transition metadata exceeds its size limit")
        if len(final_summary) > 200_000 or len(failure_code) > 256:
            raise ValueError("Task terminal metadata exceeds its size limit")
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._owned_task(db, principal, normalized_task_id)
            current = TaskState(str(row["state"]))
            if expected_revision is not None and int(row["revision"]) != expected_revision:
                raise TaskTransitionError("Task revision changed")
            if target not in _TRANSITIONS[current]:
                raise TaskTransitionError(
                    f"Invalid Task transition: {current.value} -> {target.value}"
                )
            if target is TaskState.COMPLETED and not final_summary.strip():
                raise TaskTransitionError("Completed Task requires a final summary")
            if target is TaskState.FAILED and not failure_code.strip():
                raise TaskTransitionError("Failed Task requires a failure code")
            event_type: TaskEventType = (
                target.value
                if target in TERMINAL_TASK_STATES
                else "state_changed"
            )
            payload = TaskEventPayload(
                content=final_summary if target is TaskState.COMPLETED else "",
                previous_state=current,
                state=target,
                phase=normalized_phase or str(row["phase"]),
                reason=normalized_reason,
            )
            event = self._append_event(db, row, event_type, payload, now)
            attempt_state = {
                TaskState.COMPLETED: TaskAttemptState.COMPLETED,
                TaskState.FAILED: TaskAttemptState.FAILED,
                TaskState.CANCELLED: TaskAttemptState.CANCELLED,
                TaskState.PAUSED: TaskAttemptState.INTERRUPTED,
            }.get(target)
            if attempt_state is not None:
                self._finish_active_attempt(
                    db,
                    normalized_task_id,
                    attempt_state,
                    now,
                    failure_code=(
                        failure_code
                        if target is TaskState.FAILED
                        else "paused"
                        if target is TaskState.PAUSED
                        else "cancelled"
                        if target is TaskState.CANCELLED
                        else ""
                    ),
                )
            started_at = row["started_at"]
            if target is TaskState.RUNNING and started_at is None:
                started_at = now
            finished_at = now if target in TERMINAL_TASK_STATES else None
            lease_owner = str(row["lease_owner"])
            lease_expires_at = row["lease_expires_at"]
            if target is not TaskState.RUNNING:
                lease_owner = ""
                lease_expires_at = None
            db.execute(
                """UPDATE runtime_tasks SET
                       state=?, phase=?, final_summary=?, failure_code=?,
                       lease_owner=?, lease_expires_at=?, updated_at=?,
                       started_at=?, finished_at=?, next_event_seq=?,
                       revision=revision+1
                   WHERE task_id=?""",
                (
                    target.value,
                    normalized_phase or str(row["phase"]),
                    final_summary if target is TaskState.COMPLETED else "",
                    failure_code if target is TaskState.FAILED else "",
                    lease_owner,
                    lease_expires_at,
                    now,
                    started_at,
                    finished_at,
                    event.event_seq + 1,
                    normalized_task_id,
                ),
            )
            updated = self._owned_task(db, principal, normalized_task_id)
            return self._record(updated), event

    def claim_next(
        self,
        worker_id: str,
        *,
        lease_seconds: float = 60.0,
        principal_id: str = "",
    ) -> TaskRecord | None:
        worker = self._normalize_identifier(
            worker_id,
            label="worker_id",
            limit=128,
        )
        if not 1.0 <= lease_seconds <= 3600.0:
            raise ValueError("Task lease must be between 1 and 3600 seconds")
        principal = principal_id.strip()
        if principal:
            principal = self._normalize_identifier(
                principal,
                label="principal_id",
                limit=256,
            )
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if principal:
                row = db.execute(
                    """SELECT candidate.* FROM runtime_tasks AS candidate
                       WHERE candidate.state=? AND candidate.principal_id=?
                         AND NOT EXISTS (
                             SELECT 1 FROM runtime_tasks AS active
                             WHERE active.session_handle=candidate.session_handle
                               AND active.state IN (?, ?)
                         )
                       ORDER BY candidate.priority DESC,
                                candidate.created_at, candidate.task_id
                       LIMIT 1""",
                    (
                        TaskState.QUEUED.value,
                        principal,
                        TaskState.RUNNING.value,
                        TaskState.WAITING_APPROVAL.value,
                    ),
                ).fetchone()
            else:
                row = db.execute(
                    """SELECT candidate.* FROM runtime_tasks AS candidate
                       WHERE candidate.state=?
                         AND NOT EXISTS (
                             SELECT 1 FROM runtime_tasks AS active
                             WHERE active.session_handle=candidate.session_handle
                               AND active.state IN (?, ?)
                         )
                       ORDER BY candidate.priority DESC,
                                candidate.created_at, candidate.task_id
                       LIMIT 1""",
                    (
                        TaskState.QUEUED.value,
                        TaskState.RUNNING.value,
                        TaskState.WAITING_APPROVAL.value,
                    ),
                ).fetchone()
            if row is None:
                return None
            payload = TaskEventPayload(
                previous_state=TaskState.QUEUED,
                state=TaskState.RUNNING,
            )
            event = self._append_event(db, row, "state_changed", payload, now)
            ordinal = int(row["attempt_count"]) + 1
            db.execute(
                """INSERT INTO runtime_task_attempts(
                       attempt_id, task_id, ordinal, state, started_at,
                       finished_at, failure_code
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    self._attempt_id(str(row["task_id"]), ordinal),
                    str(row["task_id"]),
                    ordinal,
                    TaskAttemptState.RUNNING.value,
                    now,
                    None,
                    "",
                ),
            )
            db.execute(
                """UPDATE runtime_tasks SET
                       state=?, attempt_count=attempt_count+1,
                       lease_owner=?, lease_expires_at=?, started_at=?,
                       updated_at=?, next_event_seq=?, revision=revision+1
                   WHERE task_id=? AND state=?""",
                (
                    TaskState.RUNNING.value,
                    worker,
                    now + lease_seconds,
                    row["started_at"] if row["started_at"] is not None else now,
                    now,
                    event.event_seq + 1,
                    str(row["task_id"]),
                    TaskState.QUEUED.value,
                ),
            )
            claimed = self._owned_task(
                db,
                str(row["principal_id"]),
                str(row["task_id"]),
            )
            return self._record(claimed)

    def is_cancel_requested(self, principal_id: str, task_id: str) -> bool:
        return self.get(principal_id, task_id).cancel_requested

    def recover_interrupted(self) -> tuple[TaskEvent, ...]:
        """Pause running Tasks whose last tool outcome cannot be proven."""

        now = self._clock()
        recovered: list[TaskEvent] = []
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """UPDATE runtime_task_tool_steps SET
                       state=?, updated_at=?
                   WHERE state=?""",
                (
                    TaskToolStepState.OUTCOME_UNKNOWN.value,
                    now,
                    TaskToolStepState.COMMITTING.value,
                ),
            )
            waiting_rows = db.execute(
                """SELECT * FROM runtime_tasks
                   WHERE state=? ORDER BY created_at, task_id""",
                (TaskState.WAITING_APPROVAL.value,),
            ).fetchall()
            for row in waiting_rows:
                self._finish_active_attempt(
                    db,
                    str(row["task_id"]),
                    TaskAttemptState.INTERRUPTED,
                    now,
                    failure_code="core_restart_waiting_approval",
                )
                event = self._append_event(
                    db,
                    row,
                    "warning",
                    TaskEventPayload(
                        state=TaskState.WAITING_APPROVAL,
                        phase="waiting_approval",
                        reason=(
                            "Core restarted while approval was pending; the approval "
                            "remains valid and will start a new attempt when resolved"
                        ),
                    ),
                    now,
                )
                db.execute(
                    """UPDATE runtime_tasks SET
                           lease_owner='', lease_expires_at=NULL, updated_at=?,
                           next_event_seq=?, revision=revision+1
                       WHERE task_id=?""",
                    (
                        now,
                        event.event_seq + 1,
                        str(row["task_id"]),
                    ),
                )
                recovered.append(event)
            rows = db.execute(
                """SELECT * FROM runtime_tasks
                   WHERE state=? ORDER BY created_at, task_id""",
                (TaskState.RUNNING.value,),
            ).fetchall()
            for row in rows:
                unknown_commit = db.execute(
                    """SELECT 1 FROM runtime_task_tool_steps
                       WHERE task_id=? AND state=? LIMIT 1""",
                    (
                        str(row["task_id"]),
                        TaskToolStepState.OUTCOME_UNKNOWN.value,
                    ),
                ).fetchone()
                pause_requested = str(row["phase"]) == "pause_requested"
                phase = (
                    "outcome_unknown"
                    if unknown_commit is not None
                    else "manual_pause"
                    if pause_requested
                    else "interrupted"
                )
                reason = (
                    "Core restarted during a tool commit; its outcome is unknown "
                    "and explicit recovery is required"
                    if unknown_commit is not None
                    else "The persisted pause request was completed during Core recovery"
                    if pause_requested
                    else "Core restarted before execution completed; explicit resume is required"
                )
                self._finish_active_attempt(
                    db,
                    str(row["task_id"]),
                    TaskAttemptState.INTERRUPTED,
                    now,
                    failure_code="core_restart",
                )
                event = self._append_event(
                    db,
                    row,
                    "state_changed",
                    TaskEventPayload(
                        previous_state=TaskState.RUNNING,
                        state=TaskState.PAUSED,
                        phase=phase,
                        reason=reason,
                    ),
                    now,
                )
                db.execute(
                    """UPDATE runtime_tasks SET
                           state=?, phase=?, lease_owner='', lease_expires_at=NULL,
                           updated_at=?, next_event_seq=?, revision=revision+1
                       WHERE task_id=?""",
                    (
                        TaskState.PAUSED.value,
                        phase,
                        now,
                        event.event_seq + 1,
                        str(row["task_id"]),
                    ),
                )
                recovered.append(event)
        return tuple(recovered)
