from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from knoa_platform.agent_runtime.artifact_service import ArtifactService
from knoa_agent_contracts import RuntimeHealth, TurnFinished
from knoa_platform.agent_runtime.contracts import (
    ConfigSetRequest,
    ConfigSetResult,
)
from knoa_platform.agent_runtime.control import ControlService
from knoa_platform.agent_runtime.session_store import RuntimeSessionRepository
from knoa_platform.artifacts import ArtifactStore
from knoa_platform.context.memory_db import SQLiteMemoryRepository
from knoa_platform.service.core_client import CoreClient
from knoa_platform.service.core_host import (
    CoreServiceHost,
    TcpCoreEndpoint,
)
from knoa_platform.service.core_auth import StaticTokenAuthenticator
from knoa_platform.service.core_server import CoreServer
from knoa_platform.tasks import (
    DurableApprovalService,
    DurableToolCommitService,
    TaskEventHub,
    TaskExecutor,
    TaskRepository,
    TaskService,
)


class EmptyRuntime:
    def execute_turn(self, request):
        async def stream():
            yield TurnFinished(
                runtime_session_ref="agent-session-a",
                runtime_turn_ref=request.turn_id,
                occurred_at=1.0,
                status="completed",
            )

        return stream()

    async def health(self):
        return RuntimeHealth(healthy=True, state="ready")


class FakeConfig:
    async def set_config(self, request: ConfigSetRequest) -> ConfigSetResult:
        return ConfigSetResult(applied=True)


def _servers(tmp_path: Path):
    database = tmp_path / "assistant.db"
    sessions = RuntimeSessionRepository(database)
    control = ControlService(
        sessions,
        SQLiteMemoryRepository(database),
        tool_names=lambda _scope: (),
        config_controller=FakeConfig(),
    )
    artifacts = ArtifactService(
        sessions,
        ArtifactStore(
            tmp_path / "attachments",
            db_path=database,
        ),
    )
    repository = TaskRepository(database)
    events = TaskEventHub()
    approvals = DurableApprovalService(repository, events)
    commits = DurableToolCommitService(repository)
    tasks = TaskService(
        repository,
        TaskExecutor(repository, sessions, EmptyRuntime(), approvals, commits, events),
        approvals,
        events,
    )
    tcp = CoreServer(
        tasks,
        SimpleNamespace(),
        SimpleNamespace(),
        control,
        artifacts,
        StaticTokenAuthenticator({"remote-token": "remote-a"}),
    )
    return tcp, sessions


@pytest.mark.asyncio
async def test_host_serves_authenticated_loopback_principal(
    tmp_path: Path,
) -> None:
    tcp_server, sessions = _servers(tmp_path)
    host = CoreServiceHost(
        tcp=TcpCoreEndpoint(tcp_server, "127.0.0.1", 0),
    )

    async with host:
        client = await CoreClient.connect(
            f"ws://127.0.0.1:{host.bound_tcp_port}",
            "remote-token",
        )
        session = await client.create_session()

        assert sessions.resolve("remote-a", session).session_handle == session
        await client.disconnect()


def test_host_rejects_non_loopback_plaintext_tcp(tmp_path: Path) -> None:
    tcp_server, _sessions = _servers(tmp_path)

    with pytest.raises(ValueError, match="loopback until TLS"):
        CoreServiceHost(
            tcp=TcpCoreEndpoint(tcp_server, "0.0.0.0", 8765),
        )
