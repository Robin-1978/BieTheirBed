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
    TriggerDispatcher,
    TriggerRepository,
    TriggerService,
)
from pc_assistant.artifacts import ArtifactStore
from pc_assistant.context.memory_db import SQLiteMemoryRepository
from pc_assistant.conversation import ConversationRepository, ConversationService
from pc_assistant.service.core_client import (
    CoreClient,
    CoreConnectionLostError,
)
from pc_assistant.service.core_auth import StaticTokenAuthenticator
from pc_assistant.service.core_server import CoreServer
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
        conversations: ConversationService,
        server_task: asyncio.Task[None],
    ) -> None:
        self.client = client
        self.runtime = runtime
        self.tasks = tasks
        self.repository = repository
        self.conversations = conversations
        self.server_task = server_task

    async def close(self) -> None:
        await self.client.disconnect()
        await self.server_task
        await self.tasks.stop()
        await self.conversations.stop()


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
    executor = TaskExecutor(repository, sessions, runtime, approvals, commits, hub)
    tasks = TaskService(repository, executor, approvals, hub)
    conversation_repository = ConversationRepository(
        database,
        turn_id_factory=lambda: "turn-opaque",
        approval_id_factory=lambda: "chat-approval-opaque",
    )
    conversations = ConversationService(
        sessions,
        conversation_repository,
        runtime,
    )
    schedule_repository = ScheduleRepository(
        database,
        schedule_id_factory=lambda: "schedule-opaque",
    )
    schedule_dispatcher = ScheduleDispatcher(schedule_repository, tasks)
    schedules = ScheduleService(schedule_repository, schedule_dispatcher)
    trigger_repository = TriggerRepository(
        database,
        trigger_id_factory=lambda: "trigger-opaque",
    )
    trigger_dispatcher = TriggerDispatcher(trigger_repository, tasks)
    triggers = TriggerService(trigger_repository, trigger_dispatcher)
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
        triggers,
        control,
        artifacts,
        StaticTokenAuthenticator({"token-a": "local"}),
        conversations=conversations,
    )
    await conversations.start()
    await tasks.start()
    client_socket, server_socket = _socket_pair()
    server_task = asyncio.create_task(server.handle(server_socket))
    client = CoreClient(client_socket, approval_handler=approval_handler)
    await client.start("token-a")
    return Connected(client, runtime, tasks, repository, conversations, server_task)


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
            "completed",
        ]
        assert {event.task_id for event in events} == {"task-opaque"}
        snapshot = await connected.client.get_task("task-opaque")
        listing = await connected.client.list_tasks(limit=10)
        assert snapshot.task_id == "task-opaque"
        assert snapshot.trace is not None
        assert snapshot.trace.final_output == "done"
        assert [task.task_id for task in listing.tasks] == ["task-opaque"]
    finally:
        await connected.close()


@pytest.mark.asyncio
async def test_client_chat_turn_round_trip_is_not_a_task(tmp_path: Path) -> None:
    connected = await _connected(tmp_path)
    try:
        session_handle = await connected.client.create_session()
        before, before_cursor = await connected.client.list_conversation_sessions()
        accepted = await connected.client.create_chat_turn(
            session_handle,
            "hello",
            client_request_id="chat-request-a",
        )
        snapshots = [
            snapshot
            async for snapshot in connected.client.chat_turn_updates(accepted.turn_id)
        ]

        assert snapshots[-1].state.value == "completed"
        assert snapshots[-1].content == "hello"
        assert snapshots[-1].final_output == "done"
        stored = await connected.client.get_chat_turn(accepted.turn_id)
        assert stored.turn_id == snapshots[-1].turn_id
        assert stored.final_output == snapshots[-1].final_output
        assert [entry.kind for entry in stored.timeline] == ["content"]
        assert stored.timeline[0].content == "hello"
        listed, turn_cursor = await connected.client.list_chat_turns(session_handle)
        assert [turn.turn_id for turn in listed] == [accepted.turn_id]
        assert turn_cursor == ""
        conversations, conversation_cursor = await connected.client.list_conversation_sessions()
        assert before == ()
        assert before_cursor == ""
        assert [item.title for item in conversations] == ["hello"]
        assert conversation_cursor == ""
        tasks = await connected.client.list_tasks(limit=10)
        assert tasks.tasks == ()
    finally:
        await connected.close()


