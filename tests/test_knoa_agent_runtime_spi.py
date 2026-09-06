from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from knoa_agent import ContextCheckpointRepository, KnoaAgentRuntime
from knoa_agent_contracts import (
    ArtifactPart,
    ArtifactReference,
    CreateRuntimeSession,
    McpEndpointGrant,
    RuntimeInterruptCommand,
    RuntimeTurnContext,
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


class ImageClient(Client):
    def __init__(self, *, expose_tool: bool = True) -> None:
        self.expose_tool = expose_tool
        self.calls = []

    async def list_tools(self):
        if not self.expose_tool:
            return ()
        return ({
            "name": "image_inspect",
            "description": "Observe image pixels.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string"},
                    "question": {"type": "string"},
                },
                "required": ["artifact_id", "question"],
            },
        },)

    async def read_resource(self, uri):
        assert uri == "knoa-artifact://image-a"
        return ({
            "media_type": "image/png",
            "blob": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC",
        },)

    async def call_tool(self, call):
        self.calls.append(call)
        return ToolStepResult(
            call_id=call.call_id,
            tool_name=call.name,
            status="completed",
            output={
                "observation_id": "observation-a",
                "artifact_id": "image-a",
                "observation": "The image contains a blue status panel.",
                "model": "vision",
            },
        )


class ImageConnector:
    def __init__(self, client: ImageClient) -> None:
        self.client = client

    def connect(self, grant):
        del grant
        client = self.client

        class Bound:
            async def __aenter__(self):
                return client

            async def __aexit__(self, *_args):
                return None

        return Bound()


def image_part() -> ArtifactPart:
    return ArtifactPart(
        artifact=ArtifactReference(
            artifact_id="image-a",
            name="photo.png",
            media_type="image/png",
            size_bytes=68,
            sha256="b" * 64,
        ),
        resource_uri="knoa-artifact://image-a",
        presentation="image",
    )


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
    usage = next(event.usage for event in events if event.event_type == "usage_reported")
    assert "completion_tokens" not in usage
    assert usage["completion_tokens_source"] == "unavailable"
    assert usage["prompt_tokens_source"] == "estimated"
    assert usage["prompt_tokens_estimated"] > 0
    checkpoint = store.load_checkpoint(session.runtime_session_ref)
    assert checkpoint is not None
    assert checkpoint.payload["messages"][-1] == {
        "role": "assistant",
        "content": "hello",
    }


@pytest.mark.asyncio
async def test_knoa_runtime_restores_compacted_summary_from_checkpoint(
    tmp_path: Path,
) -> None:
    provider = Provider()
    store = ContextCheckpointRepository(tmp_path / "context.db")
    runtime = KnoaAgentRuntime(
        provider,
        store,
        Connector(),
        system_prompt="system",
        health_probe=healthy,
        context_window=700,
        max_output_tokens=350,
    )
    session = await runtime.create_session(
        CreateRuntimeSession(operation_id="create-summary", binding_epoch=1)
    )
    checkpoint = store.load_checkpoint(session.runtime_session_ref)
    assert checkpoint is None
    from knoa_agent.context_store import ContextCheckpoint

    stored = ContextCheckpoint(
        runtime_session_ref=session.runtime_session_ref,
        state_version="1",
        source_cursor=8,
        agent_config_digest="agent",
        model_context_digest="model",
        payload={
            "messages": [
                {"role": "user", "content": "recent question"},
                {"role": "assistant", "content": "recent answer"},
            ],
            "summary": "User: earlier important request",
            "covered_messages": 6,
        },
        revision=1,
        created_at=0.0,
        updated_at=0.0,
    )
    store.save_checkpoint(stored, expected_revision=None)

    turn = await runtime.start_turn(
        RuntimeTurnRequest(
            session=session,
            operation_id="operation-summary",
            input=(TextPart(text="follow up"),),
            mcp=grant(),
            context=RuntimeTurnContext(core_memory=("preferred_language: zh",)),
        )
    )
    events = [event async for event in turn.events]

    assert events[-1].status == "completed"
    messages = provider.requests[0].messages
    assert "earlier important request" in messages[1]["content"]
    assert "preferred_language: zh" in messages[-2]["content"]
    restored = store.load_checkpoint(session.runtime_session_ref)
    assert restored is not None
    assert restored.payload["summary"] == "User: earlier important request"
    assert restored.payload["covered_messages"] == 6


