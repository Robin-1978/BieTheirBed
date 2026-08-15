from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from knoa_platform.agent_runtime.contracts import RuntimeScope
from knoa_platform.agent_runtime.session_store import RuntimeSessionRepository
from knoa_platform.agent_runtime.tool_step import ProposedToolCall
from knoa_platform.tasks import (
    TaskCapacityError,
    TaskEventPayload,
    TaskIdempotencyConflictError,
    TaskNotFoundError,
    TaskRepository,
    TaskDefinitionState,
    TaskLaunchPolicy,
    TaskLaunchKind,
    TaskLaunchReason,
    TaskState,
    TaskTraceEntry,
    TaskTransitionError,
)
from knoa_platform.tasks.identity import task_approval_action_id


def _repository(tmp_path: Path) -> tuple[TaskRepository, RuntimeScope]:
    database = tmp_path / "assistant.db"
    sessions = RuntimeSessionRepository(
        database,
        handle_factory=lambda: "session-a",
    )
    scope = sessions.create("principal-a")
    return (
        TaskRepository(
            database,
            task_id_factory=lambda: "task-a",
            approval_id_factory=lambda: "approval-a",
            clock=lambda: 1000.0,
        ),
        scope,
    )


def test_create_is_idempotent_and_persists_first_event(tmp_path: Path) -> None:
    repository, scope = _repository(tmp_path)

    created, was_created = repository.create(
        scope,
        client_request_id="request-a",
        goal="finish the report",
        priority=4,
    )
    repeated, repeated_created = repository.create(
        scope,
        client_request_id="request-a",
        goal="finish the report",
        priority=4,
    )

    assert was_created is True
    assert repeated_created is False
    assert repeated == created
    assert created.state is TaskState.QUEUED
    assert created.next_event_seq == 2
    events = repository.list_events(scope.principal_id, created.task_id)
    assert [(event.event_seq, event.event_type) for event in events] == [
        (1, "task_created")
    ]
    assert events[0].payload.state is TaskState.QUEUED


def test_idempotency_key_rejects_a_different_request(tmp_path: Path) -> None:
    repository, scope = _repository(tmp_path)
    repository.create(scope, client_request_id="request-a", goal="first")

    with pytest.raises(TaskIdempotencyConflictError):
        repository.create(scope, client_request_id="request-a", goal="second")


def test_create_enforces_active_capacity_after_idempotency_lookup(
    tmp_path: Path,
) -> None:
    database = tmp_path / "assistant.db"
    sessions = RuntimeSessionRepository(
        database,
        handle_factory=lambda: "session-a",
    )
    scope = sessions.create("principal-a")
    task_ids = iter(("task-a", "task-b"))
    repository = TaskRepository(
        database,
        task_id_factory=lambda: next(task_ids),
        max_active_tasks=1,
        max_active_tasks_per_principal=1,
    )
    created, _ = repository.create(
        scope,
        client_request_id="request-a",
        goal="first",
    )

    repeated, repeated_created = repository.create(
        scope,
        client_request_id="request-a",
        goal="first",
    )
    assert repeated == created
    assert repeated_created is False
    with pytest.raises(TaskCapacityError):
        repository.create(
            scope,
            client_request_id="request-b",
            goal="second",
        )

    repository.request_cancel(scope.principal_id, created.task_id)
    second, second_created = repository.create(
        scope,
        client_request_id="request-b",
        goal="second",
    )
    assert second_created is True
    assert second.task_id == "task-b"


def test_claim_and_transitions_append_gap_free_events(tmp_path: Path) -> None:
    repository, scope = _repository(tmp_path)
    task, _ = repository.create(
        scope,
        client_request_id="request-a",
        goal="finish the report",
    )

    claimed = repository.claim_next("worker-a", lease_seconds=30)
    assert claimed is not None
    assert claimed.state is TaskState.RUNNING
    assert claimed.attempt_count == 1
    assert claimed.lease_owner == "worker-a"
    repository.append_event(
        scope.principal_id,
        task.task_id,
        "warning",
        TaskEventPayload(content="working"),
    )
    completed, terminal = repository.transition(
        scope.principal_id,
        task.task_id,
        TaskState.COMPLETED,
        final_summary="done",
    )

    assert completed.state is TaskState.COMPLETED
    assert completed.final_summary == "done"
    assert completed.lease_owner == ""
    attempts = repository.list_attempts(scope.principal_id, task.task_id)
    assert len(attempts) == 1
    assert attempts[0].state.value == "completed"
    assert terminal.event_type == "completed"
    events = repository.list_events(scope.principal_id, task.task_id)
    assert [event.event_seq for event in events] == [1, 2, 3, 4]
    assert [event.event_type for event in events] == [
        "task_created",
        "state_changed",
        "warning",
        "completed",
    ]


