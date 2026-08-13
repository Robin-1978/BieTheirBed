from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from knoa_agent import ContextCheckpointRepository, KnoaAgentRuntime
from knoa_agent_contracts import (
    CreateRuntimeSession,
    McpEndpointGrant,
    RuntimeInterruptCommand,
    RuntimeTurnRequest,
    TextPart,
)
from knoa_platform.agent_runtime.model_step import ProviderChunk
from knoa_platform.agent_runtime.tool_step import ProposedToolCall, ToolStepResult


class Provider:
    def __init__(self, *, wait: bool = False) -> None:
        self.wait = wait
        self.requests = []

    def stream(self, request, cancellation):
        async def iterate():
            self.requests.append(request)
            if self.wait:
                await cancellation.wait()
                return
            yield ProviderChunk(content_delta="hello")
            yield ProviderChunk(finish_reason="stop", terminal=True)

        return iterate()


class Client:
    async def list_tools(self):
        return ()

    async def call_tool(self, call):
        raise AssertionError(call)

    async def read_resource(self, uri):
        raise AssertionError(uri)


class Connector:
    def connect(self, grant):
        del grant

        class Bound:
            async def __aenter__(self):
                return Client()

            async def __aexit__(self, *_args):
                return None

        return Bound()


class WeatherClient(Client):
    async def list_tools(self):
        return (
            {
                "name": "tool_help",
                "description": "Describe an available tool",
                "inputSchema": {
                    "type": "object",
                    "properties": {"tool_name": {"type": "string"}},
                    "required": ["tool_name"],
                },
            },
            {
                "name": "weather",
                "description": "Get weather for a location.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"location": {"type": "string"}},
                    "required": ["location"],
                },
            },
        )


class WeatherConnector:
    def connect(self, grant):
        del grant

        class Bound:
            async def __aenter__(self):
                return WeatherClient()

            async def __aexit__(self, *_args):
                return None

        return Bound()


class DeferredMcpProvider:
    def __init__(self) -> None:
        self.requests = []

    def stream(self, request, cancellation):
        del cancellation

        async def iterate():
            self.requests.append(request)
            if len(self.requests) == 1:
                yield ProviderChunk(
                    tool_calls=(
                        ProposedToolCall(
                            call_id="help-a",
                            name="tool_help",
                            arguments={"tool_name": "mcp__jira__issue_get"},
                        ),
                    ),
                    finish_reason="tool_calls",
                    terminal=True,
                )
                return
            yield ProviderChunk(content_delta="done")
            yield ProviderChunk(finish_reason="stop", terminal=True)

        return iterate()


class DeferredMcpClient(Client):
    async def list_tools(self):
        return (
            {
                "name": "tool_help",
                "description": "Describe an available tool",
                "inputSchema": {
                    "type": "object",
                    "properties": {"tool_name": {"type": "string"}},
                    "required": ["tool_name"],
                },
            },
            {
                "name": "weather",
                "description": "Get weather for a location.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"location": {"type": "string"}},
                    "required": ["location"],
                },
            },
            {
                "name": "mcp__jira__issue_get",
                "description": "Get one Jira issue.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "issue_key": {
                            "type": "string",
                            "pattern": "^[A-Z]+-[0-9]+$",
                        }
                    },
                    "required": ["issue_key"],
                    "additionalProperties": False,
                },
            },
        )

    async def call_tool(self, call):
        assert call.name == "tool_help"
        return ToolStepResult(
            call_id=call.call_id,
            tool_name=call.name,
            status="completed",
            output={
                "found": True,
                "tool": "mcp__jira__issue_get",
                "schema": {
                    "name": "mcp__jira__issue_get",
                    "description": "Get one Jira issue.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"issue_key": {"type": "string"}},
                        "required": ["issue_key"],
                    },
                },
            },
        )


class DeferredMcpConnector:
    def connect(self, grant):
        del grant

        class Bound:
            async def __aenter__(self):
                return DeferredMcpClient()

            async def __aexit__(self, *_args):
                return None

        return Bound()


async def healthy():
    return type("Health", (), {"healthy": True, "detail": "ok"})()


def grant(epoch: int = 1) -> McpEndpointGrant:
    return McpEndpointGrant(
        server_id="knoa-platform-capabilities",
        transport="in_memory",
        endpoint="memory://platform-capabilities",
        authorization="token",
        expires_at=9999999999.0,
        scope_digest="a" * 64,
        binding_epoch=epoch,
    )