@pytest.mark.asyncio
async def test_provider_usage_is_authoritative_and_missing_output_is_marked(
    tmp_path: Path,
) -> None:
    class UsageProvider(Provider):
        def stream(self, request, cancellation):
            del cancellation

            async def iterate():
                self.requests.append(request)
                yield ProviderChunk(content_delta="hello")
                yield ProviderChunk(
                    finish_reason="stop",
                    usage={"prompt_tokens": 77, "completion_tokens": 9},
                    terminal=True,
                )

            return iterate()

    runtime = KnoaAgentRuntime(
        UsageProvider(),
        ContextCheckpointRepository(tmp_path / "context.db"),
        Connector(),
        system_prompt="system",
        health_probe=healthy,
    )
    session = await runtime.create_session(
        CreateRuntimeSession(operation_id="create-usage", binding_epoch=1)
    )
    turn = await runtime.start_turn(
        RuntimeTurnRequest(
            session=session,
            operation_id="operation-usage",
            input=(TextPart(text="hi"),),
            mcp=grant(),
        )
    )
    events = [event async for event in turn.events]
    usage = next(event.usage for event in events if event.event_type == "usage_reported")

    assert usage["prompt_tokens"] == 77
    assert usage["completion_tokens"] == 9
    assert usage["prompt_tokens_source"] == "provider"
    assert usage["completion_tokens_source"] == "provider"
    assert usage["prompt_tokens_estimated"] > 0


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
    assert provider.requests[0].tools[1]["description"] == "Get weather for a location."


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
        "mcp__jira__issue_get",
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
async def test_text_model_uses_dedicated_image_inspect_before_answering(
    tmp_path: Path,
) -> None:
    class VisionRoutingProvider(Provider):
        def stream(self, request, cancellation):
            del cancellation

            async def iterate():
                self.requests.append(request)
                if len(self.requests) == 1:
                    yield ProviderChunk(
                        tool_calls=(ProposedToolCall(
                            call_id="inspect-a",
                            name="image_inspect",
                            arguments={
                                "artifact_id": "image-a",
                                "question": "What visible status is shown?",
                            },
                        ),),
                        finish_reason="tool_calls",
                        terminal=True,
                    )
                    return
                yield ProviderChunk(content_delta="The visible panel is blue.")
                yield ProviderChunk(finish_reason="stop", terminal=True)

            return iterate()

    provider = VisionRoutingProvider()
    client = ImageClient()
    runtime = KnoaAgentRuntime(
        provider,
        ContextCheckpointRepository(tmp_path / "context.db"),
        ImageConnector(client),
        system_prompt="system",
        health_probe=healthy,
        supports_vision=False,
    )
    session = await runtime.create_session(
        CreateRuntimeSession(operation_id="create-image", binding_epoch=1)
    )
    turn = await runtime.start_turn(RuntimeTurnRequest(
        session=session,
        operation_id="operation-image",
        input=(TextPart(text="What does this show?"), image_part()),
        mcp=grant(),
    ))

    events = [event async for event in turn.events]

    assert events[-1].status == "completed"
    assert [call.name for call in client.calls] == ["image_inspect"]
    first_content = provider.requests[0].messages[-1]["content"]
    assert any("You cannot see its pixels" in block.get("text", "") for block in first_content)
    assert not any(block.get("type") == "image" for block in first_content)
    assert "observation-a" in str(provider.requests[1].messages)


@pytest.mark.asyncio
async def test_text_model_cannot_finish_before_image_is_observed(
    tmp_path: Path,
) -> None:
    class DelayedInspectProvider(Provider):
        def stream(self, request, cancellation):
            del cancellation

            async def iterate():
                self.requests.append(request)
                if len(self.requests) == 1:
                    yield ProviderChunk(content_delta="It looks fine.")
                    yield ProviderChunk(finish_reason="stop", terminal=True)
                elif len(self.requests) == 2:
                    yield ProviderChunk(
                        tool_calls=(ProposedToolCall(
                            call_id="inspect-b",
                            name="image_inspect",
                            arguments={"artifact_id": "image-a", "question": "What is visible?"},
                        ),),
                        finish_reason="tool_calls",
                        terminal=True,
                    )
                else:
                    yield ProviderChunk(content_delta="Observed answer.")
                    yield ProviderChunk(finish_reason="stop", terminal=True)

            return iterate()

    provider = DelayedInspectProvider()
    runtime = KnoaAgentRuntime(
        provider,
        ContextCheckpointRepository(tmp_path / "context.db"),
        ImageConnector(ImageClient()),
        system_prompt="system",
        health_probe=healthy,
        supports_vision=False,
    )
    session = await runtime.create_session(
        CreateRuntimeSession(operation_id="create-guard", binding_epoch=1)
    )
    turn = await runtime.start_turn(RuntimeTurnRequest(
        session=session,
        operation_id="operation-guard",
        input=(image_part(),),
        mcp=grant(),
    ))

    events = [event async for event in turn.events]

    assert events[-1].status == "completed"
    assert len(provider.requests) == 3
    assert "has not been observed" in str(provider.requests[1].messages)