def test_claim_serializes_one_session_without_blocking_other_sessions(
    tmp_path: Path,
) -> None:
    database = tmp_path / "assistant.db"
    handles = iter(("session-a", "session-b"))
    sessions = RuntimeSessionRepository(
        database,
        handle_factory=lambda: next(handles),
    )
    scope_a = sessions.create("principal-a")
    scope_b = sessions.create("principal-a")
    task_ids = iter(("task-a1", "task-a2", "task-b1"))
    repository = TaskRepository(
        database,
        task_id_factory=lambda: next(task_ids),
    )
    first_a, _ = repository.create(
        scope_a,
        client_request_id="request-a1",
        goal="first A",
    )
    second_a, _ = repository.create(
        scope_a,
        client_request_id="request-a2",
        goal="second A",
    )
    first_b, _ = repository.create(
        scope_b,
        client_request_id="request-b1",
        goal="first B",
    )

    first_claim = repository.claim_next("worker-a")
    second_claim = repository.claim_next("worker-b")

    assert first_claim is not None
    assert first_claim.task_id == first_a.task_id
    assert second_claim is not None
    assert second_claim.task_id == first_b.task_id
    assert repository.claim_next("worker-c") is None

    repository.transition(
        scope_a.principal_id,
        first_a.task_id,
        TaskState.COMPLETED,
        final_summary="done",
    )
    third_claim = repository.claim_next("worker-c")
    assert third_claim is not None
    assert third_claim.task_id == second_a.task_id


def test_terminal_task_rejects_events_and_transitions(tmp_path: Path) -> None:
    repository, scope = _repository(tmp_path)
    task, _ = repository.create(
        scope,
        client_request_id="request-a",
        goal="finish the report",
    )
    repository.claim_next("worker-a")
    repository.transition(
        scope.principal_id,
        task.task_id,
        TaskState.FAILED,
        failure_code="provider_failed",
    )

    with pytest.raises(TaskTransitionError):
        repository.append_event(
            scope.principal_id,
            task.task_id,
            "warning",
            TaskEventPayload(content="late"),
        )
    with pytest.raises(TaskTransitionError):
        repository.transition(
            scope.principal_id,
            task.task_id,
            TaskState.QUEUED,
        )


def test_streaming_output_is_rejected_from_durable_event_journal(
    tmp_path: Path,
) -> None:
    repository, scope = _repository(tmp_path)
    task, _ = repository.create(
        scope,
        client_request_id="request-a",
        goal="finish the report",
    )
    repository.claim_next("worker-a")

    with pytest.raises(ValueError, match="ExecutionTrace"):
        repository.append_event(
            scope.principal_id,
            task.task_id,
            "content_delta",
            TaskEventPayload(content="token"),
        )


def test_expired_terminal_trace_is_compacted_without_losing_result(
    tmp_path: Path,
) -> None:
    now = [1000.0]
    database = tmp_path / "trace-retention.db"
    sessions = RuntimeSessionRepository(database, handle_factory=lambda: "session-a")
    scope = sessions.create("principal-a")
    repository = TaskRepository(
        database,
        task_id_factory=lambda: "task-a",
        clock=lambda: now[0],
        trace_retention_seconds=60,
    )
    task, _ = repository.create(
        scope,
        client_request_id="request-a",
        goal="finish the report",
    )
    repository.claim_next("worker-a")
    repository.save_trace(
        scope.principal_id,
        task.task_id,
        entries=(
            TaskTraceEntry(
                entry_type="reasoning",
                iteration=1,
                content="private draft",
                occurred_at=now[0],
            ),
            TaskTraceEntry(
                entry_type="tool_call",
                iteration=1,
                tool_name="read_file",
                tool_args={"path": "/tmp/a"},
                occurred_at=now[0],
            ),
        ),
        final_output="done",
    )
    repository.transition(
        scope.principal_id,
        task.task_id,
        TaskState.COMPLETED,
        final_summary="done",
    )

    now[0] += 61
    assert repository.compact_expired_traces() == 1
    trace = repository.get_trace(scope.principal_id, task.task_id)
    assert trace is not None
    assert trace.compacted_at == now[0]
    assert trace.final_output == "done"
    assert [entry.entry_type for entry in trace.entries] == ["tool_call"]
    assert trace.entries[0].tool_args == {}
    assert repository.get(scope.principal_id, task.task_id).final_summary == "done"


