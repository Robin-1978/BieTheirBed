from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from pc_assistant.agent_runtime.contracts import HealthStatus, RuntimeScope
from pc_assistant.service.core_api import (
    AuthenticateRequest,
    CancelTaskRequest,
    CreateSessionRequest,
    CreateTaskRequest,
    GetTaskRequest,
    ListTasksRequest,
    PauseTaskRequest,
    ResumeTaskRequest,
    SubscribePrincipalTaskEventsRequest,
    SubscribeTaskRequest,
    parse_core_server_message_json,
)
from pc_assistant.service.core_server import CoreServer, StaticTokenAuthenticator
from pc_assistant.tasks import (
    PrincipalTaskEvent,
    TaskCapacityError,
    TaskCancelResult,
    TaskEvent,
    TaskEventPayload,
    TaskNotFoundError,
    TaskPauseResult,
    TaskState,
)


_CLOSE = object()


class FakeWebSocket:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[object] = asyncio.Queue()
        self.sent: list[str] = []
        self.sent_event = asyncio.Event()

    async def recv(self) -> str:
        item = await self.incoming.get()
        if item is _CLOSE:
            raise RuntimeError("closed")
        return str(item)

    async def send(self, message: str) -> None:
        self.sent.append(message)
        self.sent_event.set()

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        item = await self.incoming.get()
        if item is _CLOSE:
            raise StopAsyncIteration
        return str(item)

    async def push(self, request) -> None:
        await self.incoming.put(request.model_dump_json())

    async def close_input(self) -> None:
        await self.incoming.put(_CLOSE)

    async def wait_sent(self, count: int) -> None:
        while len(self.sent) < count:
            self.sent_event.clear()
            await asyncio.wait_for(self.sent_event.wait(), timeout=2.0)

    def messages(self):
        return [parse_core_server_message_json(raw) for raw in self.sent]


class FakeTasks:
    def __init__(self) -> None:
        self.cancelled: list[str] = []
        self.paused: list[str] = []
        self.resumed: list[str] = []
        self.subscription_closed = asyncio.Event()
        self.reject_create = False

    async def create(self, scope, **kwargs):
        del scope, kwargs
        if self.reject_create:
            raise TaskCapacityError("full")
        return SimpleNamespace(task_id="task-a", state=TaskState.QUEUED)

    async def get(self, principal_id: str, task_id: str):
        if principal_id != "principal-a" or task_id != "task-a":
            raise TaskNotFoundError("Task not found")
        return _task_record(task_id)

    async def list(self, principal_id: str, **kwargs):
        del kwargs
        if principal_id != "principal-a":
            return (), ""
        return (_task_record("task-a"),), "next-page"

    async def events(self, principal_id: str, task_id: str, *, after_seq: int = 0):
        del principal_id, after_seq
        try:
            yield TaskEvent(
                task_id=task_id,
                event_seq=1,
                event_type="task_created",
                payload=TaskEventPayload(state=TaskState.QUEUED),
                occurred_at=1.0,
            )
            await asyncio.Event().wait()
        finally:
            self.subscription_closed.set()

    async def principal_events(self, principal_id: str, *, after_id: int = 0):
        del after_id
        try:
            event = TaskEvent(
                task_id="task-a",
                event_seq=1,
                event_type="task_created",
                payload=TaskEventPayload(state=TaskState.QUEUED),
                occurred_at=1.0,
            )
            yield PrincipalTaskEvent(
                feed_event_id=7,
                principal_id=principal_id,
                event=event,
            )
            await asyncio.Event().wait()
        finally:
            self.subscription_closed.set()

    async def cancel(self, principal_id: str, task_id: str, *, reason: str = ""):
        del principal_id, reason
        self.cancelled.append(task_id)
        return TaskCancelResult(accepted=True, state=TaskState.RUNNING)

    async def resolve_approval(self, *args, **kwargs):
        raise AssertionError("not used")

    async def pause(self, principal_id: str, task_id: str, *, reason: str = ""):
        del principal_id, reason
        self.paused.append(task_id)
        return TaskPauseResult(accepted=True, state=TaskState.PAUSED)

    async def resume(
        self,
        principal_id: str,
        task_id: str,
        *,
        reason: str = "",
        acknowledge_outcome_unknown: bool = False,
    ):
        del principal_id, reason, acknowledge_outcome_unknown
        self.resumed.append(task_id)
        return SimpleNamespace(task_id=task_id, state=TaskState.QUEUED)

    async def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True)


