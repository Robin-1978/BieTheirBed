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
from pc_assistant.context.memory_db import SQLiteMemoryRepository
from pc_assistant.artifacts import ArtifactStore
from pc_assistant.service.core_api import (
    AuthenticateRequest,
    CancelRunRequest,
    CreateSessionRequest,
    DownloadArtifactRequest,
    GetHistoryRequest,
    StartRunRequest,
    UploadArtifactRequest,
    parse_core_server_message_json,
)
from pc_assistant.service.core_server import CoreServer, StaticTokenAuthenticator


_CLOSE = object()
_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)


class FakeWebSocket:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[object] = asyncio.Queue()
        self.sent: list[str] = []
        self.sent_event = asyncio.Event()
        self.fail_on_message_type = ""

    async def recv(self) -> str:
        item = await self.incoming.get()
        if item is _CLOSE:
            raise RuntimeError("connection closed before authentication")
        return str(item)

    async def send(self, message: str) -> None:
        if json.loads(message).get("message_type") == self.fail_on_message_type:
            raise RuntimeError("send failed")
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


class FakeRuntime:
    def __init__(self) -> None:
        self.block = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancel_requests: list[tuple[RuntimeScope, CancelRequest]] = []

    def run(
        self,
        context: RuntimeRunContext,
        request: RunRequest,
    ) -> AsyncIterator[RuntimeEvent]:
        async def stream() -> AsyncIterator[RuntimeEvent]:
            self.started.set()
            if self.block:
                await self.release.wait()
            yield RuntimeEvent(
                event_type="content_delta",
                payload=RuntimeEventPayload(content=request.input),
            )

        return stream()

    async def cancel(self, scope: RuntimeScope, request: CancelRequest) -> CancelResult:
        self.cancel_requests.append((scope, request))
        self.release.set()
        return CancelResult(accepted=True, status="cancelling")

    async def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True)


class FakeConfig:
    async def set_config(self, request: ConfigSetRequest) -> ConfigSetResult:
        return ConfigSetResult(applied=True)


def _server(
    tmp_path: Path,
    *,
    authentication_timeout_seconds: float = 10.0,
    max_active_runs_per_connection: int = 8,
    global_max_active_runs: int = 32,
):
    handles = iter(("session-a", "session-b"))
    sessions = RuntimeSessionRepository(
        tmp_path / "assistant.db",
        handle_factory=lambda: next(handles),
    )
    memory = SQLiteMemoryRepository(tmp_path / "assistant.db")
    runtime = FakeRuntime()
    runs = CoreRunRegistry(
        run_id_factory=lambda: "run-opaque",
        max_active_runs=global_max_active_runs,
    )
    application = CoreApplication(runtime, sessions, runs)
    control = ControlService(
        sessions,
        memory,
        tool_names=lambda _scope: ["read_file"],
        config_controller=FakeConfig(),
    )
    artifact_store = ArtifactStore(
        tmp_path / "attachments",
        db_path=tmp_path / "assistant.db",
    )
    artifacts = ArtifactService(
        sessions,
        artifact_store,
    )
    server = CoreServer(
        application,
        control,
        artifacts,
        StaticTokenAuthenticator({"token-a": "principal-a"}),
        authentication_timeout_seconds=authentication_timeout_seconds,
        max_active_runs_per_connection=max_active_runs_per_connection,
    )
    return server, runtime, runs, sessions, artifact_store


def test_static_authenticator_rejects_empty_configuration() -> None:
    with pytest.raises(ValueError, match="credential"):
        StaticTokenAuthenticator({"": "principal-a"})
    with pytest.raises(ValueError, match="At least one"):
        StaticTokenAuthenticator({})


def test_server_rejects_invalid_active_run_limit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one"):
        _server(tmp_path, max_active_runs_per_connection=0)


@pytest.mark.asyncio
async def test_unauthenticated_connection_times_out(tmp_path: Path) -> None:
    server, _runtime, _runs, _sessions, _store = _server(
        tmp_path,
        authentication_timeout_seconds=0.01,
    )
    websocket = FakeWebSocket()

    await asyncio.wait_for(server.handle(websocket), timeout=1.0)

    assert websocket.sent == []


@pytest.mark.asyncio
async def test_first_message_must_authenticate(tmp_path: Path) -> None:
    server, _runtime, _runs, _sessions, _store = _server(tmp_path)
    websocket = FakeWebSocket()
    await websocket.push(CreateSessionRequest(request_id="session-request"))

    await server.handle(websocket)

    messages = websocket.messages()
    assert len(messages) == 1
    assert messages[0].message_type == "error"
    assert messages[0].code == "unauthenticated"


