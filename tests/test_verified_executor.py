from __future__ import annotations

import pytest

from pc_assistant.desktop_session import DesktopSessionError
from pc_assistant.harness.audit import AuditLogger
from pc_assistant.harness import executor as executor_module
from pc_assistant.harness.executor import PreparedToolCall, VerifiedToolExecutor
from pc_assistant.harness.safety import SafetyChecker
from pc_assistant.harness.verifier import Verifier
from pc_assistant.tools.base import ToolBase
from pc_assistant.tools.registry import ToolRegistry


class _MouseTool(ToolBase):
    name = "mouse"

    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def execute(self, **kwargs):
        self._events.append("execute")
        return "ok"

    def schema(self):
        return {"name": self.name, "parameters": {"type": "object", "properties": {}}}


class _SchemaTool(ToolBase):
    name = "schema_tool"

    async def execute(self, **kwargs):
        return kwargs

    def schema(self):
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


class _FailingTool(_SchemaTool):
    async def execute(self, **kwargs):
        return {"error": "Invalid destination"}


class _CrashingTool(_SchemaTool):
    async def execute(self, **kwargs):
        raise RuntimeError("backend unavailable")


def _executor_for_tool(tmp_path, tool):
    registry = ToolRegistry()
    registry.register(tool)
    verifier = Verifier(SafetyChecker(), registry, AuditLogger(str(tmp_path / "audit")))
    return VerifiedToolExecutor(verifier, registry)


def _executor(tmp_path, events):
    registry = ToolRegistry()
    registry.register(_MouseTool(events))

    async def post_verify(tool_name, arguments):
        events.append("post_verify")
        return "confirmed"

    async def confirm(_tool_name, _arguments):
        return True

    verifier = Verifier(
        SafetyChecker(),
        registry,
        AuditLogger(str(tmp_path / "audit")),
        verify_enabled=True,
        post_verify_callback=post_verify,
        confirm_callback=confirm,
    )
    return VerifiedToolExecutor(verifier, registry)


@pytest.mark.asyncio
async def test_commit_orders_execution_before_postcondition(tmp_path, monkeypatch):
    events: list[str] = []
    executor = _executor(tmp_path, events)
    monkeypatch.setattr(
        executor_module,
        "ensure_desktop_session",
        lambda tool_name: events.append("recover"),
    )
    verdict, prepared = await executor.authorize("mouse", {"action": "click"})

    assert verdict.accepted and prepared is not None
    assert events == []
    assert await executor.commit(prepared) == "ok"
    assert events == ["recover", "execute", "post_verify"]


@pytest.mark.asyncio
async def test_desktop_recovery_failure_prevents_execution_and_postcondition(tmp_path, monkeypatch):
    events: list[str] = []
    executor = _executor(tmp_path, events)

    def fail_recovery(tool_name):
        raise DesktopSessionError("No active graphical session found")

    monkeypatch.setattr(executor_module, "ensure_desktop_session", fail_recovery)
    verdict, prepared = await executor.authorize("mouse", {"action": "click"})

    assert verdict.accepted and prepared is not None
    result = await executor.commit(prepared)

    assert result["error"] == "Tool execution failed: No active graphical session found"
    assert result["exception_type"] == "DesktopSessionError"
    assert result["tool"] == "mouse"
    assert events == []


@pytest.mark.asyncio
async def test_non_desktop_tool_does_not_request_recovery(tmp_path, monkeypatch):
    executor = _executor_for_tool(tmp_path, _SchemaTool())
    monkeypatch.setattr(
        executor_module,
        "ensure_desktop_session",
        lambda tool_name: pytest.fail("non-desktop tool requested recovery"),
    )
    _, prepared = await executor.authorize("schema_tool", {"action": "read"})
    assert prepared is not None

    assert await executor.commit(prepared) == {"action": "read"}


@pytest.mark.asyncio
async def test_prepared_call_is_single_use(tmp_path):
    executor = _executor(tmp_path, [])
    _, prepared = await executor.authorize("mouse", {"action": "click"})
    assert prepared is not None
    await executor.commit(prepared)

    with pytest.raises(RuntimeError, match="already"):
        await executor.commit(prepared)


@pytest.mark.asyncio
async def test_forged_call_cannot_commit(tmp_path):
    executor = _executor(tmp_path, [])
    forged = PreparedToolCall("mouse", {"action": "click"}, object())

    with pytest.raises(PermissionError, match="not authorized"):
        await executor.commit(forged)


@pytest.mark.asyncio
async def test_schema_validation_rejects_missing_and_invalid_arguments(tmp_path):
    registry = ToolRegistry()
    registry.register(_SchemaTool())
    verifier = Verifier(SafetyChecker(), registry, AuditLogger(str(tmp_path / "audit")))

    missing = await verifier.verify("schema_tool", {})
    invalid = await verifier.verify("schema_tool", {"action": "delete"})

    assert missing.rejected and missing.code.value == "invalid_arguments"
    assert invalid.rejected and invalid.code.value == "invalid_arguments"


@pytest.mark.asyncio
async def test_tool_errors_include_allowed_inputs_and_next_step(tmp_path):
    executor = _executor_for_tool(tmp_path, _FailingTool())
    _, prepared = await executor.authorize("schema_tool", {"action": "write"})
    assert prepared is not None

    result = await executor.commit(prepared)

    assert result["error"] == "Invalid destination"
    assert result["tool"] == "schema_tool"
    assert result["allowed_parameters"] == ["action"]
    assert result["allowed_actions"] == ["read", "write"]
    assert "retry once" in result["instruction"]


@pytest.mark.asyncio
async def test_tool_exceptions_become_structured_errors(tmp_path):
    executor = _executor_for_tool(tmp_path, _CrashingTool())
    _, prepared = await executor.authorize("schema_tool", {"action": "read"})
    assert prepared is not None

    result = await executor.commit(prepared)

    assert result["error"] == "Tool execution failed: backend unavailable"
    assert result["exception_type"] == "RuntimeError"
    assert result["tool"] == "schema_tool"
