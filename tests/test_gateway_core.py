from __future__ import annotations

import pytest

from pc_assistant.config import AppConfig
from pc_assistant.gateway.core import GatewayCoreBridge
from pc_assistant.service.core_api import ArtifactInputRef, TaskSnapshot
from pc_assistant.tasks import TaskOrigin, TaskState


class _Client:
    def __init__(self, principal_id: str) -> None:
        self.principal_id = principal_id
        self.is_connected = True
        self.disconnected = False
        self.sessions = 0

    async def create_session(self, **_kwargs) -> str:
        self.sessions += 1
        return f"{self.principal_id}:{self.sessions}"

    async def disconnect(self) -> None:
        self.is_connected = False
        self.disconnected = True

    async def get_task(self, task_id):
        return TaskSnapshot(
            task_id=task_id,
            session_handle="session-a",
            client_request_id="request-a",
            goal="original goal",
            attachments=(ArtifactInputRef(artifact_id="artifact-a"),),
            tools_enabled=True,
            priority=3,
            state=TaskState.FAILED,
            phase="failed",
            attempt_count=1,
            cancel_requested=False,
            created_at=1.0,
            updated_at=2.0,
            next_event_seq=3,
        )

    async def create_task(self, session_handle, goal, attachments, **kwargs):
        self.created_task = (session_handle, goal, attachments, kwargs)
        return type(
            "Accepted",
            (),
            {"task_id": "task-retry", "state": TaskState.QUEUED},
        )()


@pytest.mark.asyncio
async def test_gateway_core_bridge_reuses_only_same_principal_client(tmp_path) -> None:
    clients = []

    async def factory(principal_id):
        client = _Client(principal_id)
        clients.append(client)
        return client

    bridge = GatewayCoreBridge(
        AppConfig(fallback_enabled=False, runtime_root=str(tmp_path)),
        client_factory=factory,
    )

    first = await bridge.create_session("personal:a")
    second = await bridge.create_session("personal:a")
    other = await bridge.create_session("personal:b")
    await bridge.close()

    assert first == "personal:a:1"
    assert second == "personal:a:2"
    assert other == "personal:b:1"
    assert [client.principal_id for client in clients] == ["personal:a", "personal:b"]
    assert all(client.disconnected for client in clients)


@pytest.mark.asyncio
async def test_gateway_core_bridge_retries_as_child_task(tmp_path) -> None:
    client = _Client("personal:owner")

    async def factory(_principal_id):
        return client

    bridge = GatewayCoreBridge(
        AppConfig(fallback_enabled=False, runtime_root=str(tmp_path)),
        client_factory=factory,
    )

    accepted = await bridge.retry_task(
        "personal:owner",
        "task-failed",
        reason="network recovered",
    )

    assert accepted.task_id == "task-retry"
    session_handle, goal, attachments, options = client.created_task
    assert session_handle == "personal:owner:1"
    assert goal == "original goal\n\nRetry note: network recovered"
    assert attachments[0].artifact_id == "artifact-a"
    assert options == {
        "tools_enabled": True,
        "priority": 3,
        "parent_task_id": "task-failed",
        "origin": TaskOrigin.USER,
    }
