"""SQLite repository owning durable Task state and ordered events."""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from pc_assistant.agent_runtime.contracts import ArtifactAttachment, RuntimeScope
from pc_assistant.sqlite_connection import connect_sqlite, initialize_wal
from pc_assistant.tasks.definition_repository import TaskDefinitionRepositoryMixin
from pc_assistant.tasks.errors import (
    TaskNotFoundError,
)
from pc_assistant.tasks.execution_repository import TaskExecutionRepositoryMixin
from pc_assistant.tasks.models import (
    ApprovalState,
    TaskApprovalRecord,
    TaskAttemptRecord,
    TaskAttemptState,
    TaskDefinitionRecord,
    TaskDefinitionState,
    TaskEventPayload,
    TaskExecutionRecord,
    TaskExecutionTrace,
    TaskLaunchPolicy,
    TaskLaunchReason,
    TaskOrigin,
    TaskRecord,
    TaskState,
    TaskToolStepRecord,
    TaskToolStepState,
    TaskTraceEntry,
)
from pc_assistant.tasks.runtime_repository import TaskRuntimeRepositoryMixin
from pc_assistant.tasks.schema import TaskSchemaMixin
from pc_assistant.tasks.tool_repository import TaskToolRepositoryMixin

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


class TaskRepository(
    TaskSchemaMixin,
    TaskDefinitionRepositoryMixin,
    TaskExecutionRepositoryMixin,
    TaskToolRepositoryMixin,
    TaskRuntimeRepositoryMixin,
):
    """Persist Task aggregates and journal events with principal ownership."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        task_id_factory: Callable[[], str] | None = None,
        definition_id_factory: Callable[[], str] | None = None,
        approval_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] = time.time,
        max_active_tasks: int = 128,
        max_active_tasks_per_principal: int = 32,
        trace_retention_seconds: float = _DEFAULT_TRACE_RETENTION_SECONDS,
    ) -> None:
        if not 1 <= max_active_tasks <= 10_000:
            raise ValueError("Global active Task limit must be between 1 and 10000")
        if not 1 <= max_active_tasks_per_principal <= max_active_tasks:
            raise ValueError(
                "Per-principal active Task limit must be between 1 and the global limit"
            )
        if not 60 <= trace_retention_seconds <= 10 * 365 * 24 * 60 * 60:
            raise ValueError("Task trace retention must be between 60 seconds and 10 years")
        self._path = Path(db_path).expanduser().resolve()
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._path.parent.chmod(0o700)
        self._task_id_factory = task_id_factory or (
            lambda: secrets.token_urlsafe(18)
        )
        self._definition_id_factory = definition_id_factory or (
            lambda: secrets.token_urlsafe(18)
        )
        self._approval_id_factory = approval_id_factory or (
            lambda: secrets.token_urlsafe(18)
        )
        self._clock = clock
        self._max_active_tasks = max_active_tasks
        self._max_active_tasks_per_principal = max_active_tasks_per_principal
        self._trace_retention_seconds = float(trace_retention_seconds)
        initialize_wal(self._path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = connect_sqlite(self._path, foreign_keys=True)
        self._path.chmod(0o600)
        return connection


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

    @classmethod
    def _definition_record(
        cls,
        row: sqlite3.Row,
        *,
        execution_count: int | None = None,
    ) -> TaskDefinitionRecord:
        notification_raw = cls._json_object(
            str(row["notification_policy_json"]),
            label="notification policy",
        )
        if not all(isinstance(value, bool) for value in notification_raw.values()):
            raise ValueError("Task notification policy must contain booleans")
        return TaskDefinitionRecord(
            task_id=str(row["task_id"]),
            principal_id=str(row["principal_id"]),
            session_handle=str(row["session_handle"]),
            title=str(row["title"]),
            goal=str(row["goal"]),
            attachments=cls._decode_attachments(str(row["attachments_json"])),
            tools_enabled=bool(row["tools_enabled"]),
            priority=int(row["priority"]),
            launch_policy=TaskLaunchPolicy.model_validate_json(
                str(row["policy_json"])
            ),
            notification_policy={
                str(key): bool(value) for key, value in notification_raw.items()
            },
            state=TaskDefinitionState(str(row["state"])),
            revision=int(row["revision"]),
            latest_execution_id=str(row["latest_execution_id"] or ""),
            execution_count=(
                int(row["execution_count"])
                if execution_count is None and "execution_count" in row.keys()
                else int(execution_count or 0)
            ),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    @staticmethod
    def _execution_record(
        mapping: sqlite3.Row,
        execution: TaskRecord,
        *,
        trace: TaskExecutionTrace | None = None,
    ) -> TaskExecutionRecord:
        return TaskExecutionRecord(
            execution_id=execution.task_id,
            task_id=str(mapping["task_id"]),
            task_revision=int(mapping["task_revision"]),
            launch_reason=TaskLaunchReason(str(mapping["launch_reason"])),
            goal_snapshot=str(mapping["goal_snapshot"]),
            attachment_snapshots=TaskRepository._decode_attachments(
                str(mapping["attachments_json"])
            ),
            policy_snapshot=TaskLaunchPolicy.model_validate_json(
                str(mapping["policy_snapshot_json"])
            ),
            state=execution.state,
            phase=execution.phase,
            cancel_requested=execution.cancel_requested,
            final_result=execution.final_summary,
            failure_code=execution.failure_code,
            created_at=execution.created_at,
            updated_at=execution.updated_at,
            started_at=execution.started_at,
            finished_at=execution.finished_at,
            trace=trace,
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

    @staticmethod
    def _trace_entries_json(entries: tuple[TaskTraceEntry, ...]) -> str:
        encoded = json.dumps(
            [entry.model_dump(mode="json") for entry in entries],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(encoded.encode("utf-8")) > 16 * 1024 * 1024:
            raise ValueError("Task execution trace exceeds the size limit")
        return encoded

    @staticmethod
    def _trace_record(row: sqlite3.Row) -> TaskExecutionTrace:
        try:
            raw_entries = json.loads(str(row["entries_json"]))
            if not isinstance(raw_entries, list):
                raise ValueError
            entries = tuple(TaskTraceEntry.model_validate(item) for item in raw_entries)
        except Exception as exc:
            raise RuntimeError("Task execution trace is corrupt") from exc
        return TaskExecutionTrace(
            task_id=str(row["task_id"]),
            entries=entries,
            final_output=str(row["final_output"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            retained_until=float(row["retained_until"]),
            compacted_at=(
                None if row["compacted_at"] is None else float(row["compacted_at"])
            ),
            revision=int(row["revision"]),
        )
