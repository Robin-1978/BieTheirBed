"""Tests for the bounded sleep tool with activity heartbeats."""
from __future__ import annotations

import asyncio
import pytest

from knoa_platform.agent_runtime.contracts import RuntimeScope
from knoa_platform.agent_runtime.tool_step import (
    ToolStepContext,
    _CURRENT_TOOL_STEP_CONTEXT,
)
from knoa_platform.tools.base import ToolCapability
from knoa_platform.tools.sleep import SleepTool


@pytest.mark.asyncio
async def test_sleep_tool_execution():
    tool = SleepTool(max_seconds=5.0)
    res = await tool.execute(seconds=0.1, reason="waiting for update")
    assert res["status"] == "completed"
    assert res["slept_seconds"] >= 0.05
    assert res["reason"] == "waiting for update"


@pytest.mark.asyncio
async def test_sleep_tool_caps_at_maximum():
    tool = SleepTool(max_seconds=0.2)
    res = await tool.execute(seconds=10.0)
    assert res["status"] == "completed"
    assert res["slept_seconds"] <= 0.5
    assert "note" in res
    assert "capped to maximum allowed 0.2s" in res["note"]


@pytest.mark.asyncio
async def test_sleep_tool_invalid_arguments():
    tool = SleepTool()
    res = await tool.execute(seconds="invalid")
    assert "error" in res
    res_neg = await tool.execute(seconds=-5)
    assert "error" in res_neg


@pytest.mark.asyncio
async def test_sleep_tool_emits_heartbeat():
    tool = SleepTool(max_seconds=5.0)
    heartbeat_event = asyncio.Event()
    cancellation_event = asyncio.Event()

    context = ToolStepContext(
        scope=RuntimeScope(principal_id="test", session_handle="test_session"),
        run_id="run_1",
        client_request_id="req_1",
        capabilities=frozenset({ToolCapability.HOST_READ}),
        cancellation=cancellation_event,
        activity_notifier=heartbeat_event,
    )

    token = _CURRENT_TOOL_STEP_CONTEXT.set(context)
    try:
        assert not heartbeat_event.is_set()
        res = await tool.execute(seconds=1.1)
        assert res["status"] == "completed"
        # Heartbeat should have been signaled
        assert heartbeat_event.is_set()
    finally:
        _CURRENT_TOOL_STEP_CONTEXT.reset(token)


@pytest.mark.asyncio
async def test_sleep_tool_handles_cancellation():
    tool = SleepTool(max_seconds=10.0)
    cancellation_event = asyncio.Event()

    context = ToolStepContext(
        scope=RuntimeScope(principal_id="test", session_handle="test_session"),
        run_id="run_1",
        client_request_id="req_1",
        capabilities=frozenset(),
        cancellation=cancellation_event,
    )

    token = _CURRENT_TOOL_STEP_CONTEXT.set(context)
    try:
        # Pre-set cancellation
        cancellation_event.set()
        res = await tool.execute(seconds=5.0)
        assert res["status"] == "interrupted"
        assert res["slept_seconds"] < 1.0
    finally:
        _CURRENT_TOOL_STEP_CONTEXT.reset(token)
