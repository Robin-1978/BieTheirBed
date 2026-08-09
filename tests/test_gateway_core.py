from __future__ import annotations

import pytest

from pc_assistant.config import AppConfig
from pc_assistant.gateway.core import GatewayCoreBridge


class _Client:
    def __init__(self, principal_id: str) -> None:
        self.principal_id = principal_id
        self.is_connected = True
        self.disconnected = False
        self.sessions = 0

    async def create_session(self) -> str:
        self.sessions += 1
        return f"{self.principal_id}:{self.sessions}"

    async def disconnect(self) -> None:
        self.is_connected = False
        self.disconnected = True


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
