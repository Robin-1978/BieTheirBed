from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from pc_assistant.agent_runtime.session_store import RuntimeSessionRepository
from pc_assistant.automation import (
    OccurrenceState,
    ScheduleKind,
    ScheduleRepository,
    ScheduleSpec,
    ScheduleState,
)
from pc_assistant.automation.repository import (
    ScheduleIdempotencyConflictError,
    ScheduleNotFoundError,
)


@dataclass
class _Clock:
    value: float

    def __call__(self) -> float:
        return self.value


def _repository(
    tmp_path: Path,
    clock: _Clock,
    *,
    max_delivery_attempts: int = 5,
):
    database = tmp_path / "assistant.db"
    sessions = RuntimeSessionRepository(
        database,
        handle_factory=lambda: "session-a",
    )
    scope = sessions.create("principal-a")
    repository = ScheduleRepository(
        database,
        schedule_id_factory=lambda: "schedule-a",
        clock=clock,
        max_delivery_attempts=max_delivery_attempts,
        retry_base_seconds=10.0,
    )
    return repository, scope


def test_create_schedule_is_owned_and_idempotent(tmp_path: Path) -> None:
    clock = _Clock(100.0)
    repository, scope = _repository(tmp_path, clock)
    spec = ScheduleSpec(kind=ScheduleKind.ONE_TIME, run_at=200.0)

    created, changed = repository.create(
        scope,
        client_request_id="request-a",
        goal="prepare report",
        spec=spec,
    )
    repeated, repeated_changed = repository.create(
        scope,
        client_request_id="request-a",
        goal="prepare report",
        spec=spec,
    )

    assert changed is True
    assert repeated_changed is False
    assert repeated == created
    assert created.next_fire_at == 200.0
    with pytest.raises(ScheduleNotFoundError):
        repository.get("principal-b", created.schedule_id)
    with pytest.raises(ScheduleIdempotencyConflictError):
        repository.create(
            scope,
            client_request_id="request-a",
            goal="different goal",
            spec=spec,
        )


def test_one_time_claim_is_durable_and_completes_schedule(tmp_path: Path) -> None:
    clock = _Clock(100.0)
    repository, scope = _repository(tmp_path, clock)
    schedule, _ = repository.create(
        scope,
        client_request_id="request-a",
        goal="prepare report",
        spec=ScheduleSpec(kind=ScheduleKind.ONE_TIME, run_at=200.0),
    )
    assert repository.claim_due("worker-a") is None

    clock.value = 200.0
    occurrence = repository.claim_due("worker-a", lease_seconds=30.0)

    assert occurrence is not None
    assert occurrence.state is OccurrenceState.CLAIMED
    assert occurrence.ordinal == 1
    assert occurrence.attempt_count == 1
    assert occurrence.lease_expires_at == 230.0
    completed = repository.get(scope.principal_id, schedule.schedule_id)
    assert completed.state is ScheduleState.COMPLETED
    assert completed.next_fire_at is None
    assert completed.last_fire_at == 200.0
    assert completed.fire_count == 1


def test_expired_claim_reuses_same_occurrence_identity(tmp_path: Path) -> None:
    clock = _Clock(100.0)
    repository, scope = _repository(tmp_path, clock)
    repository.create(
        scope,
        client_request_id="request-a",
        goal="prepare report",
        spec=ScheduleSpec(kind=ScheduleKind.ONE_TIME, run_at=101.0),
    )
    clock.value = 101.0
    first = repository.claim_due("worker-a", lease_seconds=10.0)
    assert first is not None

    clock.value = 111.0
    reclaimed = repository.claim_due("worker-b", lease_seconds=10.0)

    assert reclaimed is not None
    assert reclaimed.occurrence_id == first.occurrence_id
    assert reclaimed.attempt_count == 2
    assert reclaimed.lease_owner == "worker-b"


def test_expired_claim_is_bounded_when_worker_never_checkpoints(tmp_path: Path) -> None:
    clock = _Clock(100.0)
    repository, scope = _repository(
        tmp_path,
        clock,
        max_delivery_attempts=2,
    )
    repository.create(
        scope,
        client_request_id="request-a",
        goal="prepare report",
        spec=ScheduleSpec(kind=ScheduleKind.ONE_TIME, run_at=101.0),
    )
    clock.value = 101.0
    first = repository.claim_due("worker-a", lease_seconds=10.0)
    assert first is not None
    clock.value = 111.0
    second = repository.claim_due("worker-b", lease_seconds=10.0)
    assert second is not None

    clock.value = 121.0
    assert repository.claim_due("worker-c", lease_seconds=10.0) is None
    dead = repository.get_occurrence(first.occurrence_id)
    assert dead.state is OccurrenceState.DEAD
    assert dead.failure_code == "delivery_lease_exhausted"


