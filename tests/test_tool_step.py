from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from pc_assistant.agent_runtime.contracts import RuntimeScope
from pc_assistant.agent_runtime.tool_step import (
    ProposedToolCall,
    ToolArgumentPolicy,
    ToolStep,
    ToolStepContext,
)
from pc_assistant.tools.base import (
    ToolBase,
    ToolCapability,
    ToolEffect,
    ToolPolicy,
    ToolRisk,
)
from pc_assistant.tools.registry import ToolRegistry


class RecordingTool(ToolBase):
    name = "record"
    effect = ToolEffect.LOCAL_WRITE
    capabilities = frozenset({ToolCapability.WORKSPACE_WRITE})
    risk = ToolRisk.MEDIUM

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return {"ok": True}

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "options": {
                        "type": "object",
                        "properties": {"enabled": {"type": "boolean"}},
                        "required": ["enabled"],
                        "additionalProperties": False,
                    },
                },
                "required": ["path", "options"],
            },
        }


class UnknownPolicyTool(ToolBase):
    name = "unknown"

    async def execute(self, **kwargs: Any) -> Any:
        return kwargs

    def schema(self) -> dict[str, Any]:
        return {"name": self.name, "parameters": {"type": "object"}}


class MixedPolicyTool(ToolBase):
    name = "mixed"
    effect = ToolEffect.LOCAL_WRITE
    capabilities = frozenset({ToolCapability.WORKSPACE_WRITE})
    schema_capabilities = frozenset({ToolCapability.WORKSPACE_READ})
    risk = ToolRisk.MEDIUM

    def __init__(self) -> None:
        self.calls: list[str] = []

    def policy_for(self, arguments: dict[str, Any]) -> ToolPolicy:
        if arguments.get("action") == "read":
            return ToolPolicy(
                ToolEffect.READ_ONLY,
                frozenset({ToolCapability.WORKSPACE_READ}),
                ToolRisk.LOW,
            )
        return self.policy

    async def execute(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs["action"])
        return {"ok": True}

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["read", "write"]},
                },
                "required": ["action"],
            },
        }


class Confirmation:
    def __init__(self, approved: bool) -> None:
        self.approved = approved
        self.calls = 0

    async def confirm(self, scope, run_id, call, reason: str) -> bool:
        assert run_id == "run-a"
        self.calls += 1
        return self.approved


def _context(
    *,
    confirmation=None,
    cancelled: bool = False,
    capabilities=frozenset({ToolCapability.WORKSPACE_WRITE}),
) -> ToolStepContext:
    event = asyncio.Event()
    if cancelled:
        event.set()
    return ToolStepContext(
        scope=RuntimeScope(principal_id="local", session_handle="session-a"),
        run_id="run-a",
        client_request_id="request-a",
        capabilities=capabilities,
        cancellation=event,
        confirmation=confirmation,
    )


def _step(tmp_path: Path, tool: ToolBase):
    registry = ToolRegistry()
    registry.register(tool)
    return ToolStep(registry, ToolArgumentPolicy(tmp_path))


def _step_with_prepare(tmp_path: Path, tool: ToolBase, prepare):
    registry = ToolRegistry()
    registry.register(tool)
    return ToolStep(
        registry,
        ToolArgumentPolicy(tmp_path),
        prepare_execution=prepare,
    )


@pytest.mark.asyncio
async def test_nested_json_schema_is_enforced_before_commit(tmp_path: Path) -> None:
    tool = RecordingTool()
    step = _step(tmp_path, tool)

    result = await step.execute(
        _context(confirmation=Confirmation(True)),
        ProposedToolCall(
            call_id="call-a",
            name="record",
            arguments={"path": "out.txt", "options": {"enabled": "yes"}},
        ),
    )

    assert result.status == "rejected"
    assert result.code == "tool_invalid_arguments"
    assert tool.calls == []


@pytest.mark.asyncio
async def test_workspace_escape_and_missing_capability_fail_closed(tmp_path: Path) -> None:
    tool = RecordingTool()
    step = _step(tmp_path, tool)
    call = ProposedToolCall(
        call_id="call-a",
        name="record",
        arguments={"path": "../escape.txt", "options": {"enabled": True}},
    )

    escaped = await step.execute(_context(confirmation=Confirmation(True)), call)
    denied = await step.execute(_context(capabilities=frozenset()), call)

    assert escaped.code == "capability_denied"
    assert denied.code == "capability_denied"
    assert tool.calls == []