@pytest.mark.asyncio
async def test_client_uploads_named_file_artifact(tmp_path: Path) -> None:
    connected = await _connected(tmp_path)
    try:
        session_handle = await connected.client.create_session()
        uploaded = await connected.client.upload_artifact(
            session_handle,
            "data:text/plain;base64,SGVsbG8sIEtub2Eh",
            media_type="text/plain",
            name="notes.txt",
            caption="meeting notes",
        )
        downloaded = await connected.client.download_artifact(
            session_handle,
            uploaded.artifact_id,
        )

        assert uploaded.kind == "file"
        assert uploaded.name == "notes.txt"
        assert downloaded.data_url == "data:text/plain;base64,SGVsbG8sIEtub2Eh"
    finally:
        await connected.close()


@pytest.mark.asyncio
async def test_client_replays_and_tails_principal_task_event_feed(
    tmp_path: Path,
) -> None:
    connected = await _connected(tmp_path)
    stream = connected.client.principal_task_events(after_id=0)
    try:
        first_event = asyncio.create_task(anext(stream))
        session_handle = await connected.client.create_session()
        accepted = await connected.client.create_task(session_handle, "background task")
        events = [await asyncio.wait_for(first_event, timeout=2.0)]
        while events[-1].event.event_type != "completed":
            events.append(await asyncio.wait_for(anext(stream), timeout=2.0))

        assert accepted.task_id == "task-opaque"
        assert [item.event.event_type for item in events] == [
            "task_created",
            "state_changed",
            "completed",
        ]
        assert [item.feed_event_id for item in events] == sorted(
            item.feed_event_id for item in events
        )
        assert {item.principal_id for item in events} == {"local"}
    finally:
        await stream.aclose()
        await connected.close()


@pytest.mark.asyncio
async def test_client_disconnect_only_closes_subscription(tmp_path: Path) -> None:
    connected = await _connected(tmp_path)
    session = await connected.client.create_session()
    connected.runtime.hold = True
    accepted = await connected.client.create_task(session, "long task")
    stream = connected.client.task_events(accepted.task_id)
    assert (await anext(stream)).event_type == "task_created"
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
        paused = await connected.client.pause_schedule(created.schedule_id)
        resumed = await connected.client.resume_schedule(created.schedule_id)

        assert created.schedule_id == "schedule-opaque"
        assert detail == created
        assert listing == (created,)
        assert created.priority == 3
        assert paused.state.value == "paused"
        assert resumed.state.value == "active"
    finally:
        await connected.close()


@pytest.mark.asyncio
async def test_client_can_manage_and_fire_authenticated_trigger(tmp_path: Path) -> None:
    connected = await _connected(tmp_path)
    try:
        session = await connected.client.create_session()
        created = await connected.client.create_trigger(
            session,
            "gitlab merge",
            "review merge request",
            priority=4,
        )
        detail = await connected.client.get_trigger(created.trigger_id)
        listing = await connected.client.list_triggers()
        paused = await connected.client.pause_trigger(created.trigger_id)
        resumed = await connected.client.resume_trigger(created.trigger_id)
        event = await connected.client.fire_trigger(
            created.trigger_id,
            "gitlab-event-1",
            {"project": "knoa"},
        )
        repeated = await connected.client.fire_trigger(
            created.trigger_id,
            "gitlab-event-1",
            {"project": "knoa"},
        )

        assert created.trigger_id == "trigger-opaque"
        assert detail == created
        assert listing == (created,)
        assert paused.state.value == "paused"
        assert resumed.state.value == "active"
        assert event == repeated
        assert event.external_event_id == "gitlab-event-1"
        assert event.state.value == "received"
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
