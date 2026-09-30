from __future__ import annotations

import asyncio

import pytest

from knoa_platform.agent_runtime.contracts import RuntimeScope
from knoa_platform.agent_runtime.tool_step import (
    ProposedToolCall,
    ToolArgumentPolicy,
    ToolStep,
    ToolStepContext,
)
from knoa_platform.tools.device_location import DeviceLocationTool
from knoa_platform.tools.base import ToolCapability
from knoa_platform.tools.registry import ToolRegistry


class _LocationHandle:
    def __init__(self, value: dict[str, str]) -> None:
        self._value = value
        self.expired = False

    async def wait(self):
        return self._value

    async def expire(self) -> None:
        self.expired = True


class _LocationInteractions:
    owner_kind = "conversation_turn"

    def __init__(self, value: dict[str, str]) -> None:
        self._value = value
        self.events = []

    async def begin(self, scope, run_id, event):
        del scope, run_id
        self.events.append(event)
        return _LocationHandle(self._value)


async def _execute_location_tool(tmp_path, *, arguments, value, reverse):
    registry = ToolRegistry()
    registry.register(DeviceLocationTool(reverse_geocode=reverse))
    step = ToolStep(registry, ToolArgumentPolicy(tmp_path))
    interactions = _LocationInteractions(value)
    result = await step.execute(
        ToolStepContext(
            scope=RuntimeScope(
                principal_id="principal-a",
                session_handle="session-a",
            ),
            run_id="turn-a",
            client_request_id="request-a",
            capabilities=frozenset({ToolCapability.HOST_READ}),
            cancellation=asyncio.Event(),
            interaction=interactions,
        ),
        ProposedToolCall(
            call_id="location-a",
            name="device_location",
            arguments=arguments,
        ),
    )
    return result, interactions


@pytest.mark.asyncio
async def test_device_location_requests_phone_only_when_tool_is_called(
    tmp_path,
) -> None:
    reverse_calls: list[str] = []
    result, interactions = await _execute_location_tool(
        tmp_path,
        arguments={
            "purpose": "weather",
            "precision": "city",
            "address_required": True,
        },
        value={
            "status": "available",
            "location": "未知位置 (31.2,121.6 ±30m)",
            "precision": "city",
        },
        reverse=lambda text: reverse_calls.append(text) or f"{text} (附近：上海市)",
    )

    assert result.status == "completed"
    assert result.output == {
        "available": True,
        "location": "未知位置 (31.2,121.6 ±30m) (附近：上海市)",
        "precision": "city",
    }
    assert reverse_calls == ["未知位置 (31.2,121.6 ±30m)"]
    assert len(interactions.events) == 1
    event = interactions.events[0]
    assert event.kind == "device_location"
    assert event.display["purpose"] == "weather"
    assert event.display["precision"] == "city"
    assert event.display["address_required"] is True


@pytest.mark.asyncio
async def test_coordinate_request_does_not_reverse_geocode(tmp_path) -> None:
    result, _interactions = await _execute_location_tool(
        tmp_path,
        arguments={
            "purpose": "navigation",
            "precision": "precise",
            "address_required": False,
        },
        value={
            "status": "available",
            "location": "(31.2136,121.6469 ±30m)",
            "precision": "precise",
        },
        reverse=lambda _text: pytest.fail("coordinates must not be reverse-geocoded"),
    )

    assert result.output == {
        "available": True,
        "location": "(31.2136,121.6469 ±30m)",
        "precision": "precise",
    }


@pytest.mark.asyncio
async def test_device_location_rejects_precision_above_the_request(tmp_path) -> None:
    result, _interactions = await _execute_location_tool(
        tmp_path,
        arguments={
            "purpose": "weather",
            "precision": "city",
            "address_required": False,
        },
        value={
            "status": "available",
            "location": "(31.2136,121.6469 ±30m)",
            "precision": "precise",
        },
        reverse=lambda _text: pytest.fail("over-precise data must be rejected"),
    )

    assert result.output == {
        "available": False,
        "reason": "device_location_precision_exceeded",
    }


@pytest.mark.asyncio
async def test_device_location_is_unavailable_outside_interactive_chat(
    tmp_path,
) -> None:
    registry = ToolRegistry()
    registry.register(DeviceLocationTool())
    step = ToolStep(registry, ToolArgumentPolicy(tmp_path))
    result = await step.execute(
        ToolStepContext(
            scope=RuntimeScope(
                principal_id="principal-a",
                session_handle="session-a",
            ),
            run_id="task-a",
            client_request_id="request-a",
            capabilities=frozenset({ToolCapability.HOST_READ}),
            cancellation=asyncio.Event(),
            interaction=None,
        ),
        ProposedToolCall(
            call_id="location-a",
            name="device_location",
            arguments={
                "purpose": "nearby",
                "precision": "block",
                "address_required": True,
            },
        ),
    )

    assert result.status == "completed"
    assert result.output == {
        "available": False,
        "reason": "device_location_unavailable",
    }