@pytest.mark.asyncio
async def test_knoa_runtime_owns_session_checkpoint_and_one_terminal_event(
    tmp_path: Path,
) -> None:
    provider = Provider()
    store = ContextCheckpointRepository(
        tmp_path / "context.db",
        session_id_factory=lambda: "agent-session-a",
    )
    runtime = KnoaAgentRuntime(
        provider,
        store,
        Connector(),
        system_prompt="system",
        health_probe=healthy,
        turn_id_factory=lambda: "runtime-turn-a",
    )
    session = await runtime.create_session(
        CreateRuntimeSession(operation_id="create-a", binding_epoch=1)
    )
    assert await runtime.create_session(
        CreateRuntimeSession(operation_id="create-a", binding_epoch=1)
    ) == session
    turn = await runtime.start_turn(
        RuntimeTurnRequest(
            session=session,
            operation_id="operation-a",
            input=(TextPart(text="hi"),),
            mcp=grant(),
        )
    )

    events = [event async for event in turn.events]

    assert [event.event_type for event in events] == [
        "assistant_delta",
        "usage_reported",
        "turn_finished",
    ]
    assert events[-1].status == "completed"
    assert events[-1].final_output == "hello"
    checkpoint = store.load_checkpoint(session.runtime_session_ref)
    assert checkpoint is not None
    assert checkpoint.payload["messages"][-1] == {
        "role": "assistant",
        "content": "hello",
    }


@pytest.mark.asyncio
async def test_knoa_runtime_statically_injects_weather_for_chinese_query(
    tmp_path: Path,
) -> None:
    provider = Provider()
    runtime = KnoaAgentRuntime(
        provider,
        ContextCheckpointRepository(tmp_path / "context.db"),
        WeatherConnector(),
        system_prompt="system",
        health_probe=healthy,
    )
    session = await runtime.create_session(
        CreateRuntimeSession(operation_id="create-weather", binding_epoch=1)
    )
    turn = await runtime.start_turn(
        RuntimeTurnRequest(
            session=session,
            operation_id="operation-weather",
            input=(TextPart(text="查天气"),),
            mcp=grant(),
        )
    )

    await anext(turn.events)

    assert [tool["name"] for tool in provider.requests[0].tools] == [
        "tool_help",
        "weather",
    ]
    assert "description" not in provider.requests[0].tools[1]


@pytest.mark.asyncio
async def test_tool_help_activates_deferred_mcp_tool_on_next_model_step(
    tmp_path: Path,
) -> None:
    provider = DeferredMcpProvider()
    runtime = KnoaAgentRuntime(
        provider,
        ContextCheckpointRepository(tmp_path / "context.db"),
        DeferredMcpConnector(),
        system_prompt="system",
        health_probe=healthy,
    )
    session = await runtime.create_session(
        CreateRuntimeSession(operation_id="create-jira", binding_epoch=1)
    )
    turn = await runtime.start_turn(
        RuntimeTurnRequest(
            session=session,
            operation_id="operation-jira",
            input=(TextPart(text="分析 Jira 问题"),),
            mcp=grant(),
        )
    )

    events = [event async for event in turn.events]

    assert events[-1].status == "completed"
    assert [tool["name"] for tool in provider.requests[0].tools] == [
        "tool_help",
        "weather",
    ]
    assert [tool["name"] for tool in provider.requests[1].tools] == [
        "mcp__jira__issue_get",
        "tool_help",
        "weather",
    ]
    jira = provider.requests[1].tools[0]
    assert jira["inputSchema"] == {
        "type": "object",
        "properties": {"issue_key": {"type": "string"}},
        "required": ["issue_key"],
    }


@pytest.mark.asyncio
async def test_knoa_runtime_interrupts_active_turn_with_explicit_terminal(
    tmp_path: Path,
) -> None:
    runtime = KnoaAgentRuntime(
        Provider(wait=True),
        ContextCheckpointRepository(tmp_path / "context.db"),
        Connector(),
        system_prompt="system",
        health_probe=healthy,
        turn_id_factory=lambda: "runtime-turn-a",
    )
    session = await runtime.create_session(
        CreateRuntimeSession(operation_id="create-a", binding_epoch=1)
    )
    turn = await runtime.start_turn(
        RuntimeTurnRequest(
            session=session,
            operation_id="operation-a",
            input=(TextPart(text="hi"),),
            mcp=grant(),
        )
    )
    consume = asyncio.create_task(anext(turn.events))
    await asyncio.sleep(0)
    result = await runtime.interrupt_turn(
        RuntimeInterruptCommand(
            session=session,
            runtime_turn_ref=turn.runtime_turn_ref,
            command_id="interrupt-a",
        )
    )
    first = await consume

    assert result.status == "accepted"
    assert first.event_type == "turn_finished"
    assert first.status == "interrupted"
