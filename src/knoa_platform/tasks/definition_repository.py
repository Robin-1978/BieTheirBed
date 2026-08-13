"""SQLite repository owning durable Task state and ordered events."""
from __future__ import annotations

import json
import sqlite3

from knoa_platform.agent_runtime.contracts import ArtifactAttachment, RuntimeScope
from knoa_platform.tasks.errors import (
    TaskIdempotencyConflictError,
    TaskNotFoundError,
    TaskTransitionError,
)
from knoa_platform.tasks.models import (
    TERMINAL_TASK_STATES,
    TaskDefinitionRecord,
    TaskDefinitionState,
    TaskLaunchPolicy,
    TaskState,
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


class TaskDefinitionRepositoryMixin:

    @staticmethod
    def _task_title(title: str, goal: str) -> str:
        normalized = " ".join(title.strip().split())
        if not normalized:
            normalized = " ".join(goal.strip().splitlines()[0].split())
        if not normalized:
            raise ValueError("Task title must not be empty")
        return normalized[:200]

    @staticmethod
    def _notification_policy_payload(policy: dict[str, bool]) -> str:
        if len(policy) > 32 or any(
            not isinstance(key, str)
            or not key.strip()
            or len(key) > 64
            or not isinstance(value, bool)
            for key, value in policy.items()
        ):
            raise ValueError("Task notification policy is invalid")
        return json.dumps(policy, separators=(",", ":"), sort_keys=True)

    def create_task_definition(
        self,
        scope: RuntimeScope,
        *,
        client_request_id: str,
        title: str,
        goal: str,
        attachments: tuple[ArtifactAttachment, ...] = (),
        tools_enabled: bool = True,
        priority: int = 0,
        launch_policy: TaskLaunchPolicy | None = None,
        notification_policy: dict[str, bool] | None = None,
        agent_id: str | None = None,
    ) -> tuple[TaskDefinitionRecord, bool]:
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
        normalized_title = self._task_title(title, normalized_goal)
        attachment_json = self._attachments_payload(attachments)
        policy = launch_policy or TaskLaunchPolicy()
        policy_json = policy.model_dump_json()
        notifications_json = self._notification_policy_payload(
            notification_policy or {
                "completed": True,
                "failed": True,
                "waiting_approval": True,
            }
        )
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
                        """SELECT tasks.*, task_launch_policies.policy_json,
                                  (SELECT COUNT(*) FROM task_executions
                                   WHERE task_executions.task_id=tasks.task_id)
                                      AS execution_count
                           FROM tasks
                           JOIN task_launch_policies USING(task_id)
                           WHERE principal_id=? AND client_request_id=?""",
                        (scope.principal_id, request_id),
                    ).fetchone()
                    if existing is not None:
                        same = (
                            str(existing["session_handle"]) == scope.session_handle
                            and str(existing["agent_id"]) == selected_agent
                            and str(existing["title"]) == normalized_title
                            and str(existing["goal"]) == normalized_goal
                            and str(existing["attachments_json"]) == attachment_json
                            and bool(existing["tools_enabled"]) is bool(tools_enabled)
                            and int(existing["priority"]) == priority
                            and str(existing["policy_json"]) == policy_json
                            and str(existing["notification_policy_json"])
                            == notifications_json
                        )
                        if not same:
                            raise TaskIdempotencyConflictError(
                                "client_request_id already belongs to another Task definition"
                            )
                        return self._definition_record(existing), False
                    task_id = self._normalize_identifier(
                        self._definition_id_factory(),
                        label="task_id",
                        limit=128,
                    )
                    db.execute(
                        """INSERT INTO tasks(
                               task_id, principal_id, client_request_id,
                               session_handle, agent_id, title, goal, attachments_json,
                               tools_enabled, priority, notification_policy_json,
                               state, revision, latest_execution_id,
                               created_at, updated_at
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            task_id,
                            scope.principal_id,
                            request_id,
                            scope.session_handle,
                            selected_agent,
                            normalized_title,
                            normalized_goal,
                            attachment_json,
                            int(tools_enabled),
                            priority,
                            notifications_json,
                            TaskDefinitionState.ACTIVE.value,
                            1,
                            None,
                            now,
                            now,
                        ),
                    )
                    db.execute(
                        "INSERT INTO task_launch_policies(task_id, policy_json) VALUES (?, ?)",
                        (task_id, policy_json),
                    )
                    row = db.execute(
                        """SELECT tasks.*, task_launch_policies.policy_json,
                                  0 AS execution_count
                           FROM tasks JOIN task_launch_policies USING(task_id)
                           WHERE task_id=?""",
                        (task_id,),
                    ).fetchone()
                    if row is None:
                        raise RuntimeError("Task definition was not persisted")
                    return self._definition_record(row), True
            except sqlite3.IntegrityError as exc:
                if "tasks.task_id" in str(exc):
                    continue
                raise
        raise RuntimeError("Could not allocate a unique Task definition ID")

    def get_task_definition(
        self,
        principal_id: str,
        task_id: str,
    ) -> TaskDefinitionRecord:
        principal = self._normalize_identifier(
            principal_id, label="principal_id", limit=256
        )
        normalized_task_id = self._normalize_identifier(
            task_id, label="task_id", limit=128
        )
        with self._connect() as db:
            row = db.execute(
                """SELECT tasks.*, task_launch_policies.policy_json,
                          (SELECT COUNT(*) FROM task_executions
                           WHERE task_executions.task_id=tasks.task_id)
                              AS execution_count
                   FROM tasks JOIN task_launch_policies USING(task_id)
                   WHERE tasks.principal_id=? AND tasks.task_id=?""",
                (principal, normalized_task_id),
            ).fetchone()
        if row is None:
            raise TaskNotFoundError("Task not found")
        return self._definition_record(row)

    def list_task_definitions(
        self,
        principal_id: str,
        *,
        state: TaskDefinitionState | None = None,
        include_archived: bool = False,
        limit: int = 100,
    ) -> tuple[TaskDefinitionRecord, ...]:
        principal = self._normalize_identifier(
            principal_id, label="principal_id", limit=256
        )
        if not 1 <= limit <= 200:
            raise ValueError("Task list limit must be between 1 and 200")
        clauses = ["tasks.principal_id=?"]
        parameters: list[object] = [principal]
        if state is not None:
            clauses.append("tasks.state=?")
            parameters.append(state.value)
        elif not include_archived:
            clauses.append("tasks.state<>?")
            parameters.append(TaskDefinitionState.ARCHIVED.value)
        parameters.append(limit)
        with self._connect() as db:
            rows = db.execute(
                """SELECT tasks.*, task_launch_policies.policy_json,
                          (SELECT COUNT(*) FROM task_executions
                           WHERE task_executions.task_id=tasks.task_id)
                              AS execution_count
                   FROM tasks JOIN task_launch_policies USING(task_id)
                   WHERE """
                + " AND ".join(clauses)
                + " ORDER BY tasks.updated_at DESC, tasks.task_id DESC LIMIT ?",
                tuple(parameters),
            ).fetchall()
        return tuple(self._definition_record(row) for row in rows)

    def update_task_definition(
        self,
        principal_id: str,
        task_id: str,
        *,
        title: str | None = None,
        goal: str | None = None,
        attachments: tuple[ArtifactAttachment, ...] | None = None,
        tools_enabled: bool | None = None,
        priority: int | None = None,
        launch_policy: TaskLaunchPolicy | None = None,
        notification_policy: dict[str, bool] | None = None,
        expected_revision: int | None = None,
    ) -> TaskDefinitionRecord:
        current = self.get_task_definition(principal_id, task_id)
        if expected_revision is not None and current.revision != expected_revision:
            raise TaskTransitionError("Task definition changed; refresh before editing")
        next_goal = current.goal if goal is None else goal.strip()
        if not next_goal or len(next_goal) > 200_000:
            raise ValueError("Task goal must contain 1-200000 characters")
        next_title = self._task_title(
            current.title if title is None else title,
            next_goal,
        )
        next_priority = current.priority if priority is None else priority
        if not 0 <= next_priority <= 9:
            raise ValueError("Task priority must be between 0 and 9")
        next_attachments = current.attachments if attachments is None else attachments
        next_notifications = (
            current.notification_policy
            if notification_policy is None
            else notification_policy
        )
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                """UPDATE tasks SET title=?, goal=?, attachments_json=?,
                       tools_enabled=?, priority=?, notification_policy_json=?,
                       revision=revision+1, updated_at=?
                   WHERE principal_id=? AND task_id=? AND revision=?""",
                (
                    next_title,
                    next_goal,
                    self._attachments_payload(next_attachments),
                    int(current.tools_enabled if tools_enabled is None else tools_enabled),
                    next_priority,
                    self._notification_policy_payload(next_notifications),
                    now,
                    current.principal_id,
                    current.task_id,
                    current.revision,
                ),
            ).rowcount
            if changed != 1:
                raise TaskTransitionError("Task definition changed; refresh before editing")
            if launch_policy is not None:
                db.execute(
                    "UPDATE task_launch_policies SET policy_json=? WHERE task_id=?",
                    (launch_policy.model_dump_json(), current.task_id),
                )
        return self.get_task_definition(principal_id, task_id)

    def set_task_definition_state(
        self,
        principal_id: str,
        task_id: str,
        state: TaskDefinitionState,
    ) -> TaskDefinitionRecord:
        current = self.get_task_definition(principal_id, task_id)
        if current.state is state:
            return current
        now = self._clock()
        with self._connect() as db:
            db.execute(
                """UPDATE tasks SET state=?, revision=revision+1, updated_at=?
                   WHERE principal_id=? AND task_id=?""",
                (state.value, now, current.principal_id, current.task_id),
            )
        return self.get_task_definition(principal_id, task_id)

    def bind_task_launch(
        self,
        principal_id: str,
        task_id: str,
        *,
        provider_kind: str,
        provider_id: str,
    ) -> None:
        definition = self.get_task_definition(principal_id, task_id)
        kind = provider_kind.strip()
        if kind not in {"schedule", "event"}:
            raise ValueError("Task launch provider kind is invalid")
        normalized_provider_id = self._normalize_identifier(
            provider_id,
            label="provider_id",
            limit=128,
        )
        with self._connect() as db:
            db.execute(
                """INSERT INTO task_launch_bindings(task_id, provider_kind, provider_id)
                   VALUES (?, ?, ?)
                   ON CONFLICT(provider_kind, provider_id) DO UPDATE SET
                       task_id=excluded.task_id""",
                (definition.task_id, kind, normalized_provider_id),
            )

    def task_for_launch(
        self,
        principal_id: str,
        *,
        provider_kind: str,
        provider_id: str,
    ) -> TaskDefinitionRecord:
        with self._connect() as db:
            row = db.execute(
                """SELECT tasks.*, task_launch_policies.policy_json,
                          (SELECT COUNT(*) FROM task_executions
                           WHERE task_executions.task_id=tasks.task_id)
                              AS execution_count
                   FROM task_launch_bindings
                   JOIN tasks USING(task_id)
                   JOIN task_launch_policies USING(task_id)
                   WHERE tasks.principal_id=? AND provider_kind=? AND provider_id=?""",
                (principal_id, provider_kind.strip(), provider_id.strip()),
            ).fetchone()
        if row is None:
            raise TaskNotFoundError("Task launch binding not found")
        return self._definition_record(row)

    def launch_binding_for_task(
        self,
        principal_id: str,
        task_id: str,
    ) -> tuple[str, str] | None:
        definition = self.get_task_definition(principal_id, task_id)
        with self._connect() as db:
            row = db.execute(
                """SELECT provider_kind, provider_id FROM task_launch_bindings
                   WHERE task_id=?""",
                (definition.task_id,),
            ).fetchone()
        if row is None:
            return None
        return str(row["provider_kind"]), str(row["provider_id"])

    def unbind_launch(self, principal_id: str, task_id: str) -> tuple[str, str] | None:
        definition = self.get_task_definition(principal_id, task_id)
        with self._connect() as db:
            row = db.execute(
                "SELECT provider_kind, provider_id FROM task_launch_bindings WHERE task_id=?",
                (definition.task_id,),
            ).fetchone()
            db.execute("DELETE FROM task_launch_bindings WHERE task_id=?", (definition.task_id,))
        if row is None:
            return None
        return str(row["provider_kind"]), str(row["provider_id"])

    def delete_task_definition(
        self,
        principal_id: str,
        task_id: str,
        *,
        allow_active: bool = False,
    ) -> tuple[str, ...]:
        definition = self.get_task_definition(principal_id, task_id)
        executions = self.list_task_executions(principal_id, task_id, limit=200)
        active = tuple(
            execution.execution_id
            for execution in executions
            if execution.state not in TERMINAL_TASK_STATES
        )
        if active and not allow_active:
            raise TaskTransitionError(
                "Task has active executions; stop them before deleting"
            )
        execution_ids = tuple(execution.execution_id for execution in executions)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for execution_id in execution_ids:
                db.execute("DELETE FROM runtime_tasks WHERE task_id=?", (execution_id,))
            db.execute(
                "DELETE FROM tasks WHERE principal_id=? AND task_id=?",
                (definition.principal_id, definition.task_id),
            )
        return execution_ids