@pytest.mark.asyncio
async def test_confirmation_precedes_single_commit(tmp_path: Path) -> None:
    tool = RecordingTool()
    step = _step(tmp_path, tool)
    confirmation = Confirmation(True)

    result = await step.execute(
        _context(confirmation=confirmation),
        ProposedToolCall(
            call_id="call-a",
            name="record",
            arguments={"path": "out.txt", "options": {"enabled": True}},
        ),
    )

    assert result.status == "completed"
    assert confirmation.calls == 1
    assert tool.calls == [
        {"path": str((tmp_path / "out.txt").resolve()), "options": {"enabled": True}}
    ]


@pytest.mark.asyncio
async def test_unknown_policy_and_cancellation_never_commit(tmp_path: Path) -> None:
    unknown = await _step(tmp_path, UnknownPolicyTool()).execute(
        _context(),
        ProposedToolCall(call_id="call-a", name="unknown"),
    )
    tool = RecordingTool()
    cancelled = await _step(tmp_path, tool).execute(
        _context(cancelled=True, confirmation=Confirmation(True)),
        ProposedToolCall(
            call_id="call-b",
            name="record",
            arguments={"path": "out.txt", "options": {"enabled": True}},
        ),
    )

    assert unknown.code == "capability_denied"
    assert cancelled.status == "not_executed"
    assert cancelled.code == "cancelled"
    assert tool.calls == []


@pytest.mark.asyncio
async def test_execution_environment_is_prepared_after_confirmation(
    tmp_path: Path,
) -> None:
    tool = RecordingTool()
    prepared: list[str] = []
    step = _step_with_prepare(tmp_path, tool, prepared.append)

    result = await step.execute(
        _context(confirmation=Confirmation(True)),
        ProposedToolCall(
            call_id="call-a",
            name="record",
            arguments={"path": "out.txt", "options": {"enabled": True}},
        ),
    )

    assert result.status == "completed"
    assert prepared == ["record"]
    assert len(tool.calls) == 1


@pytest.mark.asyncio
async def test_execution_environment_failure_prevents_commit(tmp_path: Path) -> None:
    tool = RecordingTool()

    def unavailable(_tool_name: str) -> None:
        raise RuntimeError("missing display")

    result = await _step_with_prepare(tmp_path, tool, unavailable).execute(
        _context(confirmation=Confirmation(True)),
        ProposedToolCall(
            call_id="call-a",
            name="record",
            arguments={"path": "out.txt", "options": {"enabled": True}},
        ),
    )

    assert result.status == "failed"
    assert result.code == "execution_environment_unavailable"
    assert tool.calls == []


@pytest.mark.asyncio
async def test_call_specific_read_policy_does_not_request_confirmation(
    tmp_path: Path,
) -> None:
    tool = MixedPolicyTool()
    result = await _step(tmp_path, tool).execute(
        _context(capabilities=frozenset({ToolCapability.WORKSPACE_READ})),
        ProposedToolCall(
            call_id="call-a",
            name="mixed",
            arguments={"action": "read"},
        ),
    )

    assert result.status == "completed"
    assert tool.calls == ["read"]


def test_mixed_tool_schema_is_visible_with_read_capability() -> None:
    registry = ToolRegistry()
    registry.register(MixedPolicyTool())
    capabilities = frozenset({ToolCapability.WORKSPACE_READ})

    assert registry.list_for(capabilities) == ["mixed"]
    assert registry.schemas_for(capabilities)[0]["function"]["name"] == "mixed"


@pytest.mark.asyncio
async def test_call_specific_write_policy_still_requires_confirmation(
    tmp_path: Path,
) -> None:
    tool = MixedPolicyTool()
    result = await _step(tmp_path, tool).execute(
        _context(),
        ProposedToolCall(
            call_id="call-a",
            name="mixed",
            arguments={"action": "write"},
        ),
    )

    assert result.status == "rejected"
    assert result.code == "confirmation_required"
    assert tool.calls == []
