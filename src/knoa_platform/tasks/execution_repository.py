"""SQLite repository owning durable Task state and ordered events."""
from __future__ import annotations

from knoa_platform.agent_runtime.contracts import ArtifactAttachment
from knoa_platform.tasks.errors import (
    TaskIdempotencyConflictError,
    TaskNotFoundError,
    TaskTransitionError,
)
from knoa_platform.tasks.models import (
    TERMINAL_TASK_STATES,
    TaskAttemptRecord,
    TaskDefinitionRecord,
    TaskExecutionRecord,
    TaskExecutionTrace,
    TaskLaunchPolicy,
    TaskLaunchReason,
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


class TaskExecutionRepositoryMixin:

    def link_task_execution(
        self,
        principal_id: str,
        task_id: str,
        execution_id: str,
        *,
        launch_reason: TaskLaunchReason,
        goal_snapshot: str | None = None,
        attachments_snapshot: tuple[ArtifactAttachment, ...] | None = None,
        policy_snapshot: TaskLaunchPolicy | None = None,
        task_revision: int | None = None,
        agent_id_snapshot: str | None = None,
    ) -> TaskExecutionRecord:
        definition = self.get_task_definition(principal_id, task_id)
        execution = self.get(principal_id, execution_id)
        revision = definition.revision if task_revision is None else task_revision
        goal = definition.goal if goal_snapshot is None else goal_snapshot.strip()
        if not goal:
            raise ValueError("Execution goal snapshot must not be empty")
        attachments = (
            definition.attachments
            if attachments_snapshot is None
            else attachments_snapshot
        )
        policy = definition.launch_policy if policy_snapshot is None else policy_snapshot
        agent_id = definition.agent_id if agent_id_snapshot is None else agent_id_snapshot
        if execution.agent_id != agent_id:
            raise ValueError("TaskExecution Agent must match its Runtime Task Agent")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM task_executions WHERE execution_id=?",
                (execution.task_id,),
            ).fetchone()
            if existing is None:
                db.execute(
                    """INSERT INTO task_executions(
                           execution_id, task_id, task_revision, launch_reason,
                           agent_id_snapshot, goal_snapshot, attachments_json, policy_snapshot_json,
                           created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        execution.task_id,
                        definition.task_id,
                        revision,
                        launch_reason.value,
                        agent_id,
                        goal,
                        self._attachments_payload(attachments),
                        policy.model_dump_json(),
                        execution.created_at,
                    ),
                )
            elif str(existing["task_id"]) != definition.task_id:
                raise TaskIdempotencyConflictError(
                    "Execution already belongs to another Task"
                )
            db.execute(
                """UPDATE tasks SET latest_execution_id=?, updated_at=?
                   WHERE task_id=?""",
                (execution.task_id, self._clock(), definition.task_id),
            )
            mapping = db.execute(
                "SELECT * FROM task_executions WHERE execution_id=?",
                (execution.task_id,),
            ).fetchone()
        if mapping is None:
            raise RuntimeError("Task execution link was not persisted")
        return self._execution_record(mapping, execution)

    def get_task_execution(
        self,
        principal_id: str,
        execution_id: str,
        *,
        include_trace: bool = True,
    ) -> TaskExecutionRecord:
        execution = self.get(principal_id, execution_id)
        with self._connect() as db:
            mapping = db.execute(
                """SELECT task_executions.* FROM task_executions
                   JOIN tasks USING(task_id)
                   WHERE tasks.principal_id=? AND execution_id=?""",
                (principal_id, execution.task_id),
            ).fetchone()
        if mapping is None:
            raise TaskNotFoundError("Task execution not found")
        trace = self.get_trace(principal_id, execution.task_id) if include_trace else None
        return self._execution_record(mapping, execution, trace=trace)

    def list_task_executions(
        self,
        principal_id: str,
        task_id: str,
        *,
        limit: int = 100,
    ) -> tuple[TaskExecutionRecord, ...]:
        definition = self.get_task_definition(principal_id, task_id)
        if not 1 <= limit <= 200:
            raise ValueError("Execution list limit must be between 1 and 200")
        with self._connect() as db:
            mappings = db.execute(
                """SELECT * FROM task_executions WHERE task_id=?
                   ORDER BY created_at DESC, execution_id DESC LIMIT ?""",
                (definition.task_id, limit),
            ).fetchall()
        return tuple(
            self._execution_record(
                mapping,
                self.get(principal_id, str(mapping["execution_id"])),
            )
            for mapping in mappings
        )

    def task_for_execution(
        self,
        principal_id: str,
        execution_id: str,
    ) -> TaskDefinitionRecord:
        normalized_execution_id = self._normalize_identifier(
            execution_id, label="execution_id", limit=128
        )
        with self._connect() as db:
            row = db.execute(
                """SELECT tasks.*, task_launch_policies.policy_json,
                          (SELECT COUNT(*) FROM task_executions AS counted
                           WHERE counted.task_id=tasks.task_id) AS execution_count
                   FROM task_executions
                   JOIN tasks USING(task_id)
                   JOIN task_launch_policies USING(task_id)
                   WHERE tasks.principal_id=?
                     AND task_executions.execution_id=?""",
                (principal_id, normalized_execution_id),
            ).fetchone()
        if row is None:
            raise TaskNotFoundError("Task execution not found")
        return self._definition_record(row)

    def delete_task_execution(self, principal_id: str, execution_id: str) -> None:
        execution = self.get_task_execution(
            principal_id, execution_id, include_trace=False
        )
        if execution.state not in TERMINAL_TASK_STATES:
            raise TaskTransitionError("Active TaskExecution cannot be deleted")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM runtime_tasks WHERE task_id=?", (execution.execution_id,))
            latest = db.execute(
                """SELECT execution_id FROM task_executions
                   WHERE task_id=? ORDER BY created_at DESC, execution_id DESC LIMIT 1""",
                (execution.task_id,),
            ).fetchone()
            db.execute(
                """UPDATE tasks SET latest_execution_id=?, updated_at=?
                   WHERE task_id=?""",
                (
                    None if latest is None else str(latest["execution_id"]),
                    self._clock(),
                    execution.task_id,
                ),
            )

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

    def get_trace(
        self,
        principal_id: str,
        task_id: str,
    ) -> TaskExecutionTrace | None:
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
            self._owned_task(db, principal, normalized_task_id)
            row = db.execute(
                "SELECT * FROM runtime_task_execution_traces WHERE task_id=?",
                (normalized_task_id,),
            ).fetchone()
            return None if row is None else self._trace_record(row)

    def save_trace(
        self,
        principal_id: str,
        task_id: str,
        *,
        entries: tuple[TaskTraceEntry, ...],
        final_output: str = "",
    ) -> TaskExecutionTrace:
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
        if len(final_output) > 200_000:
            raise ValueError("Task trace final output exceeds 200000 characters")
        encoded = self._trace_entries_json(entries)
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._owned_task(db, principal, normalized_task_id)
            existing = db.execute(
                "SELECT created_at, revision FROM runtime_task_execution_traces WHERE task_id=?",
                (normalized_task_id,),
            ).fetchone()
            if existing is None:
                db.execute(
                    """INSERT INTO runtime_task_execution_traces(
                           task_id, entries_json, final_output, created_at,
                           updated_at, retained_until, compacted_at, revision
                       ) VALUES (?, ?, ?, ?, ?, ?, NULL, 0)""",
                    (
                        normalized_task_id,
                        encoded,
                        final_output,
                        now,
                        now,
                        now + self._trace_retention_seconds,
                    ),
                )
            else:
                db.execute(
                    """UPDATE runtime_task_execution_traces SET
                           entries_json=?, final_output=?, updated_at=?,
                           retained_until=?, compacted_at=NULL, revision=revision+1
                       WHERE task_id=?""",
                    (
                        encoded,
                        final_output,
                        now,
                        now + self._trace_retention_seconds,
                        normalized_task_id,
                    ),
                )
            row = db.execute(
                "SELECT * FROM runtime_task_execution_traces WHERE task_id=?",
                (normalized_task_id,),
            ).fetchone()
            assert row is not None
            return self._trace_record(row)

    def compact_expired_traces(self) -> int:
        """Drop verbose drafts after retention while preserving Task results."""
        now = self._clock()
        terminal_values = tuple(state.value for state in TERMINAL_TASK_STATES)
        placeholders = ",".join("?" for _ in terminal_values)
        compacted = 0
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                f"""SELECT trace.* FROM runtime_task_execution_traces trace
                    JOIN runtime_tasks task ON task.task_id=trace.task_id
                    WHERE trace.compacted_at IS NULL
                      AND trace.retained_until<=?
                      AND task.state IN ({placeholders})""",
                (now, *terminal_values),
            ).fetchall()
            for row in rows:
                trace = self._trace_record(row)
                compact_entries = tuple(
                    entry.model_copy(
                        update={
                            "tool_args": {},
                            "tool_result": None,
                        }
                    )
                    for entry in trace.entries
                    if entry.entry_type
                    in {
                        "plan",
                        "tool_call",
                        "tool_result",
                        "artifact",
                        "context_compacted",
                        "warning",
                        "final_output",
                    }
                )
                db.execute(
                    """UPDATE runtime_task_execution_traces SET
                           entries_json=?, compacted_at=?, updated_at=?,
                           revision=revision+1
                       WHERE task_id=? AND compacted_at IS NULL""",
                    (
                        self._trace_entries_json(compact_entries),
                        now,
                        now,
                        trace.task_id,
                    ),
                )
                compacted += 1
        return compacted
