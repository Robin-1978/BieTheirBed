"""SQLite repository owning durable Task state and ordered events."""
from __future__ import annotations

import json
import sqlite3

from knoa_platform.tasks.errors import (
    TaskIdempotencyConflictError,
    TaskNotFoundError,
    TaskTransitionError,
)
from knoa_platform.tasks.models import (
    ApprovalState,
    TaskApprovalRecord,
    TaskAttemptState,
    TaskEvent,
    TaskEventPayload,
    TaskRecord,
    TaskState,
    TaskToolStepRecord,
    TaskToolStepState,
)

_MAX_EVENT_BYTES = 512 * 1024
_DEFAULT_TRACE_RETENTION_SECONDS = 90 * 24 * 60 * 60
_PRINCIPAL_FEED_EVENT_TYPES = frozenset(
    {
        "task_created",
        "state_changed",
        "approval_requested",
        "approval_resolved",
        "interaction_requested",
        "interaction_resolved",
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


class TaskToolRepositoryMixin:

    def annotate_approval_review(
        self,
        principal_id: str,
        approval_id: str,
        *,
        reason: str,
    ) -> tuple[TaskApprovalRecord, TaskEvent]:
        principal = self._normalize_identifier(
            principal_id, label="principal_id", limit=256
        )
        normalized_approval_id = self._normalize_identifier(
            approval_id, label="approval_id", limit=128
        )
        normalized_reason = reason.strip()[:2000]
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._owned_approval(db, principal, normalized_approval_id)
            approval = self._approval_record(row)
            if approval.state is not ApprovalState.PENDING:
                event_row = db.execute(
                    """SELECT * FROM runtime_task_events
                       WHERE task_id=? AND event_seq=?""",
                    (approval.task_id, approval.request_event_seq),
                ).fetchone()
                assert event_row is not None
                return approval, TaskEvent(
                    task_id=approval.task_id,
                    event_seq=approval.request_event_seq,
                    event_type="approval_requested",
                    payload=TaskEventPayload.model_validate_json(
                        event_row["payload_json"]
                    ),
                    occurred_at=float(event_row["occurred_at"]),
                )
            db.execute(
                "UPDATE runtime_task_approvals SET reason=? WHERE approval_id=?",
                (normalized_reason, normalized_approval_id),
            )
            event_row = db.execute(
                """SELECT * FROM runtime_task_events
                   WHERE task_id=? AND event_seq=?""",
                (approval.task_id, approval.request_event_seq),
            ).fetchone()
            assert event_row is not None
            payload = TaskEventPayload.model_validate_json(event_row["payload_json"])
            payload = payload.model_copy(update={"reason": normalized_reason})
            db.execute(
                """UPDATE runtime_task_events SET payload_json=?
                   WHERE task_id=? AND event_seq=?""",
                (
                    self._event_json(payload),
                    approval.task_id,
                    approval.request_event_seq,
                ),
            )
            updated = self._owned_approval(db, principal, normalized_approval_id)
            return self._approval_record(updated), TaskEvent(
                task_id=approval.task_id,
                event_seq=approval.request_event_seq,
                event_type="approval_requested",
                payload=payload,
                occurred_at=float(event_row["occurred_at"]),
            )

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
            "internal_write",
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

    def list_approvals(
        self,
        principal_id: str,
        task_id: str,
    ) -> tuple[TaskApprovalRecord, ...]:
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
            rows = db.execute(
                """SELECT * FROM runtime_task_approvals
                   WHERE principal_id=? AND task_id=?
                   ORDER BY created_at, approval_id""",
                (principal, normalized_task_id),
            ).fetchall()
            return tuple(self._approval_record(row) for row in rows)
