from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from pc_assistant.agent_runtime.contracts import (
    CancelRequest,
    CancelResult,
    ConfigSetRequest,
    ConfigSetResult,
    HealthStatus,
    RunRequest,
    RunEvent,
    RuntimeEvent,
    RuntimeEventPayload,
    RuntimeRunContext,
    RuntimeScope,
)
from pc_assistant.agent_runtime.control import ControlService
from pc_assistant.agent_runtime.artifact_service import ArtifactService
from pc_assistant.agent_runtime.core_application import CoreApplication
from pc_assistant.agent_runtime.run_registry import CoreRunRegistry
from pc_assistant.agent_runtime.session_store import RuntimeSessionRepository
from pc_assistant.agent_runtime.tool_step import ProposedToolCall
from pc_assistant.context.memory_db import SQLiteMemoryRepository
from pc_assistant.artifacts import ArtifactStore
from pc_assistant.service.core_api import ArtifactInputRef
from pc_assistant.service.core_client import (
    CoreClient,
    CoreConnectionLostError,
    CoreRequestError,
    CoreRequestTimeoutError,
    CoreRunBufferOverflowError,
)
from pc_assistant.service.core_server import CoreServer, StaticTokenAuthenticator


_CLOSE = object()
_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)


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
        self.block = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancel_requests: list[CancelRequest] = []
        self.confirm_call = False
        self.confirmed: bool | None = None

    def run(
        self,
        context: RuntimeRunContext,
        request: RunRequest,
    ) -> AsyncIterator[RuntimeEvent]:
        async def stream() -> AsyncIterator[RuntimeEvent]:
            self.started.set()
            if self.confirm_call:
                assert context.confirmation is not None
                self.confirmed = await context.confirmation.confirm(
                    context.scope,
                    ProposedToolCall(
                        call_id="call-mouse",
                        name="mouse",
                        arguments={"action": "click", "x": 10, "y": 20},
                    ),
                    "desktop_control:high",
                )
            if self.block:
                await self.release.wait()
            yield RuntimeEvent(
                event_type="content_delta",
                payload=RuntimeEventPayload(content=request.input),
            )

        return stream()

    async def cancel(self, scope: RuntimeScope, request: CancelRequest) -> CancelResult:
        self.cancel_requests.append(request)
        self.release.set()
        return CancelResult(accepted=True, status="cancelling")

    async def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True)


class FakeConfig:
    async def set_config(self, request: ConfigSetRequest) -> ConfigSetResult:
        return ConfigSetResult(applied=True)


async def _connected(tmp_path: Path, confirmation_handler=None):
    sessions = RuntimeSessionRepository(
        tmp_path / "assistant.db",
        handle_factory=lambda: "session-opaque",
    )
    memory = SQLiteMemoryRepository(tmp_path / "assistant.db")
    runtime = FakeRuntime()
    application = CoreApplication(
        runtime,
        sessions,
        CoreRunRegistry(run_id_factory=lambda: "run-opaque"),
    )
    control = ControlService(
        sessions,
        memory,
        tool_names=lambda _scope: ["read_file"],
        config_controller=FakeConfig(),
    )
    artifacts = ArtifactService(
        sessions,
        ArtifactStore(
            tmp_path / "attachments",
            db_path=tmp_path / "assistant.db",
        ),
    )
    server = CoreServer(
        application,
        control,
        artifacts,
        StaticTokenAuthenticator({"token-a": "local"}),
    )
    client_socket, server_socket = _socket_pair()
    server_task = asyncio.create_task(server.handle(server_socket))
    client = CoreClient(
        client_socket,
        confirmation_handler=confirmation_handler,
    )
    await client.start("token-a")
    return client, runtime, server_task


@pytest.mark.asyncio
async def test_client_auth_session_and_run_round_trip(tmp_path: Path) -> None:
    client, _runtime, server_task = await _connected(tmp_path)

    session_handle = await client.create_session()
    events = [event async for event in client.run(session_handle, "hello")]

    assert session_handle == "session-opaque"
    assert [event.event_type for event in events] == [
        "run_started",
        "content_delta",
        "completed",
    ]
    assert sum(event.is_terminal for event in events) == 1
    await client.disconnect()
    await server_task


@pytest.mark.asyncio
async def test_authentication_failure_closes_client_transport(tmp_path: Path) -> None:
    sessions = RuntimeSessionRepository(tmp_path / "assistant.db")
    runtime = FakeRuntime()
    application = CoreApplication(runtime, sessions, CoreRunRegistry())
    control = ControlService(
        sessions,
        SQLiteMemoryRepository(tmp_path / "assistant.db"),
        tool_names=lambda _scope: (),
        config_controller=FakeConfig(),
    )
    artifacts = ArtifactService(
        sessions,
        ArtifactStore(tmp_path / "attachments", db_path=tmp_path / "assistant.db"),
    )
    server = CoreServer(
        application,
        control,
        artifacts,
        StaticTokenAuthenticator({"valid": "principal-a"}),
    )
    client_socket, server_socket = _socket_pair()
    server_task = asyncio.create_task(server.handle(server_socket))
    client = CoreClient(client_socket)

    with pytest.raises(CoreRequestError) as exc_info:
        await client.start("invalid")

    assert exc_info.value.code == "unauthenticated"
    assert not client.is_connected
    assert client_socket.closed
    await server_task


