from __future__ import annotations

from types import SimpleNamespace

import pytest

from knoa_platform.extensions import (
    ExtensionDescriptor,
    ExtensionKind,
    ExtensionState,
    ExtensionStatus,
)
from knoa_platform.service.core_api import (
    DeployMCPPackageRequest,
    MCPPackageDeployedMessage,
)
from knoa_platform.service.core_mcp_commands import MCPPackageCommandHandler


class _Packages:
    def __init__(self) -> None:
        self.calls = []

    async def deploy_local(self, path, server_id, *, route=None):
        self.calls.append((path, server_id, route))
        return "updated", ExtensionStatus(
            ExtensionDescriptor(f"mcp:{server_id}", ExtensionKind.MCP),
            ExtensionState.RUNNING,
            tools=(f"mcp__{server_id}__ping",),
        )


class _Sessions:
    def resolve(self, principal_id, session_handle):
        assert (principal_id, session_handle) == ("personal:owner", "session-a")
        return SimpleNamespace(
            principal_id=principal_id,
            session_handle=session_handle,
        )


@pytest.mark.asyncio
async def test_explicit_owner_mcp_deployment_bypasses_agent_approval() -> None:
    packages = _Packages()
    handler = MCPPackageCommandHandler(
        packages,
        _Sessions(),
        owner_principal_id="personal:owner",
    )
    sent = []

    async def send(message):
        sent.append(message)

    request = DeployMCPPackageRequest(
        request_id="request-a",
        path="/workspace/provider",
        server_id="jira",
        resource_uri="jira://assigned-to-me/events",
        session_handle="session-a",
    )

    handled = await handler.dispatch("personal:owner", request, send)

    assert handled is True
    assert isinstance(sent[0], MCPPackageDeployedMessage)
    assert sent[0].deployment.state == "running"
    _, _, route = packages.calls[0]
    assert route[1].principal_id == "personal:owner"
    assert route[1].session_handle == "session-a"


@pytest.mark.asyncio
async def test_explicit_mcp_deployment_is_owner_only() -> None:
    handler = MCPPackageCommandHandler(
        _Packages(),
        _Sessions(),
        owner_principal_id="personal:owner",
    )
    request = DeployMCPPackageRequest(
        request_id="request-a",
        path="/workspace/provider",
        server_id="jira",
    )

    with pytest.raises(PermissionError):
        await handler.dispatch("remote", request, lambda _message: None)
