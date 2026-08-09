from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from pc_assistant.agent_runtime.artifact_service import ArtifactService
from pc_assistant.agent_runtime.contracts import (
    CancelRequest,
    CancelResult,
    ConfigSetRequest,
    ConfigSetResult,
    HealthStatus,
    RunRequest,
    RuntimeEvent,
    RuntimeEventPayload,
    RuntimeRunContext,
    RuntimeScope,
)
from pc_assistant.agent_runtime.control import ControlService
from pc_assistant.agent_runtime.session_store import RuntimeSessionRepository
from pc_assistant.agent_runtime.tool_step import ProposedToolCall
from pc_assistant.automation import (
    ScheduleDispatcher,
    ScheduleKind,
    ScheduleRepository,
    ScheduleService,
    ScheduleSpec,
)
from pc_assistant.artifacts import ArtifactStore
from pc_assistant.context.memory_db import SQLiteMemoryRepository
from pc_assistant.service.core_client import (
    CoreClient,
    CoreConnectionLostError,
)
from pc_assistant.service.core_server import CoreServer, StaticTokenAuthenticator
from pc_assistant.tasks import (
    DurableApprovalService,
    DurableToolCommitService,
    TaskEvent,
    TaskEventHub,
    TaskExecutor,
    TaskRepository,
    TaskService,
    TaskState,
)


_CLOSE = object()


class MemoryWebSocket:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[object] = asyncio.Queue()
        self.peer: MemoryWebSocket | None = None
        self.closed = False

    async def recv(self) -> str:
        item = await self.incoming.get()
        if item is _CLOSE:
            raise CoreConnectionLostError("closed")
        return str(item)

    async def send(self, message: str) -> None:
        if self.closed or self.peer is None:
            raise CoreConnectionLostError("closed")
        await self.peer.incoming.put(message)

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.peer is not None:
            await self.peer.incoming.put(_CLOSE)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        item = await self.incoming.get()
        if item is _CLOSE:
            raise StopAsyncIteration
        return str(item)


def _socket_pair() -> tuple[MemoryWebSocket, MemoryWebSocket]:
    client = MemoryWebSocket()
    server = MemoryWebSocket()
    client.peer = server
    server.peer = client
    return client, server


class FakeRuntime:
    def __init__(self) -> None:
        self.hold = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.confirm = False
        self.confirmed: bool | None = None

    def run(
        self,
        context: RuntimeRunContext,
        request: RunRequest,
    ) -> AsyncIterator[RuntimeEvent]:
        async def stream() -> AsyncIterator[RuntimeEvent]:
            self.started.set()
            yield RuntimeEvent(
                event_type="content_delta",
                payload=RuntimeEventPayload(content=request.input),
            )
            if self.confirm:
                assert context.confirmation is not None
                self.confirmed = await context.confirmation.confirm(
                    context.scope,
                    context.run_id,
                    ProposedToolCall(
                        call_id="call-a",
                        name="publish",
                        arguments={"document": "report"},
                    ),
                    "external_side_effect:high",
                )
            if self.hold:
                release = asyncio.create_task(self.release.wait())
                cancelled = asyncio.create_task(context.cancellation.wait())
                _done, pending = await asyncio.wait(
                    {release, cancelled},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for pending_task in pending:
                    pending_task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
            if context.cancellation.is_set():
                return
            yield RuntimeEvent(
                event_type="final_output",
                payload=RuntimeEventPayload(content="done"),
            )

        return stream()

    async def cancel(self, scope: RuntimeScope, request: CancelRequest) -> CancelResult:
        del scope, request
        return CancelResult(accepted=True, status="cancelling")

    async def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True)


class FakeConfig:
    async def set_config(self, request: ConfigSetRequest) -> ConfigSetResult:
        del request
        return ConfigSetResult(applied=True)


class Connected:
    def __init__(
        self,
        client: CoreClient,
        runtime: FakeRuntime,
        tasks: TaskService,
        repository: TaskRepository,
        server_task: asyncio.Task[None],
    ) -> None:
        self.client = client
        self.runtime = runtime
        self.tasks = tasks
        self.repository = repository
        self.server_task = server_task

    async def close(self) -> None:
        await self.client.disconnect()
        await self.server_task
        await self.tasks.stop()


async def _connected(
    tmp_path: Path,
    *,
    approval_handler=None,
) -> Connected:
    database = tmp_path / "assistant.db"
    sessions = RuntimeSessionRepository(
        database,
        handle_factory=lambda: "session-opaque",
    )
    memory = SQLiteMemoryRepository(database)
    runtime = FakeRuntime()
    repository = TaskRepository(
        database,
        task_id_factory=lambda: "task-opaque",
        approval_id_factory=lambda: "approval-opaque",
    )
    hub = TaskEventHub()
    approvals = DurableApprovalService(repository, hub)
    commits = DurableToolCommitService(repository)
    executor = TaskExecutor(repository, runtime, approvals, commits, hub)
    tasks = TaskService(repository, executor, approvals, hub)
    schedule_repository = ScheduleRepository(
        database,
        schedule_id_factory=lambda: "schedule-opaque",
    )
    schedule_dispatcher = ScheduleDispatcher(schedule_repository, tasks)
    schedules = ScheduleService(schedule_repository, schedule_dispatcher)
    control = ControlService(
        sessions,
        memory,
        tool_names=lambda _scope: ["read_file"],
        config_controller=FakeConfig(),
    )
    artifacts = ArtifactService(
        sessions,
        ArtifactStore(tmp_path / "attachments", db_path=database),
    )
    server = CoreServer(
        tasks,
        schedules,
        control,
        artifacts,
        StaticTokenAuthenticator({"token-a": "local"}),
    )
    await tasks.start()
    client_socket, server_socket = _socket_pair()
    server_task = asyncio.create_task(server.handle(server_socket))
    client = CoreClient(client_socket, approval_handler=approval_handler)
    await client.start("token-a")
    return Connected(client, runtime, tasks, repository, server_task)