@pytest.mark.asyncio
async def test_scalar_request_timeout_removes_pending_future() -> None:
    client_socket, server_socket = _socket_pair()

    async def authenticate_then_ignore() -> None:
        request = await server_socket.recv()
        request_id = json.loads(request)["request_id"]
        await server_socket.send(
            json.dumps(
                {
                    "message_type": "authenticated",
                    "api_version": "v1",
                    "request_id": request_id,
                }
            )
        )
        await server_socket.recv()

    server_task = asyncio.create_task(authenticate_then_ignore())
    client = CoreClient(client_socket, request_timeout_seconds=0.01)
    await client.start("token-a")

    with pytest.raises(CoreRequestTimeoutError, match="health"):
        await client.health()

    assert client._pending == {}
    assert not client.is_connected
    assert client_socket.closed
    await server_task


@pytest.mark.asyncio
async def test_send_failure_closes_connection_and_removes_pending_future() -> None:
    client_socket, server_socket = _socket_pair()

    async def authenticate() -> None:
        request = json.loads(await server_socket.recv())
        await server_socket.send(
            json.dumps(
                {
                    "message_type": "authenticated",
                    "api_version": "v1",
                    "request_id": request["request_id"],
                }
            )
        )

    server_task = asyncio.create_task(authenticate())
    client = CoreClient(client_socket)
    await client.start("token-a")
    client_socket.peer = None

    with pytest.raises(CoreConnectionLostError, match="send failed"):
        await client.health()

    assert client._pending == {}
    assert not client.is_connected
    assert client_socket.closed
    await server_task


def test_client_rejects_invalid_run_event_buffer_limit() -> None:
    client_socket, _server_socket = _socket_pair()

    with pytest.raises(ValueError, match="at least one"):
        CoreClient(client_socket, max_buffered_run_events=0)

    with pytest.raises(ValueError, match="confirmation limit"):
        CoreClient(client_socket, max_pending_confirmations=0)


@pytest.mark.asyncio
async def test_unsolicited_run_acceptance_closes_connection_without_queue_leak() -> None:
    client_socket, server_socket = _socket_pair()

    async def authenticate_then_push() -> None:
        auth = json.loads(await server_socket.recv())
        await server_socket.send(
            json.dumps(
                {
                    "message_type": "authenticated",
                    "api_version": "v1",
                    "request_id": auth["request_id"],
                }
            )
        )
        await server_socket.send(
            json.dumps(
                {
                    "message_type": "run_accepted",
                    "api_version": "v1",
                    "request_id": "unsolicited",
                    "run_id": "run-unsolicited",
                }
            )
        )

    server_task = asyncio.create_task(authenticate_then_push())
    client = CoreClient(client_socket)
    await client.start("token-a")
    while client.is_connected:
        await asyncio.sleep(0)

    assert client._run_queues == {}
    assert client_socket.closed
    await server_task


@pytest.mark.asyncio
async def test_pending_confirmation_limit_closes_connection() -> None:
    client_socket, server_socket = _socket_pair()
    hold_confirmation = asyncio.Event()

    async def handler(_message):
        await hold_confirmation.wait()
        return False

    async def authenticate_then_push() -> None:
        auth = json.loads(await server_socket.recv())
        await server_socket.send(
            json.dumps(
                {
                    "message_type": "authenticated",
                    "api_version": "v1",
                    "request_id": auth["request_id"],
                }
            )
        )
        for index in (1, 2):
            await server_socket.send(
                json.dumps(
                    {
                        "message_type": "confirmation_requested",
                        "api_version": "v1",
                        "request_id": f"confirmation-{index}",
                        "confirmation_id": f"confirm-{index}",
                        "session_handle": "session-opaque",
                        "tool_name": "mouse",
                        "arguments": {},
                        "reason": "desktop_control:high",
                    }
                )
            )

    server_task = asyncio.create_task(authenticate_then_push())
    client = CoreClient(
        client_socket,
        confirmation_handler=handler,
        max_pending_confirmations=1,
    )
    await client.start("token-a")
    while client.is_connected:
        await asyncio.sleep(0)

    assert client_socket.closed
    assert client._confirmation_tasks == set()
    await server_task