def test_task_ownership_does_not_reveal_foreign_identity(tmp_path: Path) -> None:
    repository, scope = _repository(tmp_path)
    task, _ = repository.create(
        scope,
        client_request_id="request-a",
        goal="finish the report",
    )

    with pytest.raises(TaskNotFoundError):
        repository.get("principal-b", task.task_id)
    with pytest.raises(TaskNotFoundError):
        repository.list_events("principal-b", task.task_id)


def test_task_list_is_owned_filtered_and_cursor_paginated(tmp_path: Path) -> None:
    database = tmp_path / "assistant.db"
    sessions = RuntimeSessionRepository(
        database,
        handle_factory=lambda: "session-a",
    )
    scope = sessions.create("principal-a")
    task_ids = iter(("task-a", "task-b", "task-c"))
    repository = TaskRepository(
        database,
        task_id_factory=lambda: next(task_ids),
        clock=lambda: 1000.0,
    )
    for index in range(3):
        repository.create(
            scope,
            client_request_id=f"request-{index}",
            goal=f"task {index}",
        )

    first, cursor = repository.list_tasks("principal-a", limit=2)
    second, final_cursor = repository.list_tasks(
        "principal-a",
        limit=2,
        cursor=cursor,
    )
    foreign, _ = repository.list_tasks("principal-b")

    assert [task.task_id for task in first] == ["task-c", "task-b"]
    assert cursor
    assert [task.task_id for task in second] == ["task-a"]
    assert final_cursor == ""
    assert foreign == ()
    with pytest.raises(ValueError, match="cursor"):
        repository.list_tasks("principal-a", cursor="not-a-cursor")


def test_repository_reopens_persisted_task_and_events(tmp_path: Path) -> None:
    repository, scope = _repository(tmp_path)
    task, _ = repository.create(
        scope,
        client_request_id="request-a",
        goal="finish the report",
    )

    reopened = TaskRepository(tmp_path / "assistant.db")

    assert reopened.get(scope.principal_id, task.task_id) == task
    assert reopened.list_events(scope.principal_id, task.task_id)[0].event_type == (
        "task_created"
    )


def test_task_definition_keeps_multiple_execution_snapshots(tmp_path: Path) -> None:
    database = tmp_path / "assistant.db"
    sessions = RuntimeSessionRepository(database, handle_factory=lambda: "session-a")
    scope = sessions.create("principal-a")
    execution_ids = iter(("execution-a", "execution-b"))
    repository = TaskRepository(
        database,
        task_id_factory=lambda: next(execution_ids),
        definition_id_factory=lambda: "task-a",
        clock=lambda: 1000.0,
    )
    definition, created = repository.create_task_definition(
        scope,
        client_request_id="definition-request-a",
        title="Weekly report",
        goal="Prepare the first report",
        launch_policy=TaskLaunchPolicy(),
    )
    assert created is True
    assert definition.task_id == "task-a"
    assert definition.state is TaskDefinitionState.ACTIVE
    assert definition.execution_count == 0

    first, _ = repository.create(
        scope,
        client_request_id="execution-request-a",
        goal=definition.goal,
    )
    first_snapshot = repository.link_task_execution(
        scope.principal_id,
        definition.task_id,
        first.task_id,
        launch_reason=TaskLaunchReason.CREATED,
    )
    repository.request_cancel(scope.principal_id, first.task_id)
    updated = repository.update_task_definition(
        scope.principal_id,
        definition.task_id,
        goal="Prepare the revised report",
        expected_revision=1,
    )
    second, _ = repository.create(
        scope,
        client_request_id="execution-request-b",
        goal=updated.goal,
    )
    repository.link_task_execution(
        scope.principal_id,
        definition.task_id,
        second.task_id,
        launch_reason=TaskLaunchReason.MANUAL,
    )

    executions = repository.list_task_executions(
        scope.principal_id,
        definition.task_id,
    )
    current = repository.get_task_definition(scope.principal_id, definition.task_id)
    assert current.goal == "Prepare the revised report"
    assert current.revision == 2
    assert current.execution_count == 2
    assert current.latest_execution_id == "execution-b"
    assert current.latest_execution_state is TaskState.QUEUED
    assert current.latest_execution_updated_at == 1000.0
    assert current.pending_approval_count == 0
    assert [item.execution_id for item in executions] == ["execution-b", "execution-a"]
    assert executions[0].goal_snapshot == "Prepare the revised report"
    assert executions[1].goal_snapshot == first_snapshot.goal_snapshot
    assert executions[1].goal_snapshot == "Prepare the first report"