class FakeControl:
    async def create_session(self, principal_id: str) -> RuntimeScope:
        return RuntimeScope(principal_id=principal_id, session_handle="session-a")


class FakeArtifacts:
    pass


def _task_record(task_id: str):
    return SimpleNamespace(
        task_id=task_id,
        session_handle="session-a",
        client_request_id="request-a",
        parent_task_id="",
        goal="hello",
        attachments=(),
        tools_enabled=True,
        priority=0,
        state=TaskState.QUEUED,
        phase="",
        attempt_count=0,
        cancel_requested=False,
        final_summary="",
        failure_code="",
        created_at=1.0,
        updated_at=1.0,
        started_at=None,
        finished_at=None,
        next_event_seq=2,
    )


def _server(tasks: FakeTasks) -> CoreServer:
    return CoreServer(
        tasks,
        SimpleNamespace(),
        SimpleNamespace(),
        FakeControl(),
        FakeArtifacts(),
        StaticTokenAuthenticator({"token-a": "principal-a"}),
    )


@pytest.mark.asyncio
async def test_server_authenticates_and_accepts_task(tmp_path) -> None:
    del tmp_path
    tasks = FakeTasks()
    websocket = FakeWebSocket()
    server_task = asyncio.create_task(_server(tasks).handle(websocket))

    await websocket.push(AuthenticateRequest(request_id="auth", credential="token-a"))
    await websocket.push(CreateSessionRequest(request_id="session"))
    await websocket.push(
        CreateTaskRequest(
            request_id="create",
            session_handle="session-a",
            input="hello",
        )
    )
    await websocket.wait_sent(3)
    await websocket.close_input()
    await server_task

    assert [message.message_type for message in websocket.messages()] == [
        "authenticated",
        "session_created",
        "task_accepted",
    ]


@pytest.mark.asyncio
async def test_server_maps_task_capacity_to_resource_exhausted() -> None:
    tasks = FakeTasks()
    tasks.reject_create = True
    websocket = FakeWebSocket()
    server_task = asyncio.create_task(_server(tasks).handle(websocket))

    await websocket.push(AuthenticateRequest(request_id="auth", credential="token-a"))
    await websocket.push(
        CreateTaskRequest(
            request_id="create",
            session_handle="session-a",
            input="hello",
        )
    )
    await websocket.wait_sent(2)
    await websocket.close_input()
    await server_task

    error = websocket.messages()[-1]
    assert error.message_type == "error"
    assert error.code == "resource_exhausted"


@pytest.mark.asyncio
async def test_server_returns_owned_task_detail_and_list() -> None:
    tasks = FakeTasks()
    websocket = FakeWebSocket()
    server_task = asyncio.create_task(_server(tasks).handle(websocket))

    await websocket.push(AuthenticateRequest(request_id="auth", credential="token-a"))
    await websocket.push(GetTaskRequest(request_id="detail", task_id="task-a"))
    await websocket.push(ListTasksRequest(request_id="list", limit=10))
    await websocket.wait_sent(3)
    await websocket.close_input()
    await server_task

    messages = websocket.messages()
    assert [message.message_type for message in messages] == [
        "authenticated",
        "task_snapshot",
        "task_list",
    ]
    assert messages[1].task.task_id == "task-a"
    assert messages[2].next_cursor == "next-page"


