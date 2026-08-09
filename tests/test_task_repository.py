from __future__ import annotations

from pathlib import Path

import pytest

from pc_assistant.agent_runtime.contracts import RuntimeScope
from pc_assistant.agent_runtime.session_store import RuntimeSessionRepository
from pc_assistant.tasks import (
    TaskCapacityError,
    TaskEventPayload,
    TaskIdempotencyConflictError,
    TaskNotFoundError,
    TaskRepository,
    TaskState,
    TaskTransitionError,
)


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
        "content_delta",
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
    assert terminal.event_type == "completed"
    events = repository.list_events(scope.principal_id, task.task_id)
    assert [event.event_seq for event in events] == [1, 2, 3, 4]
    assert [event.event_type for event in events] == [
        "task_created",
        "state_changed",
        "content_delta",
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