def test_task_definition_delete_cascades_terminal_executions(tmp_path: Path) -> None:
    database = tmp_path / "assistant.db"
    sessions = RuntimeSessionRepository(database, handle_factory=lambda: "session-a")
    scope = sessions.create("principal-a")
    repository = TaskRepository(
        database,
        task_id_factory=lambda: "execution-a",
        definition_id_factory=lambda: "task-a",
    )
    definition, _ = repository.create_task_definition(
        scope,
        client_request_id="definition-request-a",
        title="Report",
        goal="Prepare report",
    )
    execution, _ = repository.create(
        scope,
        client_request_id="execution-request-a",
        goal=definition.goal,
    )
    repository.link_task_execution(
        scope.principal_id,
        definition.task_id,
        execution.task_id,
        launch_reason=TaskLaunchReason.CREATED,
    )
    with pytest.raises(TaskTransitionError, match="active executions"):
        repository.delete_task_definition(scope.principal_id, definition.task_id)
    repository.request_cancel(scope.principal_id, execution.task_id)

    deleted = repository.delete_task_definition(
        scope.principal_id,
        definition.task_id,
    )

    assert deleted == ("execution-a",)
    with pytest.raises(TaskNotFoundError):
        repository.get_task_definition(scope.principal_id, definition.task_id)
    with pytest.raises(TaskNotFoundError):
        repository.get(scope.principal_id, execution.task_id)


def test_repository_migrates_legacy_stream_events_into_execution_trace(
    tmp_path: Path,
) -> None:
    repository, scope = _repository(tmp_path)
    task, _ = repository.create(
        scope,
        client_request_id="request-a",
        goal="finish the report",
    )
    with sqlite3.connect(tmp_path / "assistant.db") as db:
        payloads = (
            TaskEventPayload(content="think ", iteration=1),
            TaskEventPayload(content="carefully", iteration=1),
            TaskEventPayload(content="done", iteration=1),
        )
        for sequence, (event_type, payload) in enumerate(
            zip(
                ("reasoning_delta", "reasoning_delta", "final_output"),
                payloads,
                strict=True,
            ),
            start=2,
        ):
            db.execute(
                """INSERT INTO runtime_task_events(
                       task_id, event_seq, event_type, payload_json, occurred_at
                   ) VALUES (?, ?, ?, ?, ?)""",
                (task.task_id, sequence, event_type, payload.model_dump_json(), 1000.0),
            )
            db.execute(
                """INSERT INTO runtime_principal_task_events(
                       principal_id, task_id, task_event_seq, occurred_at
                   ) VALUES (?, ?, ?, ?)""",
                (scope.principal_id, task.task_id, sequence, 1000.0),
            )
        db.execute(
            "UPDATE runtime_tasks SET next_event_seq=5 WHERE task_id=?",
            (task.task_id,),
        )

    reopened = TaskRepository(tmp_path / "assistant.db", clock=lambda: 1000.0)
    trace = reopened.get_trace(scope.principal_id, task.task_id)

    assert trace is not None
    assert [entry.entry_type for entry in trace.entries] == [
        "reasoning",
        "final_output",
    ]
    assert trace.entries[0].content == "think carefully"
    assert trace.final_output == "done"
    assert [event.event_type for event in reopened.list_events(
        scope.principal_id,
        task.task_id,
    )] == ["task_created"]
    assert [item.event.event_type for item in reopened.list_principal_events(
        scope.principal_id,
    )] == ["task_created"]


