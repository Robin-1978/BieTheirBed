"""SQLite repository owning durable Task state and ordered events."""
from __future__ import annotations

import sqlite3

from pc_assistant.sqlite_schema import (
    require_exact_table,
    require_foreign_keys,
    require_index_columns,
)
from pc_assistant.tasks.models import (
    TaskEventPayload,
    TaskOrigin,
    TaskState,
    TaskTraceEntry,
)

_MAX_EVENT_BYTES = 512 * 1024
_DEFAULT_TRACE_RETENTION_SECONDS = 90 * 24 * 60 * 60
_PRINCIPAL_FEED_EVENT_TYPES = frozenset(
    {
        "task_created",
        "state_changed",
        "approval_requested",
        "approval_resolved",
        "completed",
        "failed",
        "cancelled",
    }
)
_DURABLE_TASK_EVENT_TYPES = _PRINCIPAL_FEED_EVENT_TYPES | {"warning"}
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




class TaskSchemaMixin:

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
                CREATE TABLE IF NOT EXISTS runtime_task_execution_traces (
                    task_id TEXT PRIMARY KEY
                        REFERENCES runtime_tasks(task_id) ON DELETE CASCADE,
                    entries_json TEXT NOT NULL,
                    final_output TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    retained_until REAL NOT NULL,
                    compacted_at REAL,
                    revision INTEGER NOT NULL
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
                CREATE INDEX IF NOT EXISTS runtime_task_traces_by_retention
                    ON runtime_task_execution_traces(retained_until, compacted_at);
                CREATE INDEX IF NOT EXISTS runtime_task_tool_steps_by_task_state
                    ON runtime_task_tool_steps(task_id, state, created_at);
                CREATE INDEX IF NOT EXISTS runtime_principal_task_events_by_owner
                    ON runtime_principal_task_events(
                        principal_id, feed_event_id
                    );
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    client_request_id TEXT NOT NULL,
                    session_handle TEXT NOT NULL
                        REFERENCES runtime_sessions(session_handle) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    attachments_json TEXT NOT NULL,
                    tools_enabled INTEGER NOT NULL,
                    priority INTEGER NOT NULL,
                    notification_policy_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    latest_execution_id TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(principal_id, client_request_id)
                );
                CREATE TABLE IF NOT EXISTS task_launch_policies (
                    task_id TEXT PRIMARY KEY
                        REFERENCES tasks(task_id) ON DELETE CASCADE,
                    policy_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_executions (
                    execution_id TEXT PRIMARY KEY
                        REFERENCES runtime_tasks(task_id) ON DELETE CASCADE,
                    task_id TEXT NOT NULL
                        REFERENCES tasks(task_id) ON DELETE CASCADE,
                    task_revision INTEGER NOT NULL,
                    launch_reason TEXT NOT NULL,
                    goal_snapshot TEXT NOT NULL,
                    attachments_json TEXT NOT NULL,
                    policy_snapshot_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(task_id, execution_id)
                );
                CREATE TABLE IF NOT EXISTS task_launch_bindings (
                    task_id TEXT NOT NULL
                        REFERENCES tasks(task_id) ON DELETE CASCADE,
                    provider_kind TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    PRIMARY KEY(provider_kind, provider_id),
                    UNIQUE(task_id, provider_kind)
                );
                CREATE INDEX IF NOT EXISTS tasks_by_owner_state
                    ON tasks(principal_id, state, updated_at DESC, task_id DESC);
                CREATE INDEX IF NOT EXISTS task_executions_by_task
                    ON task_executions(task_id, created_at DESC, execution_id DESC);
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
            db.execute(
                "UPDATE runtime_tasks SET origin=? WHERE origin='chat'",
                (TaskOrigin.USER.value,),
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
                "runtime_task_execution_traces",
                (
                    ("task_id", "TEXT", False, None, 1),
                    ("entries_json", "TEXT", True, None, 0),
                    ("final_output", "TEXT", True, None, 0),
                    ("created_at", "REAL", True, None, 0),
                    ("updated_at", "REAL", True, None, 0),
                    ("retained_until", "REAL", True, None, 0),
                    ("compacted_at", "REAL", False, None, 0),
                    ("revision", "INTEGER", True, None, 0),
                ),
                label="Runtime Task execution trace",
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
                "runtime_task_execution_traces",
                (
                    (
                        "runtime_tasks",
                        "task_id",
                        "task_id",
                        "NO ACTION",
                        "CASCADE",
                    ),
                ),
                label="Runtime Task execution trace",
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
                "runtime_task_traces_by_retention",
                ("retained_until", "compacted_at"),
                label="Runtime Task execution trace",
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
            self._migrate_legacy_runtime_events(db)

    def _migrate_legacy_runtime_events(self, db: sqlite3.Connection) -> None:
        """Move old streaming events into one coalesced trace per Task."""
        placeholders = ",".join("?" for _ in _DURABLE_TASK_EVENT_TYPES)
        task_rows = db.execute(
            f"""SELECT DISTINCT task_id FROM runtime_task_events
                WHERE event_type NOT IN ({placeholders})""",
            tuple(sorted(_DURABLE_TASK_EVENT_TYPES)),
        ).fetchall()
        for task_row in task_rows:
            task_id = str(task_row["task_id"])
            existing = db.execute(
                "SELECT 1 FROM runtime_task_execution_traces WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if existing is None:
                entries: list[TaskTraceEntry] = []
                final_output = ""
                rows = db.execute(
                    f"""SELECT event_type, payload_json, occurred_at
                        FROM runtime_task_events
                        WHERE task_id=? AND event_type NOT IN ({placeholders})
                        ORDER BY event_seq""",
                    (task_id, *tuple(sorted(_DURABLE_TASK_EVENT_TYPES))),
                ).fetchall()
                for row in rows:
                    try:
                        payload = TaskEventPayload.model_validate_json(
                            str(row["payload_json"])
                        )
                    except Exception:
                        continue
                    event_type = str(row["event_type"])
                    entry_type = {
                        "reasoning_delta": "reasoning",
                        "content_delta": "content",
                        "plan": "plan",
                        "tool_call": "tool_call",
                        "tool_result": "tool_result",
                        "artifact": "artifact",
                        "context_compacted": "context_compacted",
                        "final_output": "final_output",
                    }.get(event_type)
                    if entry_type is None:
                        continue
                    occurred_at = float(row["occurred_at"])
                    if event_type == "final_output":
                        final_output = payload.content
                    if (
                        entry_type in {"reasoning", "content"}
                        and entries
                        and entries[-1].entry_type == entry_type
                        and entries[-1].iteration == payload.iteration
                    ):
                        previous = entries[-1]
                        entries[-1] = previous.model_copy(
                            update={
                                "content": previous.content + payload.content,
                                "occurred_at": occurred_at,
                            }
                        )
                        continue
                    entries.append(
                        TaskTraceEntry(
                            entry_type=entry_type,
                            iteration=payload.iteration,
                            content=payload.content,
                            tool_call_id=payload.tool_call_id,
                            tool_name=payload.tool_name,
                            tool_args=payload.tool_args,
                            tool_result=payload.tool_result,
                            artifact=payload.artifact,
                            occurred_at=occurred_at,
                        )
                    )
                if entries:
                    created_at = entries[0].occurred_at
                    updated_at = entries[-1].occurred_at
                    db.execute(
                        """INSERT INTO runtime_task_execution_traces(
                               task_id, entries_json, final_output, created_at,
                               updated_at, retained_until, compacted_at, revision
                           ) VALUES (?, ?, ?, ?, ?, ?, NULL, 0)""",
                        (
                            task_id,
                            self._trace_entries_json(tuple(entries)),
                            final_output,
                            created_at,
                            updated_at,
                            updated_at + self._trace_retention_seconds,
                        ),
                    )

        db.execute(
            f"""DELETE FROM runtime_principal_task_events
                WHERE EXISTS (
                    SELECT 1 FROM runtime_task_events event
                    WHERE event.task_id=runtime_principal_task_events.task_id
                      AND event.event_seq=runtime_principal_task_events.task_event_seq
                      AND event.event_type NOT IN ({','.join('?' for _ in _PRINCIPAL_FEED_EVENT_TYPES)})
                )""",
            tuple(sorted(_PRINCIPAL_FEED_EVENT_TYPES)),
        )
        db.execute(
            f"DELETE FROM runtime_task_events WHERE event_type NOT IN ({placeholders})",
            tuple(sorted(_DURABLE_TASK_EVENT_TYPES)),
        )
