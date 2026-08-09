from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

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
from pc_assistant.agent_runtime.session_store import RuntimeSessionRepository
from pc_assistant.artifacts import ArtifactStore
from pc_assistant.context.memory_db import SQLiteMemoryRepository
from pc_assistant.service.core_client import CoreClient
from pc_assistant.service.core_host import (
    CoreServiceHost,
    TcpCoreEndpoint,
)
from pc_assistant.service.core_server import (
    CoreServer,
    StaticTokenAuthenticator,
)
from pc_assistant.tasks import (
    DurableApprovalService,
    DurableToolCommitService,
    TaskEventHub,
    TaskExecutor,
    TaskRepository,
    TaskService,
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
        TaskExecutor(repository, EmptyRuntime(), approvals, commits, events),
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
