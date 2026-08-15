"""SQLite repository owning durable Task state and ordered events."""
from __future__ import annotations

import base64
import binascii
import json
import math
import sqlite3

from knoa_platform.agent_runtime.contracts import ArtifactAttachment, RuntimeScope
from knoa_platform.tasks.errors import (
    TaskCapacityError,
    TaskIdempotencyConflictError,
    TaskTransitionError,
)
from knoa_platform.tasks.models import (
    TERMINAL_TASK_STATES,
    ApprovalState,
    PrincipalTaskEvent,
    TaskAttemptState,
    TaskCancelResult,
    TaskEvent,
    TaskEventPayload,
    TaskEventType,
    TaskOrigin,
    TaskPauseResult,
    TaskRecord,
    TaskState,
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
_DURABLE_TASK_EVENT_TYPES = _PRINCIPAL_FEED_EVENT_TYPES | {"artifact", "warning"}
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


class TaskRuntimeRepositoryMixin:

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
        if event_type in _PRINCIPAL_FEED_EVENT_TYPES:
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
        origin: TaskOrigin = TaskOrigin.USER,
        agent_id: str | None = None,
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
                    session = self._owned_session(db, scope)
                    selected_agent = str(session["agent_id"])
                    if agent_id is not None and selected_agent != agent_id:
                        raise ValueError("Task Agent must match the Session Agent")
                    existing = db.execute(
                        """SELECT * FROM runtime_tasks
                           WHERE principal_id=? AND client_request_id=?""",
                        (scope.principal_id, request_id),
                    ).fetchone()
                    if existing is not None:
                        same_request = (
                            str(existing["session_handle"]) == scope.session_handle
                            and str(existing["agent_id"]) == selected_agent
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
                               task_id, principal_id, session_handle, agent_id,
                               client_request_id, origin, parent_task_id, goal,
                               attachments_json, tools_enabled, priority, state,
                               phase, attempt_count, cancel_requested,
                               final_summary, failure_code, lease_owner,
                               lease_expires_at, created_at, updated_at,
                               started_at, finished_at, next_event_seq, revision
                           ) VALUES (
                               ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               ?, ?, ?, ?, ?, ?, ?, ?, ?
                           )""",
                        (
                            task_id,
                            scope.principal_id,
                            scope.session_handle,
                            selected_agent,
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
        if event_type not in _DURABLE_TASK_EVENT_TYPES:
            raise ValueError(
                "Streaming model output belongs to Task ExecutionTrace, not the event journal"
            )
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