def test_repository_rejects_legacy_task_schema(
    tmp_path: Path,
) -> None:
    database = tmp_path / "assistant.db"
    RuntimeSessionRepository(database)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
            CREATE TABLE runtime_tasks (
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
                UNIQUE(principal_id, client_request_id)
            )
            """
        )

    with pytest.raises(RuntimeError, match="schema is incompatible"):
        TaskRepository(database)


def test_paused_task_requires_explicit_resume(tmp_path: Path) -> None:
    repository, scope = _repository(tmp_path)
    task, _ = repository.create(
        scope,
        client_request_id="request-a",
        goal="finish the report",
    )
    repository.claim_next("worker-a")
    repository.transition(
        scope.principal_id,
        task.task_id,
        TaskState.PAUSED,
        reason="outcome unknown",
    )

    resumed, event = repository.resume(
        scope.principal_id,
        task.task_id,
        reason="user reviewed recovery state",
    )

    assert resumed.state is TaskState.QUEUED
    assert event.event_type == "state_changed"
    assert event.payload.previous_state is TaskState.PAUSED
    assert event.payload.state is TaskState.QUEUED
    assert event.payload.reason == "user reviewed recovery state"
    with pytest.raises(TaskTransitionError):
        repository.resume(scope.principal_id, task.task_id)


def test_approval_is_persistent_idempotent_and_atomically_resolved(
    tmp_path: Path,
) -> None:
    repository, scope = _repository(tmp_path)
    task, _ = repository.create(
        scope,
        client_request_id="request-a",
        goal="publish the report",
    )
    repository.claim_next("worker-a")

    approval, requested, created = repository.request_approval(
        scope.principal_id,
        task.task_id,
        tool_step_id="step-a",
        tool_call_id="call-a",
        tool_name="publish",
        arguments={"document": "report"},
        reason="external_side_effect:high",
    )
    repeated, repeated_event, repeated_created = repository.request_approval(
        scope.principal_id,
        task.task_id,
        tool_step_id="step-a",
        tool_call_id="call-a",
        tool_name="publish",
        arguments={"document": "report"},
        reason="external_side_effect:high",
    )

    assert created is True
    assert repeated_created is False
    assert repeated == approval
    assert repeated_event == requested
    assert requested.event_type == "approval_requested"
    assert repository.get(scope.principal_id, task.task_id).state is (
        TaskState.WAITING_APPROVAL
    )

    resolved, resolved_event, changed = repository.resolve_approval(
        scope.principal_id,
        approval.approval_id,
        approved=True,
        resolved_by="feishu",
    )
    repeated_resolution = repository.resolve_approval(
        scope.principal_id,
        approval.approval_id,
        approved=False,
    )

    assert changed is True
    assert resolved.state.value == "approved"
    assert resolved.resolved_by == "feishu"
    assert resolved_event is not None
    assert resolved_event.event_type == "approval_resolved"
    assert repository.get(scope.principal_id, task.task_id).state is TaskState.RUNNING
    assert repeated_resolution[2] is False
    assert [event.event_seq for event in repository.list_events(
        scope.principal_id,
        task.task_id,
    )] == [1, 2, 3, 4]


def test_resolved_approval_replays_for_same_action_with_new_call_id(
    tmp_path: Path,
) -> None:
    repository, scope = _repository(tmp_path)
    task, _ = repository.create(
        scope,
        client_request_id="request-a",
        goal="retry one failed job",
    )
    repository.claim_next("worker-a")
    first_call = ProposedToolCall(
        call_id="call-before-restart",
        name="gitlab.retry_job",
        arguments={"project": "team/repo", "job_id": 9},
    )
    approval, _, created = repository.request_approval(
        scope.principal_id,
        task.task_id,
        tool_step_id=task_approval_action_id(task.task_id, first_call),
        tool_call_id=first_call.call_id,
        tool_name=first_call.name,
        arguments=first_call.arguments,
        reason="external_side_effect:high",
    )
    repository.resolve_approval(
        scope.principal_id,
        approval.approval_id,
        approved=False,
        resume_state=TaskState.QUEUED,
    )
    repository.claim_next("worker-b")
    replay_call = first_call.model_copy(update={"call_id": "call-after-restart"})
    replay, _, replay_created = repository.request_approval(
        scope.principal_id,
        task.task_id,
        tool_step_id=task_approval_action_id(task.task_id, replay_call),
        tool_call_id=replay_call.call_id,
        tool_name=replay_call.name,
        arguments=replay_call.arguments,
        reason="external_side_effect:high",
    )

    assert created is True
    assert replay_created is False
    assert replay.approval_id == approval.approval_id
    assert replay.state.value == "denied"


def test_cancelling_waiting_task_cancels_pending_approval(tmp_path: Path) -> None:
    repository, scope = _repository(tmp_path)
    task, _ = repository.create(
        scope,
        client_request_id="request-a",
        goal="publish the report",
    )
    repository.claim_next("worker-a")
    approval, _, _ = repository.request_approval(
        scope.principal_id,
        task.task_id,
        tool_step_id="step-a",
        tool_call_id="call-a",
        tool_name="publish",
        arguments={},
    )

    result, event = repository.request_cancel(
        scope.principal_id,
        task.task_id,
        reason="user cancelled",
    )

    assert result.state is TaskState.CANCELLED
    assert event is not None and event.event_type == "cancelled"
    assert repository.get_approval(
        scope.principal_id,
        approval.approval_id,
    ).state.value == "cancelled"
    assert repository.get(scope.principal_id, task.task_id).state is (
        TaskState.CANCELLED
    )


def test_pause_request_waits_for_running_task_safe_boundary(tmp_path: Path) -> None:
    repository, scope = _repository(tmp_path)
    task, _ = repository.create(
        scope,
        client_request_id="request-a",
        goal="prepare the report",
    )
    repository.claim_next("worker-a")

    result, event = repository.request_pause(
        scope.principal_id,
        task.task_id,
        reason="pause from phone",
    )

    current = repository.get(scope.principal_id, task.task_id)
    assert result.state is TaskState.RUNNING
    assert current.state is TaskState.RUNNING
    assert current.phase == "pause_requested"
    assert event is not None and event.event_type == "warning"

    paused, paused_event = repository.transition(
        scope.principal_id,
        task.task_id,
        TaskState.PAUSED,
        phase="manual_pause",
        reason="safe boundary",
    )
    assert paused.state is TaskState.PAUSED
    assert paused_event.payload.phase == "manual_pause"
    assert repository.list_attempts(
        scope.principal_id,
        task.task_id,
    )[0].failure_code == "paused"


def test_pause_queued_task_is_immediate_and_idempotent(tmp_path: Path) -> None:
    repository, scope = _repository(tmp_path)
    task, _ = repository.create(
        scope,
        client_request_id="request-a",
        goal="prepare the report",
    )

    first, event = repository.request_pause(scope.principal_id, task.task_id)
    repeated, repeated_event = repository.request_pause(
        scope.principal_id,
        task.task_id,
    )

    assert first.state is TaskState.PAUSED
    assert repeated.state is TaskState.PAUSED
    assert event is not None and event.payload.state is TaskState.PAUSED
    assert repeated_event is None


def test_restart_preserves_pending_approval_and_interrupts_old_attempt(
    tmp_path: Path,
) -> None:
    repository, scope = _repository(tmp_path)
    task, _ = repository.create(
        scope,
        client_request_id="request-a",
        goal="publish the report",
    )
    repository.claim_next("worker-a")
    approval, _, _ = repository.request_approval(
        scope.principal_id,
        task.task_id,
        tool_step_id="step-a",
        tool_call_id="call-a",
        tool_name="publish",
        arguments={},
    )

    recovered = repository.recover_interrupted()

    waiting = repository.get(scope.principal_id, task.task_id)
    assert waiting.state is TaskState.WAITING_APPROVAL
    assert waiting.lease_owner == ""
    assert repository.get_approval(
        scope.principal_id,
        approval.approval_id,
    ).state.value == "pending"
    assert repository.list_attempts(
        scope.principal_id,
        task.task_id,
    )[0].state.value == "interrupted"
    assert recovered[-1].event_type == "warning"

    repository.resolve_approval(
        scope.principal_id,
        approval.approval_id,
        approved=True,
        resume_state=TaskState.QUEUED,
    )
    claimed = repository.claim_next("worker-b")
    assert claimed is not None
    assert claimed.attempt_count == 2


def test_approval_ownership_does_not_reveal_foreign_identity(tmp_path: Path) -> None:
    repository, scope = _repository(tmp_path)
    task, _ = repository.create(
        scope,
        client_request_id="request-a",
        goal="publish the report",
    )
    repository.claim_next("worker-a")
    approval, _, _ = repository.request_approval(
        scope.principal_id,
        task.task_id,
        tool_step_id="step-a",
        tool_call_id="call-a",
        tool_name="publish",
        arguments={},
    )

    with pytest.raises(TaskNotFoundError):
        repository.get_approval("principal-b", approval.approval_id)
    with pytest.raises(TaskNotFoundError):
        repository.resolve_approval(
            "principal-b",
            approval.approval_id,
            approved=True,
        )
def test_principal_event_feed_is_ordered_and_owner_scoped(tmp_path: Path) -> None:
    database = tmp_path / "principal-feed.db"
    handles = iter(("feed-session-a", "feed-session-b"))
    sessions = RuntimeSessionRepository(
        database,
        handle_factory=lambda: next(handles),
    )
    scope_a = sessions.create("feed-principal-a")
    scope_b = sessions.create("feed-principal-b")
    task_ids = iter(("feed-task-a", "feed-task-b"))
    repository = TaskRepository(
        database,
        task_id_factory=lambda: next(task_ids),
    )
    task_a, _ = repository.create(
        scope_a,
        client_request_id="feed-request-a",
        goal="task A",
    )
    repository.create(
        scope_b,
        client_request_id="feed-request-b",
        goal="task B",
    )
    repository.claim_next("feed-worker-a", principal_id=scope_a.principal_id)
    repository.append_event(
        scope_a.principal_id,
        task_a.task_id,
        "warning",
        TaskEventPayload(content="working"),
    )

    feed_a = repository.list_principal_events(scope_a.principal_id)
    feed_b = repository.list_principal_events(scope_b.principal_id)

    assert [item.feed_event_id for item in feed_a] == sorted(
        item.feed_event_id for item in feed_a
    )
    assert [item.event.event_type for item in feed_a] == [
        "task_created",
        "state_changed",
    ]
    assert [item.event.event_type for item in feed_b] == ["task_created"]
    assert repository.list_principal_events(
        scope_a.principal_id,
        after_id=feed_a[1].feed_event_id,
    ) == ()


def test_task_launch_policy_rejects_mixed_or_incomplete_configuration() -> None:
    with pytest.raises(ValueError):
        TaskLaunchPolicy(kind=TaskLaunchKind.SCHEDULED)
    with pytest.raises(ValueError):
        TaskLaunchPolicy(
            kind=TaskLaunchKind.EVENT,
            event_source="webhook",
            interval_seconds=60,
        )

    scheduled = TaskLaunchPolicy(
        kind=TaskLaunchKind.SCHEDULED,
        schedule_type="interval",
        interval_seconds=300,
    )
    event = TaskLaunchPolicy(
        kind=TaskLaunchKind.EVENT,
        event_source="webhook",
        source_config={"topic": "build.completed"},
    )
    assert scheduled.interval_seconds == 300
    assert event.event_source == "webhook"


def test_repository_rewrites_legacy_mcp_event_policy(tmp_path: Path) -> None:
    repository, scope = _repository(tmp_path)
    definition, _ = repository.create_task_definition(
        scope,
        client_request_id="legacy-event-definition",
        title="Legacy Jira event",
        goal="Analyze one Jira event",
        launch_policy=TaskLaunchPolicy(),
    )
    database = tmp_path / "assistant.db"
    legacy = (
        '{"kind":"event","schedule_type":null,"run_at":null,'
        '"interval_seconds":null,"cron":"","timezone":"Asia/Shanghai",'
        '"event_source":"mcp:jira","source_config":'
        '{"resource_uri":"jira://assigned-to-me/events"}}'
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE task_launch_policies SET policy_json=? WHERE task_id=?",
            (legacy, definition.task_id),
        )

    reopened = TaskRepository(database)
    migrated = reopened.get_task_definition(scope.principal_id, definition.task_id)

    assert migrated.launch_policy.source_config == {
        "resource_uri_prefix": "jira://assigned-to-me/events",
        "include_root": True,
        "include_descendants": False,
    }
    with sqlite3.connect(database) as connection:
        stored = connection.execute(
            "SELECT policy_json FROM task_launch_policies WHERE task_id=?",
            (definition.task_id,),
        ).fetchone()[0]
    assert '"resource_uri"' not in stored
    assert '"resource_uri_prefix"' in stored