@pytest.mark.asyncio
async def test_authenticated_control_request_uses_transport_principal(tmp_path: Path) -> None:
    server, _runtime, _runs, sessions, _store = _server(tmp_path)
    foreign = sessions.create("principal-b")
    websocket = FakeWebSocket()
    await websocket.push(
        AuthenticateRequest(request_id="auth", credential="token-a")
    )
    await websocket.push(CreateSessionRequest(request_id="create"))
    await websocket.push(
        GetHistoryRequest(request_id="foreign", session_handle=foreign.session_handle)
    )
    await websocket.close_input()

    await server.handle(websocket)

    messages = websocket.messages()
    assert [message.message_type for message in messages] == [
        "authenticated",
        "session_created",
        "error",
    ]
    assert messages[1].session_handle == "session-b"
    assert messages[2].code == "session_not_found"


@pytest.mark.asyncio
async def test_artifact_upload_requires_owned_session(tmp_path: Path) -> None:
    server, _runtime, _runs, sessions, _store = _server(tmp_path)
    owned = sessions.create("principal-a")
    foreign = sessions.create("principal-b")
    websocket = FakeWebSocket()
    await websocket.push(
        AuthenticateRequest(request_id="auth", credential="token-a")
    )
    await websocket.push(
        UploadArtifactRequest(
            request_id="owned",
            session_handle=owned.session_handle,
            data_url=_DATA_URL,
        )
    )
    await websocket.push(
        UploadArtifactRequest(
            request_id="foreign",
            session_handle=foreign.session_handle,
            data_url=_DATA_URL,
        )
    )
    await websocket.close_input()

    await server.handle(websocket)

    messages = websocket.messages()
    assert messages[1].message_type == "artifact_uploaded"
    assert messages[1].result.media_type == "image/png"
    assert messages[2].message_type == "error"
    assert messages[2].code == "session_not_found"


@pytest.mark.asyncio
async def test_artifact_download_is_scoped_and_does_not_expose_server_path(
    tmp_path: Path,
) -> None:
    server, _runtime, _runs, sessions, store = _server(tmp_path)
    owned = sessions.create("principal-a")
    foreign = sessions.create("principal-b")
    websocket = FakeWebSocket()
    await websocket.push(
        AuthenticateRequest(request_id="auth", credential="token-a")
    )
    await websocket.push(
        UploadArtifactRequest(
            request_id="upload",
            session_handle=owned.session_handle,
            data_url=_DATA_URL,
        )
    )
    handler = asyncio.create_task(server.handle(websocket))
    await websocket.wait_sent(2)
    artifact_id = websocket.messages()[1].result.artifact_id
    await websocket.push(
        DownloadArtifactRequest(
            request_id="download",
            session_handle=owned.session_handle,
            artifact_id=artifact_id,
        )
    )
    await websocket.push(
        DownloadArtifactRequest(
            request_id="foreign",
            session_handle=foreign.session_handle,
            artifact_id=artifact_id,
        )
    )
    await websocket.close_input()
    await handler

    messages = websocket.messages()
    assert messages[2].message_type == "artifact_downloaded"
    assert messages[2].result.data_url == _DATA_URL
    assert messages[2].result.artifact.status == "delivered"
    assert "path" not in messages[2].model_dump_json()
    assert messages[3].message_type == "error"
    assert messages[3].code == "session_not_found"
    assert store.public_ref(owned.session_handle, artifact_id)["status"] == "delivered"


@pytest.mark.asyncio
async def test_artifact_is_not_marked_delivered_when_transport_send_fails(
    tmp_path: Path,
) -> None:
    server, _runtime, _runs, sessions, store = _server(tmp_path)
    owned = sessions.create("principal-a")
    websocket = FakeWebSocket()
    await websocket.push(
        AuthenticateRequest(request_id="auth", credential="token-a")
    )
    await websocket.push(
        UploadArtifactRequest(
            request_id="upload",
            session_handle=owned.session_handle,
            data_url=_DATA_URL,
        )
    )
    handler = asyncio.create_task(server.handle(websocket))
    await websocket.wait_sent(2)
    artifact_id = websocket.messages()[1].result.artifact_id
    websocket.fail_on_message_type = "artifact_downloaded"
    await websocket.push(
        DownloadArtifactRequest(
            request_id="download",
            session_handle=owned.session_handle,
            artifact_id=artifact_id,
        )
    )
    await websocket.close_input()
    await handler

    assert websocket.messages()[-1].code == "internal_error"
    assert store.public_ref(owned.session_handle, artifact_id)["status"] == "available"


@pytest.mark.asyncio
async def test_run_stream_and_cancel_remain_available_on_same_connection(tmp_path: Path) -> None:
    server, runtime, runs, _sessions, _store = _server(tmp_path)
    runtime.block = True
    websocket = FakeWebSocket()
    await websocket.push(
        AuthenticateRequest(request_id="auth", credential="token-a")
    )
    await websocket.push(CreateSessionRequest(request_id="create"))

    handler = asyncio.create_task(server.handle(websocket))
    await websocket.wait_sent(2)
    session_handle = websocket.messages()[1].session_handle
    await websocket.push(
        StartRunRequest(
            request_id="run-request",
            session_handle=session_handle,
            input="hello",
        )
    )
    await websocket.wait_sent(4)
    await runtime.started.wait()
    await websocket.push(
        CancelRunRequest(request_id="cancel", run_id="run-opaque")
    )
    await websocket.wait_sent(6)
    await websocket.close_input()
    await handler

    messages = websocket.messages()
    message_types = [message.message_type for message in messages]
    assert message_types[:4] == [
        "authenticated",
        "session_created",
        "run_accepted",
        "run_event",
    ]
    assert {message_types[4], message_types[5]} == {"cancel_result", "run_event"}
    run_events = [message for message in messages if message.message_type == "run_event"]
    assert [event.event_type for event in run_events] == ["run_started", "cancelled"]
    assert len(runtime.cancel_requests) == 1
    assert runs.status("principal-a", "run-opaque") is None