@pytest.mark.asyncio
@pytest.mark.parametrize("expose_image_inspect", [False, True])
async def test_native_vision_model_receives_image_bytes_directly(
    tmp_path: Path,
    expose_image_inspect: bool,
) -> None:
    provider = Provider()
    client = ImageClient(expose_tool=expose_image_inspect)
    runtime = KnoaAgentRuntime(
        provider,
        ContextCheckpointRepository(tmp_path / "context.db"),
        ImageConnector(client),
        system_prompt="system",
        health_probe=healthy,
        supports_vision=True,
    )
    session = await runtime.create_session(
        CreateRuntimeSession(operation_id="create-native-vision", binding_epoch=1)
    )
    turn = await runtime.start_turn(RuntimeTurnRequest(
        session=session,
        operation_id="operation-native-vision",
        input=(image_part(),),
        mcp=grant(),
    ))

    events = [event async for event in turn.events]

    assert events[-1].status == "completed"
    content = provider.requests[0].messages[-1]["content"]
    assert any(block.get("type") == "image" for block in content)
    guidance = "\n".join(block.get("text", "") for block in content)
    assert "Inline image: artifact_id=image-a; name=photo.png" in guidance
    assert "Analyze it directly." in guidance
    assert ("Reserve image_inspect for follow-up reinspection." in guidance) is (
        expose_image_inspect
    )
    assert client.calls == []


@pytest.mark.asyncio
async def test_text_model_reports_vision_unavailable_without_dedicated_tool(
    tmp_path: Path,
) -> None:
    provider = Provider()
    runtime = KnoaAgentRuntime(
        provider,
        ContextCheckpointRepository(tmp_path / "context.db"),
        ImageConnector(ImageClient(expose_tool=False)),
        system_prompt="system",
        health_probe=healthy,
        supports_vision=False,
    )
    session = await runtime.create_session(
        CreateRuntimeSession(operation_id="create-no-vision", binding_epoch=1)
    )
    turn = await runtime.start_turn(RuntimeTurnRequest(
        session=session,
        operation_id="operation-no-vision",
        input=(image_part(),),
        mcp=grant(),
    ))

    events = [event async for event in turn.events]

    assert events[-1].status == "failed"
    assert events[-1].error_code == "vision_unavailable"
    assert provider.requests == []


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

    # Verify that the interrupted turn's checkpoint WAS saved to context store!
    checkpoint = runtime._contexts.load_checkpoint(session.runtime_session_ref)
    assert checkpoint is not None
    messages = checkpoint.payload["messages"]
    assert len(messages) >= 2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "hi"
    assert messages[-1]["role"] == "assistant"
    assert "[未完成回答" in messages[-1]["content"] or "[由于超时" in messages[-1]["content"]


class FailingToolProvider(Provider):
    def stream(self, request, cancellation):
        del cancellation

        async def iterate():
            self.requests.append(request)
            if len(self.requests) == 1:
                yield ProviderChunk(
                    tool_calls=(
                        ProposedToolCall(
                            call_id="call-oom-1",
                            name="mcp__gitlab__gitlab_retry_oom_jobs",
                            arguments={
                                "project": "software/gs_map",
                                "pipeline_id": 757409,
                                "job_ids": [9462790],
                                "max_attempts": 1,
                            },
                        ),
                    ),
                    finish_reason="tool_calls",
                    terminal=True,
                )
                return
            yield ProviderChunk(content_delta="retry failed, stop")
            yield ProviderChunk(finish_reason="stop", terminal=True)

        return iterate()


class FailingToolClient(Client):
    async def list_tools(self):
        return (
            {
                "name": "mcp__gitlab__gitlab_retry_oom_jobs",
                "description": "Retry OOM jobs",
                "inputSchema": {
                    "type": "object",
                    "properties": {"project": {"type": "string"}},
                    "required": ["project"],
                },
            },
        )

    async def call_tool(self, call):
        raise RuntimeError("Invalid request parameters")