@pytest.mark.asyncio
async def test_disconnect_closes_subscription_without_cancelling_task() -> None:
    tasks = FakeTasks()
    websocket = FakeWebSocket()
    server_task = asyncio.create_task(_server(tasks).handle(websocket))

    await websocket.push(AuthenticateRequest(request_id="auth", credential="token-a"))
    await websocket.push(
        SubscribeTaskRequest(
            request_id="subscribe",
            task_id="task-a",
        )
    )
    await websocket.wait_sent(3)
    await websocket.close_input()
    await server_task

    assert tasks.cancelled == []
    assert tasks.subscription_closed.is_set()
    assert [message.message_type for message in websocket.messages()] == [
        "authenticated",
        "task_subscribed",
        "task_event",
    ]


@pytest.mark.asyncio
async def test_server_streams_authenticated_principal_task_feed() -> None:
    tasks = FakeTasks()
    websocket = FakeWebSocket()
    server_task = asyncio.create_task(_server(tasks).handle(websocket))

    await websocket.push(AuthenticateRequest(request_id="auth", credential="token-a"))
    await websocket.push(
        SubscribePrincipalTaskEventsRequest(
            request_id="principal-feed",
            after_id=6,
        )
    )
    await websocket.wait_sent(3)
    await websocket.close_input()
    await server_task

    messages = websocket.messages()
    assert [message.message_type for message in messages] == [
        "authenticated",
        "principal_task_events_subscribed",
        "principal_task_event",
    ]
    assert messages[-1].feed_event.feed_event_id == 7
    assert messages[-1].feed_event.principal_id == "principal-a"


@pytest.mark.asyncio
async def test_cancel_task_is_explicit_command() -> None:
    tasks = FakeTasks()
    websocket = FakeWebSocket()
    server_task = asyncio.create_task(_server(tasks).handle(websocket))

    await websocket.push(AuthenticateRequest(request_id="auth", credential="token-a"))
    await websocket.push(
        CancelTaskRequest(request_id="cancel", task_id="task-a")
    )
    await websocket.wait_sent(2)
    await websocket.close_input()
    await server_task

    assert tasks.cancelled == ["task-a"]
    assert websocket.messages()[-1].message_type == "task_cancel_result"


@pytest.mark.asyncio
async def test_resume_task_is_explicit_command() -> None:
    tasks = FakeTasks()
    websocket = FakeWebSocket()
    server_task = asyncio.create_task(_server(tasks).handle(websocket))

    await websocket.push(AuthenticateRequest(request_id="auth", credential="token-a"))
    await websocket.push(
        ResumeTaskRequest(request_id="resume", task_id="task-a")
    )
    await websocket.wait_sent(2)
    await websocket.close_input()
    await server_task

    assert tasks.resumed == ["task-a"]
    assert websocket.messages()[-1].message_type == "task_resumed"


@pytest.mark.asyncio
async def test_pause_task_is_explicit_command() -> None:
    tasks = FakeTasks()
    websocket = FakeWebSocket()
    server_task = asyncio.create_task(_server(tasks).handle(websocket))

    await websocket.push(AuthenticateRequest(request_id="auth", credential="token-a"))
    await websocket.push(PauseTaskRequest(request_id="pause", task_id="task-a"))
    await websocket.wait_sent(2)
    await websocket.close_input()
    await server_task

    assert tasks.paused == ["task-a"]
    assert websocket.messages()[-1].message_type == "task_pause_result"


def test_server_rejects_non_positive_subscription_limit() -> None:
    with pytest.raises(ValueError, match="subscription"):
        CoreServer(
            FakeTasks(),
            SimpleNamespace(),
            SimpleNamespace(),
            FakeControl(),
            FakeArtifacts(),
            StaticTokenAuthenticator({"token-a": "principal-a"}),
            max_subscriptions_per_connection=0,
        )
