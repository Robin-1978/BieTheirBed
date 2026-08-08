from __future__ import annotations

import stat
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
    RuntimeRunContext,
    RuntimeScope,
)
from pc_assistant.agent_runtime.control import ControlService
from pc_assistant.agent_runtime.core_application import CoreApplication
from pc_assistant.agent_runtime.run_registry import CoreRunRegistry
from pc_assistant.agent_runtime.session_store import RuntimeSessionRepository
from pc_assistant.artifacts import ArtifactStore
from pc_assistant.context.memory_db import SQLiteMemoryRepository
from pc_assistant.service.core_client import CoreClient
from pc_assistant.service.core_host import (
    CoreServiceHost,
    TcpCoreEndpoint,
    UnixCoreEndpoint,
)
from pc_assistant.service.core_server import (
    CoreServer,
    StaticTokenAuthenticator,
    UnixLocalAuthenticator,
)


class EmptyRuntime:
    def run(
        self,
        context: RuntimeRunContext,
        request: RunRequest,
    ) -> AsyncIterator[RuntimeEvent]:
        async def stream() -> AsyncIterator[RuntimeEvent]:
            if False:
                yield RuntimeEvent(event_type="warning")

        return stream()

    async def cancel(self, scope: RuntimeScope, request: CancelRequest) -> CancelResult:
        return CancelResult(accepted=False, status="not_found")

    async def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True)


class FakeConfig:
    async def set_config(self, request: ConfigSetRequest) -> ConfigSetResult:
        return ConfigSetResult(applied=True)


def _servers(tmp_path: Path):
    sessions = RuntimeSessionRepository(tmp_path / "assistant.db")
    application = CoreApplication(EmptyRuntime(), sessions, CoreRunRegistry())
    control = ControlService(
        sessions,
        SQLiteMemoryRepository(tmp_path / "assistant.db"),
        tool_names=lambda _scope: (),
        config_controller=FakeConfig(),
    )
    artifacts = ArtifactService(
        sessions,
        ArtifactStore(
            tmp_path / "attachments",
            db_path=tmp_path / "assistant.db",
        ),
    )
    unix = CoreServer(
        application,
        control,
        artifacts,
        UnixLocalAuthenticator(),
    )
    tcp = CoreServer(
        application,
        control,
        artifacts,
        StaticTokenAuthenticator({"remote-token": "remote-a"}),
    )
    return unix, tcp, sessions


@pytest.mark.asyncio
async def test_host_serves_local_and_remote_principals_without_fallback(
    tmp_path: Path,
) -> None:
    unix_server, tcp_server, sessions = _servers(tmp_path)
    socket_path = tmp_path / "core.sock"
    host = CoreServiceHost(
        unix=UnixCoreEndpoint(unix_server, socket_path),
        tcp=TcpCoreEndpoint(tcp_server, "127.0.0.1", 0),
    )

    async with host:
        assert stat.S_IMODE(socket_path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600
        local_client = await CoreClient.connect_unix(str(socket_path))
        remote_client = await CoreClient.connect(
            f"ws://127.0.0.1:{host.bound_tcp_port}",
            "remote-token",
        )
        local_session = await local_client.create_session()
        remote_session = await remote_client.create_session()

        assert sessions.resolve("local", local_session).session_handle == local_session
        assert sessions.resolve("remote-a", remote_session).session_handle == remote_session
        await local_client.disconnect()
        await remote_client.disconnect()

    assert not socket_path.exists()


@pytest.mark.asyncio
async def test_host_refuses_to_replace_existing_socket_path(tmp_path: Path) -> None:
    unix_server, _tcp_server, _sessions = _servers(tmp_path)
    socket_path = tmp_path / "core.sock"
    socket_path.write_text("owned by something else")
    host = CoreServiceHost(unix=UnixCoreEndpoint(unix_server, socket_path))

    with pytest.raises(RuntimeError, match="existing path"):
        await host.start()

    assert socket_path.read_text() == "owned by something else"


@pytest.mark.asyncio
async def test_host_requires_socket_directory_owned_by_service_user(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unix_server, _tcp_server, _sessions = _servers(tmp_path)
    socket_path = tmp_path / "core.sock"
    monkeypatch.setattr(
        "pc_assistant.service.core_host.os.geteuid",
        lambda: tmp_path.stat().st_uid + 1,
    )
    host = CoreServiceHost(unix=UnixCoreEndpoint(unix_server, socket_path))

    with pytest.raises(RuntimeError, match="owned by the service user"):
        await host.start()

    assert not socket_path.exists()


def test_host_rejects_non_loopback_plaintext_tcp(tmp_path: Path) -> None:
    _unix_server, tcp_server, _sessions = _servers(tmp_path)

    with pytest.raises(ValueError, match="loopback until TLS"):
        CoreServiceHost(
            tcp=TcpCoreEndpoint(tcp_server, "0.0.0.0", 8765),
        )