@pytest.mark.asyncio
async def test_client_auth_session_and_task_round_trip(tmp_path: Path) -> None:
    connected = await _connected(tmp_path)
    try:
        session_handle = await connected.client.create_session()
        events = [
            event
            async for event in connected.client.execute_task(
                session_handle,
                "hello",
            )
        ]

        assert session_handle == "session-opaque"
        assert [event.event_type for event in events] == [
            "task_created",
            "state_changed",
            "content_delta",
            "final_output",
            "completed",
        ]
        assert {event.task_id for event in events} == {"task-opaque"}
        snapshot = await connected.client.get_task("task-opaque")
        listing = await connected.client.list_tasks(limit=10)
        assert snapshot.task_id == "task-opaque"
        assert [task.task_id for task in listing.tasks] == ["task-opaque"]
    finally:
        await connected.close()


@pytest.mark.asyncio
async def test_client_disconnect_only_closes_subscription(tmp_path: Path) -> None:
    connected = await _connected(tmp_path)
    session = await connected.client.create_session()
    connected.runtime.hold = True
    accepted = await connected.client.create_task(session, "long task")
    stream = connected.client.task_events(accepted.task_id)
    while True:
        event = await anext(stream)
        if event.event_type == "content_delta":
            break
    await connected.client.disconnect()
    await connected.server_task

    connected.runtime.release.set()
    for _ in range(100):
        task = connected.repository.get("local", accepted.task_id)
        if task.state is TaskState.COMPLETED:
            break
        await asyncio.sleep(0.01)

    assert connected.repository.get("local", accepted.task_id).state is (
        TaskState.COMPLETED
    )
    await connected.tasks.stop()


@pytest.mark.asyncio
async def test_client_can_pause_and_resume_running_task(tmp_path: Path) -> None:
    connected = await _connected(tmp_path)
    connected.runtime.hold = True
    try:
        session = await connected.client.create_session()
        accepted = await connected.client.create_task(session, "long task")
        await connected.runtime.started.wait()

        requested = await connected.client.pause_task(
            accepted.task_id,
            reason="pause from phone",
        )
        assert requested.result.state is TaskState.RUNNING
        for _ in range(100):
            task = connected.repository.get("local", accepted.task_id)
            if task.state is TaskState.PAUSED:
                break
            await asyncio.sleep(0.01)

        assert connected.repository.get("local", accepted.task_id).state is (
            TaskState.PAUSED
        )
        resumed = await connected.client.resume_task(accepted.task_id)
        assert resumed.state is TaskState.QUEUED
        connected.runtime.release.set()
        for _ in range(100):
            task = connected.repository.get("local", accepted.task_id)
            if task.state is TaskState.COMPLETED:
                break
            await asyncio.sleep(0.01)
        assert connected.repository.get("local", accepted.task_id).state is (
            TaskState.COMPLETED
        )
    finally:
        connected.runtime.release.set()
        await connected.close()


@pytest.mark.asyncio
async def test_client_can_create_get_and_list_schedule(tmp_path: Path) -> None:
    connected = await _connected(tmp_path)
    try:
        session = await connected.client.create_session()
        created = await connected.client.create_schedule(
            session,
            "prepare tomorrow report",
            ScheduleSpec(
                kind=ScheduleKind.ONE_TIME,
                run_at=4_000_000_000.0,
            ),
            priority=3,
        )
        detail = await connected.client.get_schedule(created.schedule_id)
        listing = await connected.client.list_schedules()

        assert created.schedule_id == "schedule-opaque"
        assert detail == created
        assert listing == (created,)
        assert created.priority == 3
    finally:
        await connected.close()


@pytest.mark.asyncio
async def test_client_approval_handler_resolves_durable_approval(
    tmp_path: Path,
) -> None:
    requests: list[TaskEvent] = []

    async def approve(event: TaskEvent) -> bool:
        requests.append(event)
        return True

    connected = await _connected(tmp_path, approval_handler=approve)
    connected.runtime.confirm = True
    try:
        session = await connected.client.create_session()
        events = [
            event
            async for event in connected.client.execute_task(session, "publish")
        ]

        assert connected.runtime.confirmed is True
        assert len(requests) == 1
        assert requests[0].payload.approval_id == "approval-opaque"
        assert "approval_resolved" in [event.event_type for event in events]
    finally:
        await connected.close()
