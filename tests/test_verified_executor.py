from __future__ import annotations

import pytest

from pc_assistant.harness.audit import AuditLogger
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


def _executor(tmp_path, events):
    registry = ToolRegistry()
    registry.register(_MouseTool(events))

    async def post_verify(tool_name, arguments):
        events.append("post_verify")
        return "confirmed"

    verifier = Verifier(
        SafetyChecker(),
        registry,
        AuditLogger(str(tmp_path / "audit")),
        verify_enabled=True,
        post_verify_callback=post_verify,
    )
    return VerifiedToolExecutor(verifier, registry)


@pytest.mark.asyncio
async def test_commit_orders_execution_before_postcondition(tmp_path):
    events: list[str] = []
    executor = _executor(tmp_path, events)
    verdict, prepared = await executor.authorize("mouse", {"action": "click"})

    assert verdict.accepted and prepared is not None
    assert events == []
    assert await executor.commit(prepared) == "ok"
    assert events == ["execute", "post_verify"]


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
