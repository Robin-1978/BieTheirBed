from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest

from knoa_agent_contracts import (
    AssistantDelta,
    RuntimeHealth,
    ToolCallFinished,
    TurnFinished,
)
from knoa_platform.agent_runtime.session_store import RuntimeSessionRepository
from knoa_platform.agent_runtime.tool_step import ProposedToolCall
from knoa_platform.tasks import (
    DurableApprovalService,
    DurableToolCommitService,
    TaskEventHub,
    TaskExecutor,
    TaskLaunchKind,
    TaskLaunchPolicy,
    TaskLaunchReason,
    TaskPreflightBlockedError,
    TaskPreflightCheck,
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
        self.requests = []
        self.entered = asyncio.Event()

    async def execute_turn(self, request):
        self.requests.append(request)
        self.entered.set()
        base = {
            "runtime_session_ref": "agent-session-a",
            "runtime_turn_ref": request.turn_id,
            "occurred_at": 1.0,
        }
        yield AssistantDelta(
            **base,
            content=f"working:{request.input}",
        )
        if self.hold is not None:
            hold_task = asyncio.create_task(self.hold.wait())
            cancel_task = asyncio.create_task(request.cancellation.wait())
            done, pending = await asyncio.wait(
                {hold_task, cancel_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if cancel_task in done and request.cancellation.is_set():
                yield TurnFinished(
                    **base,
                    status="interrupted",
                    error_code="cancelled",
                )
                return
        approved = True
        if self.request_confirmation:
            assert request.confirmation is not None
            approved = await request.confirmation.confirm(
                request.scope,
                request.turn_id,
                ProposedToolCall(
                    call_id="call-a",
                    name="publish",
                    arguments={"document": "report"},
                ),
                "external_side_effect:high",
            )
        if request.cancellation.is_set():
            yield TurnFinished(
                **base,
                status="interrupted",
                error_code="cancelled",
            )
            return
        if self.unknown_outcome:
            yield TurnFinished(
                **base,
                status="outcome_unknown",
                error_code="tool_outcome_unknown",
            )
            return
        yield TurnFinished(
            **base,
            status="completed",
            final_output="approved" if approved else "denied",
        )

    async def health(self):
        return RuntimeHealth(healthy=True, state="ready")


class _ArtifactRuntime(_Runtime):
    async def execute_turn(self, request):
        base = {
            "runtime_session_ref": "agent-session-a",
            "runtime_turn_ref": request.turn_id,
            "occurred_at": 1.0,
        }
        artifact = {
            "artifact_id": "artifact-a",
            "kind": "file",
            "name": "report.txt",
            "media_type": "text/plain",
            "size": 4,
            "direction": "outbound",
            "ownership": "generated",
            "retention": "temporary",
            "status": "available",
            "visibility": "user",
        }
        yield ToolCallFinished(
            **base,
            tool_call_id="call-a",
            tool_name="attach",
            status="completed",
            output={"success": True, "artifact": artifact},
        )
        yield TurnFinished(
            **base,
            status="completed",
            final_output="attached",
        )


def _components(
    tmp_path: Path,
    runtime: _Runtime,
    *,
    task_id: str | Callable[[], str] = "task-a",
) -> tuple[TaskService, TaskRepository, object]:
    database = tmp_path / "assistant.db"
    sessions = RuntimeSessionRepository(
        database,
        handle_factory=lambda: "session-a",
    )
    scope = sessions.active("principal-a") or sessions.create("principal-a")
    repository = TaskRepository(
        database,
        task_id_factory=task_id if callable(task_id) else lambda: task_id,
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
async def test_immediate_definition_without_auto_launch_waits_for_explicit_execute(
    tmp_path: Path,
) -> None:
    service, _repository, scope = _components(tmp_path, _Runtime())
    await service.start()
    try:
        definition, execution = await service.create_definition(
            scope,
            client_request_id="definition-request-deferred",
            title="Check weather",
            goal="Check today's weather",
            launch_policy=TaskLaunchPolicy(kind=TaskLaunchKind.IMMEDIATE),
            auto_launch=False,
        )

        assert execution is None
        assert await service.list_executions(
            scope.principal_id,
            definition.task_id,
        ) == ()

        started = await service.execute_definition(
            scope.principal_id,
            definition.task_id,
            launch_reason=TaskLaunchReason.CREATED,
        )

        assert started.task_id == definition.task_id
        listed = await service.list_executions(
            scope.principal_id,
            definition.task_id,
        )
        assert [item.execution_id for item in listed] == [started.execution_id]
    finally:
        await service.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_kind", "launch_reason"),
    (
        ("schedule", TaskLaunchReason.SCHEDULED),
        ("event", TaskLaunchReason.EVENT),
    ),
)
async def test_bound_launches_cannot_bypass_core_preflight(
    tmp_path: Path,
    provider_kind: str,
    launch_reason: TaskLaunchReason,
) -> None:
    service, repository, scope = _components(tmp_path, _Runtime())
    definition, execution = await service.create_definition(
        scope,
        client_request_id="definition-preflight-a",
        title="Blocked automation",
        goal="Run only after configuration is repaired",
        auto_launch=False,
    )
    assert execution is None
    await service.bind_launch(
        scope.principal_id,
        definition.task_id,
        provider_kind=provider_kind,
        provider_id=f"{provider_kind}-a",
    )

    async def blocked(_definition):
        return (TaskPreflightCheck(
            check_id="runtime",
            status="blocked",
            detail="Agent Runtime 当前不可用，请检查 Node 状态后重试",
            recommended_action="retry",
        ),)

    service.configure_preflight(blocked)
    with pytest.raises(TaskPreflightBlockedError) as captured:
        await service.execute_bound_launch(
            scope.principal_id,
            provider_kind=provider_kind,
            provider_id=f"{provider_kind}-a",
            client_request_id=f"{provider_kind}:delivery-a",
            launch_reason=launch_reason,
        )

    assert captured.value.result.ready is False
    assert captured.value.result.checks[-1].check_id == "runtime"
    assert await service.list_executions(
        scope.principal_id,
        definition.task_id,
    ) == ()
    assert repository.get_task_definition(
        scope.principal_id,
        definition.task_id,
    ).execution_count == 0


@pytest.mark.asyncio
async def test_completed_definition_accepts_human_follow_up_as_new_execution(
    tmp_path: Path,
) -> None:
    task_ids = iter(("execution-initial", "execution-follow-up"))
    service, _repository, scope = _components(
        tmp_path,
        _Runtime(),
        task_id=lambda: next(task_ids),
    )
    await service.start()
    try:
        definition, first = await service.create_definition(
            scope,
            client_request_id="definition-follow-up",
            title="Analyze incident",
            goal="Analyze the initial evidence",
        )
        assert first is not None
        async for event in service.events(scope.principal_id, first.execution_id):
            if event.event_type in {"completed", "failed", "cancelled"}:
                break

        follow_up = await service.continue_definition(
            scope.principal_id,
            definition.task_id,
            client_request_id="follow-up-a",
            input="Please also inspect the logs around 10:32.",
        )

        assert follow_up.task_id == definition.task_id
        assert follow_up.execution_id != first.execution_id
        assert follow_up.launch_reason is TaskLaunchReason.FOLLOW_UP
        assert follow_up.goal_snapshot == "Please also inspect the logs around 10:32."
        assert follow_up.agent_id_snapshot == definition.agent_id
    finally:
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
async def test_task_tool_artifact_is_streamed_and_saved_in_trace(tmp_path: Path) -> None:
    service, repository, scope = _components(tmp_path, _ArtifactRuntime())
    await service.start()
    try:
        task = await service.create(
            scope,
            client_request_id="request-a",
            goal="attach report",
        )

        events = [
            event
            async for event in service.events(scope.principal_id, task.task_id)
        ]

        artifact_events = [event for event in events if event.event_type == "artifact"]
        assert len(artifact_events) == 1
        assert artifact_events[0].payload.artifact is not None
        assert artifact_events[0].payload.artifact.artifact_id == "artifact-a"
        trace = repository.get_trace(scope.principal_id, task.task_id)
        assert trace is not None
        assert [entry.entry_type for entry in trace.entries] == [
            "tool_result",
            "artifact",
            "final_output",
        ]
        assert trace.entries[1].artifact is not None
        assert trace.entries[1].artifact.artifact_id == "artifact-a"
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
    first_runtime = _Runtime(hold=release)
    first, first_repository, scope = _components(
        tmp_path,
        first_runtime,
    )
    await first.start()
    task = await first.create(
        scope,
        client_request_id="request-a",
        goal="finish report",
    )
    await asyncio.wait_for(first_runtime.entered.wait(), timeout=2)
    assert first_repository.get(scope.principal_id, task.task_id).state is (
        TaskState.RUNNING
    )
    await first.stop()

    second_runtime = _Runtime()
    second, second_repository, _ = _components(
        tmp_path,
        second_runtime,
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
        assert first_runtime.requests[0].operation_id == f"{task.task_id}:attempt:1"
        assert second_runtime.requests[0].operation_id == f"{task.task_id}:attempt:2"
        assert second_runtime.requests[0].turn_id == task.task_id
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


def test_synthesize_task_summary():
    from knoa_agent_contracts import TurnFinished
    from knoa_platform.tasks.models import TaskTraceEntry

    # 1. Non-empty final_output
    assert TaskExecutor._synthesize_task_summary([], "Real output", None) == "Real output"

    # 2. Empty final_output but notify tool was called
    notify_entry = TaskTraceEntry(
        entry_type="tool_call",
        content="",
        tool_call_id="call_1",
        tool_name="notify",
        tool_args={"title": "天气提醒", "message": "上海晴天，记得散步！"},
        occurred_at=100.0,
    )
    assert TaskExecutor._synthesize_task_summary([notify_entry], "", None) == "上海晴天，记得散步！"

    # 3. Empty final_output but reasoning exists
    reason_entry = TaskTraceEntry(
        entry_type="reasoning",
        content="Verified weather is clear.",
        occurred_at=100.0,
    )
    assert TaskExecutor._synthesize_task_summary([reason_entry], "", None) == "Verified weather is clear."

    # 4. Interrupted turn fallback
    term = TurnFinished(
        runtime_session_ref="s1",
        runtime_turn_ref="t1",
        occurred_at=100.0,
        status="interrupted",
        error_code="cancelled",
        final_output="",
    )
    assert TaskExecutor._synthesize_task_summary([], "", term) == "Task interrupted (cancelled)"

    # 5. Default fallback
    assert TaskExecutor._synthesize_task_summary([], "", None) == "Task completed"


def test_notification_tool_policy():
    from knoa_platform.tools.base import ToolEffect, ToolRisk
    from knoa_platform.tools.notification import NotificationTool

    tool = NotificationTool()
    normal_policy = tool.policy_for({"title": "Test", "message": "Normal alert"})
    assert normal_policy.risk is ToolRisk.LOW
    assert normal_policy.effect is ToolEffect.INTERNAL_WRITE
    assert not normal_policy.requires_confirmation

    critical_policy = tool.policy_for({"title": "Alert", "message": "Crit", "urgency": "critical"})
    assert critical_policy.risk is ToolRisk.HIGH
    assert critical_policy.effect is ToolEffect.EXTERNAL_SIDE_EFFECT
    assert critical_policy.requires_confirmation