@pytest.mark.asyncio
async def test_disconnect_requests_runtime_cancel(tmp_path: Path) -> None:
    server, runtime, runs, _sessions, _store = _server(tmp_path)
    runtime.block = True
    websocket = FakeWebSocket()
    await websocket.push(
        AuthenticateRequest(request_id="auth", credential="token-a")
    )
    await websocket.push(CreateSessionRequest(request_id="create"))
    handler = asyncio.create_task(server.handle(websocket))
    await websocket.wait_sent(2)
    session_handle = websocket.messages()[1].session_handle
    await websocket.push(
        StartRunRequest(
            request_id="run-request",
            session_handle=session_handle,
            input="hello",
        )
    )
    await websocket.wait_sent(4)

    await websocket.close_input()
    await handler

    assert runtime.cancel_requests[0][1].reason == "connection_lost"
    assert runs.status("principal-a", "run-opaque") is None


@pytest.mark.asyncio
async def test_disconnect_cancels_run_task_before_run_id_is_published(
    tmp_path: Path,
) -> None:
    server, runtime, runs, sessions, _store = _server(tmp_path)
    runtime.block = True
    scope = sessions.create("principal-a")
    websocket = FakeWebSocket()
    await websocket.push(
        AuthenticateRequest(request_id="auth", credential="token-a")
    )
    await websocket.push(
        StartRunRequest(
            request_id="run-request",
            session_handle=scope.session_handle,
            input="hello",
        )
    )
    await websocket.close_input()

    await asyncio.wait_for(server.handle(websocket), timeout=1.0)

    assert runs.status("principal-a", "run-opaque") is None


@pytest.mark.asyncio
async def test_connection_run_limit_rejects_overflow_and_reuses_slot(
    tmp_path: Path,
) -> None:
    server, runtime, _runs, sessions, _store = _server(
        tmp_path,
        max_active_runs_per_connection=1,
    )
    runtime.block = True
    scope = sessions.create("principal-a")
    websocket = FakeWebSocket()
    await websocket.push(
        AuthenticateRequest(request_id="auth", credential="token-a")
    )
    handler = asyncio.create_task(server.handle(websocket))
    await websocket.wait_sent(1)

    await websocket.push(
        StartRunRequest(
            request_id="run-one",
            session_handle=scope.session_handle,
            input="one",
        )
    )
    await websocket.wait_sent(3)
    await websocket.push(
        StartRunRequest(
            request_id="run-two",
            session_handle=scope.session_handle,
            input="two",
        )
    )
    await websocket.wait_sent(4)

    overflow = websocket.messages()[3]
    assert overflow.message_type == "error"
    assert overflow.request_id == "run-two"
    assert overflow.code == "resource_exhausted"

    runtime.release.set()
    await websocket.wait_sent(5)
    await asyncio.sleep(0)
    await websocket.push(
        StartRunRequest(
            request_id="run-three",
            session_handle=scope.session_handle,
            input="three",
        )
    )
    await websocket.wait_sent(9)
    await websocket.close_input()
    await handler

    messages = websocket.messages()
    assert any(
        message.message_type == "run_accepted"
        and message.request_id == "run-three"
        for message in messages
    )


@pytest.mark.asyncio
async def test_global_run_limit_returns_resource_exhausted(tmp_path: Path) -> None:
    server, runtime, _runs, sessions, _store = _server(
        tmp_path,
        max_active_runs_per_connection=2,
        global_max_active_runs=1,
    )
    runtime.block = True
    scope = sessions.create("principal-a")
    websocket = FakeWebSocket()
    await websocket.push(
        AuthenticateRequest(request_id="auth", credential="token-a")
    )
    handler = asyncio.create_task(server.handle(websocket))
    await websocket.wait_sent(1)
    for request_id in ("run-one", "run-two"):
        await websocket.push(
            StartRunRequest(
                request_id=request_id,
                session_handle=scope.session_handle,
                input=request_id,
            )
        )
    await websocket.wait_sent(4)

    errors = [
        message
        for message in websocket.messages()
        if message.message_type == "error"
    ]
    assert len(errors) == 1
    assert errors[0].request_id in {"run-one", "run-two"}
    assert errors[0].code == "resource_exhausted"

    runtime.release.set()
    await websocket.close_input()
    await handler
