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
from pc_assistant.artifacts import ArtifactStore
from pc_assistant.context.scope import MemoryScope, reset_memory_scope, set_memory_scope
from pc_assistant.tools.artifact_prepare import ArtifactPrepareTool
from pc_assistant.tools.base import ToolCapability
from pc_assistant.tools.read_file import ReadFileTool
from pc_assistant.tools.registry import ToolRegistry
from pc_assistant.tools.write_file import WriteFileTool


class _Confirmation:
    def __init__(self, approved: bool = True) -> None:
        self.approved = approved
        self.calls: list[ProposedToolCall] = []

    async def confirm(self, scope, run_id, call, reason: str) -> bool:
        del scope, run_id, reason
        self.calls.append(call)
        return self.approved


def _context(
    capabilities: frozenset[ToolCapability],
    confirmation: _Confirmation | None = None,
) -> ToolStepContext:
    return ToolStepContext(
        scope=RuntimeScope(principal_id="local", session_handle="session-a"),
        run_id="run-a",
        client_request_id="request-a",
        capabilities=capabilities,
        cancellation=asyncio.Event(),
        confirmation=confirmation,
    )


def _step(workspace: Path, tool) -> ToolStep:
    registry = ToolRegistry()
    registry.register(tool)
    return ToolStep(registry, ToolArgumentPolicy(workspace))


@pytest.mark.asyncio
async def test_attach_borrows_file_outside_workspace_after_confirmation(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    desktop = tmp_path / "Desktop"
    source = desktop / "report.txt"
    workspace.mkdir()
    desktop.mkdir()
    source.write_text("keep me", encoding="utf-8")
    artifact_root = tmp_path / "artifacts"
    store = ArtifactStore(
        artifact_root,
        db_path=tmp_path / "data" / "assistant.db",
    )
    confirmation = _Confirmation()
    token = set_memory_scope(MemoryScope(principal_id="local", session_id="session-a"))
    try:
        result = await _step(
            workspace,
            ArtifactPrepareTool(store, working_directory=workspace),
        ).execute(
            _context(frozenset({ToolCapability.HOST_READ}), confirmation),
            ProposedToolCall(
                call_id="call-a",
                name="attach",
                arguments={"path": str(source)},
            ),
        )
    finally:
        reset_memory_scope(token)

    assert result.status == "completed"
    assert len(confirmation.calls) == 1
    assert result.output["artifact"]["ownership"] == "borrowed"
    assert "path" not in result.output["artifact"]
    assert source.read_text(encoding="utf-8") == "keep me"
    assert not artifact_root.exists()

    store.cleanup_expired()
    assert source.read_text(encoding="utf-8") == "keep me"


@pytest.mark.asyncio
async def test_attach_requires_host_read_and_confirmation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "Desktop" / "report.txt"
    workspace.mkdir()
    source.parent.mkdir()
    source.write_text("private", encoding="utf-8")
    store = ArtifactStore(
        tmp_path / "artifacts",
        db_path=tmp_path / "data" / "assistant.db",
    )
    step = _step(workspace, ArtifactPrepareTool(store, working_directory=workspace))
    call = ProposedToolCall(
        call_id="call-a",
        name="attach",
        arguments={"path": str(source)},
    )

    wrong_capability = await step.execute(
        _context(frozenset({ToolCapability.HOST_WRITE}), _Confirmation()),
        call,
    )
    missing_confirmation = await step.execute(
        _context(frozenset({ToolCapability.HOST_READ})),
        call,
    )

    assert wrong_capability.code == "capability_denied"
    assert missing_confirmation.code == "confirmation_required"


@pytest.mark.asyncio
async def test_read_file_can_read_paths_outside_default_directory(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "Desktop" / "report.txt"
    workspace.mkdir()
    source.parent.mkdir()
    source.write_text("private", encoding="utf-8")

    result = await _step(workspace, ReadFileTool(working_directory=workspace)).execute(
        _context(frozenset({ToolCapability.HOST_READ})),
        ProposedToolCall(
            call_id="call-a",
            name="read_file",
            arguments={"path": str(source)},
        ),
    )

    assert result.status == "completed"
    assert result.output["content"] == "private"


@pytest.mark.asyncio
async def test_write_file_can_write_outside_default_directory_after_confirmation(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    destination = tmp_path / "Desktop" / "report.txt"
    workspace.mkdir()
    confirmation = _Confirmation()

    result = await _step(workspace, WriteFileTool(working_directory=workspace)).execute(
        _context(frozenset({ToolCapability.HOST_WRITE}), confirmation),
        ProposedToolCall(
            call_id="call-a",
            name="write_file",
            arguments={"path": str(destination), "content": "created"},
        ),
    )

    assert result.status == "completed"
    assert len(confirmation.calls) == 1
    assert confirmation.calls[0].arguments["path"] == str(destination.resolve())
    assert destination.read_text(encoding="utf-8") == "created"
