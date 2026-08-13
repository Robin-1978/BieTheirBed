from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from knoa_platform.agent_runtime.contracts import RuntimeScope
from knoa_platform.agent_runtime.tool_step import (
    ProposedToolCall,
    ToolArgumentPolicy,
    ToolStep,
    ToolStepContext,
)
from knoa_platform.extensions import (
    ExtensionDescriptor,
    ExtensionKind,
    ExtensionState,
    ExtensionStatus,
)
from knoa_platform.tools.base import ToolCapability
from knoa_platform.tools.mcp_deploy import MCPDeployTool
from knoa_platform.tools.registry import ToolRegistry


class _Packages:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []

    async def deploy_local(self, path: str, server_id: str, *, route=None):
        self.calls.append((path, server_id, route))
        return "installed", ExtensionStatus(
            ExtensionDescriptor(f"mcp:{server_id}", ExtensionKind.MCP),
            ExtensionState.RUNNING,
            tools=(f"mcp__{server_id}__ping",),
        )


class _Confirmation:
    async def confirm(self, scope, run_id, call, reason: str) -> bool:
        del scope, run_id
        assert call.name == "mcp_deploy"
        assert reason == "local_write:high"
        return True


def _context(confirmation=None) -> ToolStepContext:
    return ToolStepContext(
        scope=RuntimeScope(principal_id="local", session_handle="session-a"),
        run_id="run-a",
        client_request_id="request-a",
        capabilities=frozenset(
            {
                ToolCapability.HOST_READ,
                ToolCapability.HOST_WRITE,
                ToolCapability.MCP,
            }
        ),
        cancellation=asyncio.Event(),
        confirmation=confirmation,
    )


@pytest.mark.asyncio
async def test_mcp_deploy_requires_confirmation_and_activates_after_approval(
    tmp_path: Path,
) -> None:
    packages = _Packages()
    registry = ToolRegistry()
    registry.register(MCPDeployTool(packages))
    step = ToolStep(registry, ToolArgumentPolicy(tmp_path))
    call = ProposedToolCall(
        call_id="call-a",
        name="mcp_deploy",
        arguments={
            "path": "package",
            "server_id": "monitor",
            "resource_uri": "monitor://events",
        },
    )

    denied = await step.execute(_context(), call)
    completed = await step.execute(_context(_Confirmation()), call)

    assert denied.code == "confirmation_required"
    assert len(packages.calls) == 1
    path, server_id, route = packages.calls[0]
    assert path == str((tmp_path / "package").resolve())
    assert server_id == "monitor"
    assert route[1].session_handle == "session-a"
    assert route[1].uri == "monitor://events"
    assert completed.status == "completed"
    assert completed.output["action"] == "installed"
    assert completed.output["tools"] == ["mcp__monitor__ping"]