@pytest.mark.asyncio
async def test_run_event_buffer_overflow_closes_connection_without_blocking_reader() -> None:
    client_socket, server_socket = _socket_pair()

    async def send_overflowing_run() -> None:
        auth = json.loads(await server_socket.recv())
        await server_socket.send(
            json.dumps(
                {
                    "message_type": "authenticated",
                    "api_version": "v1",
                    "request_id": auth["request_id"],
                }
            )
        )
        run = json.loads(await server_socket.recv())
        await server_socket.send(
            json.dumps(
                {
                    "message_type": "run_accepted",
                    "api_version": "v1",
                    "request_id": run["request_id"],
                    "run_id": "run-overflow",
                }
            )
        )
        for event_seq, content in enumerate(("one", "two"), start=1):
            await server_socket.send(
                RunEvent(
                    run_id="run-overflow",
                    event_seq=event_seq,
                    event_type="content_delta",
                    payload=RuntimeEventPayload(content=content),
                ).model_dump_json()
            )

    server_task = asyncio.create_task(send_overflowing_run())
    client = CoreClient(client_socket, max_buffered_run_events=1)
    await client.start("token-a")

    with pytest.raises(CoreRunBufferOverflowError, match="buffer overflow"):
        async for _event in client.run("session-opaque", "hello"):
            await asyncio.sleep(0.1)

    assert not client.is_connected
    assert client_socket.closed
    await server_task


@pytest.mark.asyncio
async def test_client_control_and_artifact_round_trip(tmp_path: Path) -> None:
    client, _runtime, server_task = await _connected(tmp_path)
    session_handle = await client.create_session()

    health = await client.health()
    status = await client.status(session_handle)
    history = await client.history(session_handle)
    memories = await client.list_memory(session_handle)
    cleared = await client.clear_memory(session_handle)
    tools = await client.list_tools(session_handle)
    configured = await client.set_config(session_handle, "max_iterations", 12)
    artifact = await client.upload_artifact(
        session_handle,
        _DATA_URL,
        caption="sample",
    )
    downloaded = await client.download_artifact(
        session_handle,
        artifact.artifact_id,
    )
    events = [
        event
        async for event in client.run(
            session_handle,
            attachments=(ArtifactInputRef(artifact_id=artifact.artifact_id),),
        )
    ]

    assert health.healthy
    assert status.details["session_handle"] == session_handle
    assert history.messages == ()
    assert memories.memories == ()
    assert cleared.cleared
    assert tools.tools == ("read_file",)
    assert configured.applied
    assert artifact.media_type == "image/png"
    assert downloaded.data_url == _DATA_URL
    assert downloaded.artifact.status == "delivered"
    assert events[-1].event_type == "completed"
    await client.disconnect()
    await server_task


@pytest.mark.asyncio
async def test_client_tracks_active_run_for_scoped_cancel(tmp_path: Path) -> None:
    client, runtime, server_task = await _connected(tmp_path)
    runtime.block = True
    session_handle = await client.create_session()
    events = []

    async def consume() -> None:
        async for event in client.run(session_handle, "hello"):
            events.append(event)

    task = asyncio.create_task(consume())
    await runtime.started.wait()
    while not events:
        await asyncio.sleep(0)

    result = await client.cancel_active()
    await task

    assert result is not None and result.result.status == "cancelling"
    assert [event.event_type for event in events] == ["run_started", "cancelled"]
    assert len(runtime.cancel_requests) == 1
    await client.disconnect()
    await server_task


@pytest.mark.asyncio
async def test_confirmation_round_trip_returns_to_initiating_client(
    tmp_path: Path,
) -> None:
    requests = []

    async def approve(message):
        requests.append(message)
        return True

    client, runtime, server_task = await _connected(
        tmp_path,
        confirmation_handler=approve,
    )
    runtime.confirm_call = True
    session_handle = await client.create_session()

    events = [event async for event in client.run(session_handle, "click")]

    assert events[-1].event_type == "completed"
    assert runtime.confirmed is True
    assert len(requests) == 1
    assert requests[0].tool_name == "mouse"
    assert requests[0].arguments["x"] == 10
    await client.disconnect()
    await server_task


@pytest.mark.asyncio
async def test_missing_confirmation_handler_fails_closed(tmp_path: Path) -> None:
    client, runtime, server_task = await _connected(tmp_path)
    runtime.confirm_call = True
    session_handle = await client.create_session()

    events = [event async for event in client.run(session_handle, "click")]

    assert events[-1].event_type == "completed"
    assert runtime.confirmed is False
    await client.disconnect()
    await server_task


@pytest.mark.asyncio
async def test_disconnect_releases_stream_waiter_and_cancels_runtime(tmp_path: Path) -> None:
    client, runtime, server_task = await _connected(tmp_path)
    runtime.block = True
    session_handle = await client.create_session()

    events = []

    async def consume() -> None:
        async for event in client.run(session_handle, "hello"):
            events.append(event)

    task = asyncio.create_task(consume())
    await runtime.started.wait()
    while not events:
        await asyncio.sleep(0)

    await client.disconnect()

    with pytest.raises(CoreConnectionLostError):
        await task
    await server_task
    assert runtime.cancel_requests[0].reason == "connection_lost"