def test_interval_claim_skips_missed_backlog_without_drift(tmp_path: Path) -> None:
    clock = _Clock(100.0)
    repository, scope = _repository(tmp_path, clock)
    schedule, _ = repository.create(
        scope,
        client_request_id="request-a",
        goal="check inbox",
        spec=ScheduleSpec(
            kind=ScheduleKind.INTERVAL,
            run_at=160.0,
            interval_seconds=60.0,
        ),
    )

    clock.value = 401.0
    occurrence = repository.claim_due("worker-a")

    assert occurrence is not None
    assert occurrence.scheduled_for == 160.0
    updated = repository.get(scope.principal_id, schedule.schedule_id)
    assert updated.next_fire_at == 460.0
    assert repository.claim_due("worker-b") is None


def test_task_creation_is_idempotent_for_occurrence(tmp_path: Path) -> None:
    clock = _Clock(100.0)
    repository, scope = _repository(tmp_path, clock)
    repository.create(
        scope,
        client_request_id="request-a",
        goal="prepare report",
        spec=ScheduleSpec(kind=ScheduleKind.ONE_TIME, run_at=101.0),
    )
    clock.value = 101.0
    occurrence = repository.claim_due("worker-a")
    assert occurrence is not None

    created = repository.mark_task_created(occurrence.occurrence_id, "task-a")
    repeated = repository.mark_task_created(occurrence.occurrence_id, "task-a")

    assert created.state is OccurrenceState.TASK_CREATED
    assert repeated == created
    assert created.task_id == "task-a"
    assert repository.claim_due("worker-b") is None


def test_pause_and_resume_skip_missed_interval_occurrences(tmp_path: Path) -> None:
    clock = _Clock(100.0)
    repository, scope = _repository(tmp_path, clock)
    schedule, _ = repository.create(
        scope,
        client_request_id="request-a",
        goal="check inbox",
        spec=ScheduleSpec(
            kind=ScheduleKind.INTERVAL,
            run_at=160.0,
            interval_seconds=60.0,
        ),
    )

    paused = repository.pause(scope.principal_id, schedule.schedule_id)
    clock.value = 401.0
    assert repository.claim_due("worker-a") is None
    resumed = repository.resume(scope.principal_id, schedule.schedule_id)

    assert paused.state is ScheduleState.PAUSED
    assert resumed.state is ScheduleState.ACTIVE
    assert resumed.next_fire_at == 460.0
    assert repository.claim_due("worker-a") is None


def test_missed_one_time_schedule_completes_when_resumed(tmp_path: Path) -> None:
    clock = _Clock(100.0)
    repository, scope = _repository(tmp_path, clock)
    schedule, _ = repository.create(
        scope,
        client_request_id="request-a",
        goal="prepare report",
        spec=ScheduleSpec(kind=ScheduleKind.ONE_TIME, run_at=200.0),
    )
    repository.pause(scope.principal_id, schedule.schedule_id)
    clock.value = 201.0

    resumed = repository.resume(scope.principal_id, schedule.schedule_id)

    assert resumed.state is ScheduleState.COMPLETED
    assert resumed.next_fire_at is None


def test_delivery_failure_uses_bounded_exponential_backoff(tmp_path: Path) -> None:
    clock = _Clock(100.0)
    repository, scope = _repository(
        tmp_path,
        clock,
        max_delivery_attempts=2,
    )
    repository.create(
        scope,
        client_request_id="request-a",
        goal="prepare report",
        spec=ScheduleSpec(kind=ScheduleKind.ONE_TIME, run_at=101.0),
    )
    clock.value = 101.0
    occurrence = repository.claim_due("worker-a")
    assert occurrence is not None

    retry = repository.mark_delivery_failed(
        occurrence.occurrence_id,
        failure_code="task_capacity",
    )
    assert retry.state is OccurrenceState.RETRY_WAIT
    assert retry.next_attempt_at == 111.0
    assert repository.claim_due("worker-b") is None

    clock.value = 111.0
    second = repository.claim_due("worker-b")
    assert second is not None and second.attempt_count == 2
    dead = repository.mark_delivery_failed(
        second.occurrence_id,
        failure_code="task_capacity",
    )
    assert dead.state is OccurrenceState.DEAD
    assert dead.next_attempt_at is None
