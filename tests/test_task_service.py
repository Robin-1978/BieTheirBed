from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pc_assistant.agent_runtime.contracts import (
    CancelRequest,
    CancelResult,
    HealthStatus,
    RunRequest,
    RuntimeEvent,
    RuntimeEventPayload,
    RuntimeRunContext,
)
from pc_assistant.agent_runtime.session_store import RuntimeSessionRepository
from pc_assistant.agent_runtime.tool_step import (
    ProposedToolCall,
    ToolOutcomeUnknownError,
)
from pc_assistant.tasks import (
    DurableApprovalService,
    DurableToolCommitService,
    TaskEventHub,
    TaskExecutor,
    TaskLaunchKind,
    TaskLaunchPolicy,
    TaskRepository,
    TaskService,
    TaskState,
)


class _Runtime:
    def __init__(
        self,
        *,
        hold: asyncio.Event | None = None,
        request_confirmation: bool = False,
        unknown_outcome: bool = False,
    ) -> None:
        self.hold = hold
        self.request_confirmation = request_confirmation
        self.unknown_outcome = unknown_outcome
        self.cancellations: list[CancelRequest] = []

    def run(self, context: RuntimeRunContext, request: RunRequest):
        return self._run(context, request)

    async def _run(self, context: RuntimeRunContext, request: RunRequest):
        yield RuntimeEvent(
            event_type="content_delta",
            payload=RuntimeEventPayload(content=f"working:{request.input}"),
        )
        if self.hold is not None:
            hold_task = asyncio.create_task(self.hold.wait())
            cancel_task = asyncio.create_task(context.cancellation.wait())
            done, pending = await asyncio.wait(
                {hold_task, cancel_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if cancel_task in done and context.cancellation.is_set():
                return
        approved = True
        if self.request_confirmation:
            assert context.confirmation is not None
            approved = await context.confirmation.confirm(
                context.scope,
                context.run_id,
                ProposedToolCall(
                    call_id="call-a",
                    name="publish",
                    arguments={"document": "report"},
                ),
                "external_side_effect:high",
            )
        if context.cancellation.is_set():
            return
        if self.unknown_outcome:
            raise ToolOutcomeUnknownError("checkpoint failed")
        yield RuntimeEvent(
            event_type="final_output",
            payload=RuntimeEventPayload(
                content="approved" if approved else "denied"
            ),
        )

    async def cancel(self, scope, request: CancelRequest) -> CancelResult:
        del scope
        self.cancellations.append(request)
        return CancelResult(accepted=True, status="cancelling")

    async def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True)


def _components(
    tmp_path: Path,
    runtime: _Runtime,
    *,
    task_id: str = "task-a",
) -> tuple[TaskService, TaskRepository, object]:
    database = tmp_path / "assistant.db"
    sessions = RuntimeSessionRepository(
        database,
        handle_factory=lambda: "session-a",
    )
    scope = sessions.active("principal-a") or sessions.create("principal-a")
    repository = TaskRepository(
        database,
        task_id_factory=lambda: task_id,
        approval_id_factory=lambda: "approval-a",
    )
    hub = TaskEventHub(subscriber_capacity=32)
    approvals = DurableApprovalService(repository, hub)
    commits = DurableToolCommitService(repository)
    executor = TaskExecutor(repository, sessions, runtime, approvals, commits, hub)
    return TaskService(repository, executor, approvals, hub), repository, scope


def test_task_executor_rejects_unbounded_concurrency(tmp_path: Path) -> None:
    database = tmp_path / "assistant.db"
    sessions = RuntimeSessionRepository(database)
    sessions.create("principal-a")
    repository = TaskRepository(database)
    hub = TaskEventHub()
    approvals = DurableApprovalService(repository, hub)

    with pytest.raises(ValueError, match="concurrency"):
        TaskExecutor(
            repository,
            sessions,
            _Runtime(),
            approvals,
            DurableToolCommitService(repository),
            hub,
            max_concurrency=33,
        )


@pytest.mark.asyncio
async def test_immediate_definition_creates_one_execution_and_retries_idempotently(
    tmp_path: Path,
) -> None:
    release = asyncio.Event()
    service, _repository, scope = _components(
        tmp_path,
        _Runtime(hold=release),
    )
    await service.start()
    try:
        definition, execution = await service.create_definition(
            scope,
            client_request_id="definition-request-a",
            title="Check weather",
            goal="Check today's weather",
            launch_policy=TaskLaunchPolicy(kind=TaskLaunchKind.IMMEDIATE),
        )

        assert execution is not None
        assert definition.latest_execution_id == execution.execution_id
        listed = await service.list_executions(
            scope.principal_id,
            definition.task_id,
        )
        assert [item.execution_id for item in listed] == [execution.execution_id]

        retried_definition, retried_execution = await service.create_definition(
            scope,
            client_request_id="definition-request-a",
            title="Check weather",
            goal="Check today's weather",
            launch_policy=TaskLaunchPolicy(kind=TaskLaunchKind.IMMEDIATE),
        )

        assert retried_definition.task_id == definition.task_id
        assert retried_execution is not None
        assert retried_execution.execution_id == execution.execution_id
        assert len(await service.list_executions(
            scope.principal_id,
            definition.task_id,
        )) == 1
    finally:
        release.set()
        await service.stop()


@pytest.mark.asyncio
async def test_task_executes_without_connection_owned_run(tmp_path: Path) -> None:
    service, repository, scope = _components(tmp_path, _Runtime())
    await service.start()
    try:
        task = await service.create(
            scope,
            client_request_id="request-a",
            goal="finish report",
        )

        events = [
            event
            async for event in service.events(scope.principal_id, task.task_id)
        ]

        assert [event.event_type for event in events] == [
            "task_created",
            "state_changed",
            "completed",
        ]
        trace = repository.get_trace(scope.principal_id, task.task_id)
        assert trace is not None
        assert [entry.entry_type for entry in trace.entries] == [
            "content",
            "final_output",
        ]
        assert trace.entries[0].content == "working:finish report"
        assert trace.final_output == "approved"
        assert repository.get(scope.principal_id, task.task_id).state is (
            TaskState.COMPLETED
        )
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_unsubscribe_does_not_cancel_task(tmp_path: Path) -> None:
    release = asyncio.Event()
    service, repository, scope = _components(tmp_path, _Runtime(hold=release))
    await service.start()
    try:
        task = await service.create(
            scope,
            client_request_id="request-a",
            goal="finish report",
        )
        stream = service.events(scope.principal_id, task.task_id)
        assert (await anext(stream)).event_type == "task_created"
        await stream.aclose()

        release.set()
        for _ in range(100):
            current = repository.get(scope.principal_id, task.task_id)
            if current.state is TaskState.COMPLETED:
                break
            await asyncio.sleep(0.01)

        assert repository.get(scope.principal_id, task.task_id).state is (
            TaskState.COMPLETED
        )
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_running_task_pauses_at_runtime_safe_boundary(tmp_path: Path) -> None:
    release = asyncio.Event()
    service, repository, scope = _components(tmp_path, _Runtime(hold=release))
    await service.start()
    try:
        task = await service.create(
            scope,
            client_request_id="request-a",
            goal="finish report",
        )
        for _ in range(100):
            if repository.get(scope.principal_id, task.task_id).state is (
                TaskState.RUNNING
            ):
                break
            await asyncio.sleep(0.01)

        requested = await service.pause(
            scope.principal_id,
            task.task_id,
            reason="pause from phone",
        )
        assert requested.state is TaskState.RUNNING
        for _ in range(100):
            current = repository.get(scope.principal_id, task.task_id)
            if current.state is TaskState.PAUSED:
                break
            await asyncio.sleep(0.01)

        paused = repository.get(scope.principal_id, task.task_id)
        assert paused.state is TaskState.PAUSED
        assert paused.phase == "manual_pause"
        assert repository.list_attempts(
            scope.principal_id,
            task.task_id,
        )[0].failure_code == "paused"
    finally:
        release.set()
        await service.stop()


@pytest.mark.asyncio
async def test_approval_resolves_from_task_service_not_stream_connection(
    tmp_path: Path,
) -> None:
    service, repository, scope = _components(
        tmp_path,
        _Runtime(request_confirmation=True),
    )
    await service.start()
    try:
        task = await service.create(
            scope,
            client_request_id="request-a",
            goal="publish report",
        )
        events = []
        async for event in service.events(scope.principal_id, task.task_id):
            events.append(event)
            if event.event_type == "approval_requested":
                approval, changed = await service.resolve_approval(
                    scope.principal_id,
                    event.payload.approval_id,
                    approved=True,
                    resolved_by="another-connection",
                )
                assert changed is True
                assert approval.state.value == "approved"

        assert "approval_resolved" in [event.event_type for event in events]
        trace = repository.get_trace(scope.principal_id, task.task_id)
        assert trace is not None
        assert trace.final_output == "approved"
        assert repository.get(scope.principal_id, task.task_id).state is (
            TaskState.COMPLETED
        )
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_restart_pauses_interrupted_task_until_explicit_resume(
    tmp_path: Path,
) -> None:
    release = asyncio.Event()
    first, first_repository, scope = _components(
        tmp_path,
        _Runtime(hold=release),
    )
    await first.start()
    task = await first.create(
        scope,
        client_request_id="request-a",
        goal="finish report",
    )
    for _ in range(100):
        if first_repository.get(scope.principal_id, task.task_id).state is (
            TaskState.RUNNING
        ):
            break
        await asyncio.sleep(0.01)
    await first.stop()

    second, second_repository, _ = _components(
        tmp_path,
        _Runtime(),
        task_id="task-b",
    )
    await second.start()
    try:
        await asyncio.sleep(0.05)
        paused = second_repository.get(scope.principal_id, task.task_id)
        assert paused.state is TaskState.PAUSED
        assert paused.attempt_count == 1

        resumed = await second.resume(
            scope.principal_id,
            task.task_id,
            reason="user approved retry after restart",
        )
        assert resumed.state is TaskState.QUEUED

        events = [
            event
            async for event in second.events(scope.principal_id, task.task_id)
        ]

        assert second_repository.get(scope.principal_id, task.task_id).state is (
            TaskState.COMPLETED
        )
        assert second_repository.get(scope.principal_id, task.task_id).attempt_count == 2
        assert any(
            event.event_type == "state_changed"
            and event.payload.state is TaskState.PAUSED
            for event in events
        )
    finally:
        await second.stop()


@pytest.mark.asyncio
async def test_unknown_tool_outcome_pauses_instead_of_failing_task(
    tmp_path: Path,
) -> None:
    service, repository, scope = _components(
        tmp_path,
        _Runtime(unknown_outcome=True),
    )
    await service.start()
    try:
        task = await service.create(
            scope,
            client_request_id="request-a",
            goal="publish report",
        )
        for _ in range(100):
            current = repository.get(scope.principal_id, task.task_id)
            if current.state is TaskState.PAUSED:
                break
            await asyncio.sleep(0.01)

        paused = repository.get(scope.principal_id, task.task_id)
        assert paused.state is TaskState.PAUSED
        assert paused.phase == "outcome_unknown"
        assert repository.list_events(
            scope.principal_id,
            task.task_id,
        )[-1].payload.reason.startswith("A tool returned")
        assert repository.list_attempts(
            scope.principal_id,
            task.task_id,
        )[0].state.value == "interrupted"
    finally:
        await service.stop()
@pytest.mark.asyncio
async def test_principal_event_stream_replays_and_tails(tmp_path: Path) -> None:
    release = asyncio.Event()
    service, repository, scope = _components(tmp_path, _Runtime(hold=release))
    await service.start()
    try:
        task = await service.create(
            scope,
            client_request_id="feed-request-a",
            goal="finish report",
        )
        stream = service.principal_events(
            scope.principal_id,
            reconciliation_interval=1.0,
        )
        seen = []
        while len(seen) < 2:
            seen.append(await anext(stream))
        release.set()
        while seen[-1].event.event_type != "completed":
            seen.append(await anext(stream))
        await stream.aclose()

        assert {item.event.task_id for item in seen} == {task.task_id}
        assert [item.feed_event_id for item in seen] == sorted(
            item.feed_event_id for item in seen
        )
        assert seen[-1].event.event_type == "completed"
        assert repository.get(scope.principal_id, task.task_id).state is (
            TaskState.COMPLETED
        )
    finally:
        release.set()
        await service.stop()
