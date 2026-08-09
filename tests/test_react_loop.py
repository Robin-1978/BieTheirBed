from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from pc_assistant.agent_runtime.contracts import RuntimeScope
from pc_assistant.agent_runtime.model_step import ModelStepEvent, ModelStepResult
from pc_assistant.agent_runtime.react_loop import (
    ReActContext,
    ReActLimits,
    ReActLoop,
)
from pc_assistant.agent_runtime.tool_step import ProposedToolCall, ToolStepResult


class SequencedModelStep:
    def __init__(self, results: list[ModelStepResult]) -> None:
        self.results = results
        self.requests = []

    def run(self, request, cancellation):
        async def stream():
            self.requests.append(request)
            result = self.results.pop(0)
            if result.content:
                yield ModelStepEvent(event_type="content_delta", content=result.content)
            yield ModelStepEvent(event_type="terminal", result=result)

        return stream()


class RecordingToolStep:
    def __init__(self) -> None:
        self.calls: list[ProposedToolCall] = []

    async def execute(self, context, call: ProposedToolCall) -> ToolStepResult:
        self.calls.append(call)
        return ToolStepResult(
            call_id=call.call_id,
            tool_name=call.name,
            status="completed",
            output={"value": call.arguments.get("value")},
        )


def _context(cancellation: asyncio.Event | None = None) -> ReActContext:
    return ReActContext(
        scope=RuntimeScope(principal_id="local", session_handle="session-a"),
        client_request_id="request-a",
        messages=({"role": "user", "content": "do it"},),
        tool_definitions=(),
        capabilities=frozenset(),
        cancellation=cancellation or asyncio.Event(),
    )


@pytest.mark.asyncio
async def test_react_loop_pairs_serial_tool_calls_before_next_model_step() -> None:
    model = SequencedModelStep(
        [
            ModelStepResult(
                status="completed",
                tool_calls=(
                    ProposedToolCall(
                        call_id="call-a",
                        name="first",
                        arguments={"value": 1},
                    ),
                    ProposedToolCall(
                        call_id="call-b",
                        name="second",
                        arguments={"value": 2},
                    ),
                ),
                finish_reason="tool_calls",
            ),
            ModelStepResult(
                status="completed",
                content="done",
                finish_reason="stop",
            ),
        ]
    )
    tools = RecordingToolStep()
    loop = ReActLoop(model, tools)

    events = [event async for event in loop.run(_context())]

    assert [call.call_id for call in tools.calls] == ["call-a", "call-b"]
    outcome = events[-1].outcome
    assert outcome is not None and outcome.status == "completed"
    roles = [message["role"] for message in outcome.messages]
    assert roles == ["user", "assistant", "tool", "tool", "assistant"]
    tool_ids = [
        message["tool_call_id"]
        for message in outcome.messages
        if message["role"] == "tool"
    ]
    assert tool_ids == ["call-a", "call-b"]
    assert len(model.requests) == 2


@pytest.mark.asyncio
async def test_react_loop_refreshes_tool_definitions_between_iterations() -> None:
    model = SequencedModelStep(
        [
            ModelStepResult(
                status="completed",
                tool_calls=(ProposedToolCall(call_id="call-a", name="mcp_import"),),
                finish_reason="tool_calls",
            ),
            ModelStepResult(status="completed", content="done", finish_reason="stop"),
        ]
    )
    definitions = iter(
        [
            ({"name": "mcp_import", "inputSchema": {"type": "object"}},),
            ({"name": "mcp__monitor__query", "inputSchema": {"type": "object"}},),
        ]
    )
    context = replace(
        _context(),
        tool_definition_provider=lambda: next(definitions),
    )

    events = [event async for event in ReActLoop(model, RecordingToolStep()).run(context)]

    assert events[-1].outcome is not None
    assert model.requests[0].tools[0]["name"] == "mcp_import"
    assert model.requests[1].tools[0]["name"] == "mcp__monitor__query"


@pytest.mark.asyncio
async def test_react_loop_pairs_skipped_calls_then_fails_at_tool_limit() -> None:
    model = SequencedModelStep(
        [
            ModelStepResult(
                status="completed",
                tool_calls=(
                    ProposedToolCall(call_id="call-a", name="first"),
                    ProposedToolCall(call_id="call-b", name="second"),
                ),
                finish_reason="tool_calls",
            )
        ]
    )
    tools = RecordingToolStep()
    loop = ReActLoop(model, tools, limits=ReActLimits(max_tool_calls=1))

    events = [event async for event in loop.run(_context())]

    outcome = events[-1].outcome
    assert outcome is not None
    assert outcome.status == "failed"
    assert outcome.error_code == "tool_limit_reached"
    assert [call.call_id for call in tools.calls] == ["call-a"]
    tool_messages = [message for message in outcome.messages if message["role"] == "tool"]
    assert len(tool_messages) == 2
    assert "not_executed" in tool_messages[1]["content"]


@pytest.mark.asyncio
async def test_react_loop_cancellation_returns_outcome_without_model_call() -> None:
    cancellation = asyncio.Event()
    cancellation.set()
    model = SequencedModelStep([])
    loop = ReActLoop(model, RecordingToolStep())

    events = [event async for event in loop.run(_context(cancellation))]

    assert events[-1].outcome is not None
    assert events[-1].outcome.status == "cancelled"
    assert model.requests == []


@pytest.mark.asyncio
async def test_react_loop_provider_failure_is_terminal_outcome() -> None:
    model = SequencedModelStep(
        [ModelStepResult(status="failed", error_code="provider_failed")]
    )
    loop = ReActLoop(model, RecordingToolStep())

    events = [event async for event in loop.run(_context())]

    assert events[-1].outcome is not None
    assert events[-1].outcome.status == "failed"
    assert events[-1].outcome.error_code == "provider_failed"


def test_tool_result_message_preserves_screenshot_image_reference() -> None:
    result = ToolStepResult(
        call_id="call-screen",
        tool_name="screenshot",
        status="completed",
        output={
            "success": True,
            "artifact": {
                "artifact_id": "image-a",
                "kind": "image",
                "name": "screen.png",
                "media_type": "image/png",
                "size": 42,
                "direction": "outbound",
                "ownership": "generated",
                "retention": "temporary",
                "status": "available",
                "visibility": "user",
                "temporary": True,
            },
            "image_ref": {
                "type": "image_ref",
                "artifact_id": "image-a",
                "media_type": "image/png",
            },
        },
    )

    message = ReActLoop._tool_result_message(result)

    assert message["role"] == "tool"
    assert isinstance(message["content"], list)
    assert message["content"][0]["type"] == "text"
    assert '"status": "completed"' in message["content"][0]["text"]
    assert message["content"][1] == result.output["image_ref"]