class FailingToolConnector:
    def connect(self, grant):
        del grant

        class Bound:
            async def __aenter__(self):
                return FailingToolClient()

            async def __aexit__(self, *_args):
                return None

        return Bound()


@pytest.mark.asyncio
async def test_runtime_converts_mcp_tool_call_failure_to_failed_result(
    tmp_path: Path,
) -> None:
    provider = FailingToolProvider()
    runtime = KnoaAgentRuntime(
        provider,
        ContextCheckpointRepository(tmp_path / "context.db"),
        FailingToolConnector(),
        system_prompt="system",
        health_probe=healthy,
    )
    session = await runtime.create_session(
        CreateRuntimeSession(operation_id="create-a", binding_epoch=1)
    )
    turn = await runtime.start_turn(
        RuntimeTurnRequest(
            session=session,
            operation_id="operation-a",
            input=(TextPart(text="retry the oom job"),),
            mcp=grant(),
        )
    )

    events = [event async for event in turn.events]

    finished = [
        event
        for event in events
        if event.event_type == "tool_call_finished"
    ]
    assert len(finished) == 1
    assert finished[0].tool_name == "mcp__gitlab__gitlab_retry_oom_jobs"
    assert finished[0].status == "failed"
    assert finished[0].code == "mcp_tool_call_failed"
    assert "Invalid request parameters" in str(finished[0].output)
    assert events[-1].event_type == "turn_finished"
    assert events[-1].status == "completed"
    assert events[-1].final_output == "retry failed, stop"


def test_sanitize_messages_for_abort():
    # Case 1: unfulfilled tool call gets synthetic tool response and closing assistant message
    messages = [
        {"role": "user", "content": "search something"},
        {
            "role": "assistant",
            "content": "searching",
            "tool_calls": [
                {"id": "call-1", "type": "function", "function": {"name": "web_search", "arguments": "{}"}},
                {"id": "call-2", "type": "function", "function": {"name": "web_search", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "result 1"},
    ]
    sanitized = KnoaAgentRuntime._sanitize_messages_for_abort(
        messages, reason="cancelled", partial_content="searching"
    )
    assert len(sanitized) == 5
    # call-2 got synthetic tool response
    assert sanitized[3]["role"] == "tool"
    assert sanitized[3]["tool_call_id"] == "call-2"
    assert "cancelled" in sanitized[3]["content"]
    # Ends with assistant message
    assert sanitized[4]["role"] == "assistant"
    assert "searching" in sanitized[4]["content"]
    assert "[由于超时或取消，操作未全部完成]" in sanitized[4]["content"]


@pytest.mark.asyncio
async def test_subsequent_turn_sees_interrupted_turn_in_context(
    tmp_path: Path,
) -> None:
    provider = Provider(wait=False)
    runtime = KnoaAgentRuntime(
        provider,
        ContextCheckpointRepository(tmp_path / "context.db"),
        Connector(),
        system_prompt="system",
        health_probe=healthy,
    )
    session = await runtime.create_session(
        CreateRuntimeSession(operation_id="create-seq", binding_epoch=1)
    )

    # Turn 1: Starts and gets interrupted
    interruptible_provider = Provider(wait=True)
    runtime._provider = interruptible_provider

    turn1 = await runtime.start_turn(
        RuntimeTurnRequest(
            session=session,
            operation_id="turn-1",
            input=(TextPart(text="帮我查苏州的山"),),
            mcp=grant(),
        )
    )
    consume = asyncio.create_task(anext(turn1.events))
    await asyncio.sleep(0)
    await runtime.interrupt_turn(
        RuntimeInterruptCommand(
            session=session,
            runtime_turn_ref=turn1.runtime_turn_ref,
            command_id="interrupt-seq",
        )
    )
    first = await consume
    assert first.status == "interrupted"

    # Turn 2: Standard execution
    runtime._provider = provider
    turn2 = await runtime.start_turn(
        RuntimeTurnRequest(
            session=session,
            operation_id="turn-2",
            input=(TextPart(text="换个引擎搜"),),
            mcp=grant(),
        )
    )
    events2 = [event async for event in turn2.events]
    assert events2[-1].status == "completed"

    # Verify provider received the Turn 1 user query in Turn 2's request messages!
    assert len(provider.requests) == 1
    req_messages = provider.requests[0].messages
    user_contents = [str(m.get("content")) for m in req_messages if m.get("role") == "user"]
    assert any("帮我查苏州的山" in c for c in user_contents)
    assert any("换个引擎搜" in c for c in user_contents)

