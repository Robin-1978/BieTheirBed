from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pc_assistant.agent_runtime.contracts import RuntimeScope
from pc_assistant.agent_runtime.tool_step import (
    ProposedToolCall,
    ToolArgumentPolicy,
    ToolStep,
    ToolStepContext,
)
from pc_assistant.extensions import (
    ExtensionDescriptor,
    ExtensionKind,
    ExtensionState,
    ExtensionStatus,
)
from pc_assistant.tools.base import ToolCapability
from pc_assistant.tools.mcp_import import MCPImportTool
from pc_assistant.tools.registry import ToolRegistry


class _Packages:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def import_local(self, path: str, server_id: str) -> ExtensionStatus:
        self.calls.append((path, server_id))
        return ExtensionStatus(
            ExtensionDescriptor(f"mcp:{server_id}", ExtensionKind.MCP),
            ExtensionState.RUNNING,
            tools=(f"mcp__{server_id}__ping",),
        )


class _Confirmation:
    async def confirm(self, scope, run_id, call, reason: str) -> bool:
        del scope, run_id
        assert call.name == "mcp_import"
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
async def test_mcp_import_requires_confirmation_and_activates_after_approval(
    tmp_path: Path,
) -> None:
    packages = _Packages()
    registry = ToolRegistry()
    registry.register(MCPImportTool(packages))
    step = ToolStep(registry, ToolArgumentPolicy(tmp_path))
    call = ProposedToolCall(
        call_id="call-a",
        name="mcp_import",
        arguments={"path": "package", "server_id": "monitor"},
    )

    denied = await step.execute(_context(), call)
    completed = await step.execute(_context(_Confirmation()), call)

    assert denied.code == "confirmation_required"
    assert packages.calls == [(str((tmp_path / "package").resolve()), "monitor")]
    assert completed.status == "completed"
    assert completed.output["tools"] == ["mcp__monitor__ping"]
