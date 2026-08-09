from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from pc_assistant.agent_runtime.session_store import RuntimeSessionRepository
from pc_assistant.automation import (
    OccurrenceState,
    ScheduleDispatcher,
    ScheduleKind,
    ScheduleRepository,
    ScheduleSpec,
)


@dataclass
class _Clock:
    value: float

    def __call__(self) -> float:
        return self.value


class _Tasks:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = []

    async def create(self, scope, **kwargs):
        self.calls.append((scope, kwargs))
        if self.fail:
            raise RuntimeError("task service unavailable")
        return SimpleNamespace(task_id="task-a")


def _components(tmp_path: Path, clock: _Clock, tasks: _Tasks):
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
        retry_base_seconds=10.0,
    )
    dispatcher = ScheduleDispatcher(repository, tasks)
    return repository, dispatcher, scope


@pytest.mark.asyncio
async def test_dispatcher_creates_task_with_stable_occurrence_request_id(
    tmp_path: Path,
) -> None:
    clock = _Clock(100.0)
    tasks = _Tasks()
    repository, dispatcher, scope = _components(tmp_path, clock, tasks)
    repository.create(
        scope,
        client_request_id="request-a",
        goal="prepare report",
        spec=ScheduleSpec(kind=ScheduleKind.ONE_TIME, run_at=101.0),
        tools_enabled=False,
        priority=4,
    )
    clock.value = 101.0

    assert await dispatcher.dispatch_once() is True
    assert await dispatcher.dispatch_once() is False

    called_scope, request = tasks.calls[0]
    assert called_scope == scope
    assert request["client_request_id"].startswith("schedule:")
    assert request["goal"] == "prepare report"
    assert request["tools_enabled"] is False
    assert request["priority"] == 4
    occurrence_id = request["client_request_id"].removeprefix("schedule:")
    occurrence = repository.get_occurrence(occurrence_id)
    assert occurrence.state is OccurrenceState.TASK_CREATED
    assert occurrence.task_id == "task-a"


@pytest.mark.asyncio
async def test_dispatcher_persists_retry_instead_of_losing_occurrence(
    tmp_path: Path,
) -> None:
    clock = _Clock(100.0)
    tasks = _Tasks(fail=True)
    repository, dispatcher, scope = _components(tmp_path, clock, tasks)
    repository.create(
        scope,
        client_request_id="request-a",
        goal="prepare report",
        spec=ScheduleSpec(kind=ScheduleKind.ONE_TIME, run_at=101.0),
    )
    clock.value = 101.0

    assert await dispatcher.dispatch_once() is True
    occurrence_id = tasks.calls[0][1]["client_request_id"].removeprefix("schedule:")
    failed = repository.get_occurrence(occurrence_id)

    assert failed.state is OccurrenceState.RETRY_WAIT
    assert failed.failure_code == "RuntimeError"
    assert failed.next_attempt_at == 111.0
