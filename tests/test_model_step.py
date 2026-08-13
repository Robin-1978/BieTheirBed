from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from knoa_platform.agent_runtime.model_step import (
    ModelStep,
    ModelStepRequest,
    ProviderCallRequest,
    ProviderChunk,
)
from knoa_platform.agent_runtime.contracts import RuntimeScope
from knoa_platform.agent_runtime.tool_step import ProposedToolCall


_SCOPE = RuntimeScope(principal_id="local", session_handle="session-a")


class FakeProvider:
    def __init__(self, chunks: tuple[ProviderChunk, ...], error: Exception | None = None):
        self.chunks = chunks
        self.error = error
        self.requests: list[ProviderCallRequest] = []

    def stream(
        self,
        request: ProviderCallRequest,
        cancellation: asyncio.Event,
    ) -> AsyncIterator[ProviderChunk]:
        async def iterate() -> AsyncIterator[ProviderChunk]:
            self.requests.append(request)
            if self.error is not None:
                raise self.error
            for chunk in self.chunks:
                yield chunk

        return iterate()


@pytest.mark.asyncio
async def test_model_step_streams_and_returns_normalized_terminal_result() -> None:
    provider = FakeProvider(
        (
            ProviderChunk(content_delta="hello "),
            ProviderChunk(reasoning_delta="checking"),
            ProviderChunk(
                content_delta="world",
                tool_calls=(
                    ProposedToolCall(call_id="call-a", name="read_file"),
                ),
                finish_reason="tool_calls",
                usage={"prompt_tokens": 10},
                terminal=True,
                provider_model="fallback-model",
                failover_used=True,
            ),
        )
    )
    step = ModelStep(provider, call_id_factory=lambda: "model-call-a")

    events = [
        event
        async for event in step.run(
            ModelStepRequest(
                scope=_SCOPE,
                messages=({"role": "user", "content": "hi"},),
            ),
            asyncio.Event(),
        )
    ]

    assert [event.event_type for event in events] == [
        "content_delta",
        "reasoning_delta",
        "content_delta",
        "terminal",
    ]
    result = events[-1].result
    assert result is not None
    assert result.status == "completed"
    assert result.content == "hello world"
    assert result.tool_calls[0].name == "read_file"
    assert result.provider_model == "fallback-model"
    assert result.failover_used
    assert result.schema_tokens_estimated == 0
    assert provider.requests[0].call_id == "model-call-a"


@pytest.mark.asyncio
async def test_model_step_fails_closed_on_provider_error_or_missing_terminal() -> None:
    raised = ModelStep(FakeProvider((), RuntimeError("secret provider detail")))
    missing = ModelStep(FakeProvider((ProviderChunk(content_delta="partial"),)))
    request = ModelStepRequest(
        scope=_SCOPE,
        messages=({"role": "user", "content": "hi"},),
    )

    raised_events = [event async for event in raised.run(request, asyncio.Event())]
    missing_events = [event async for event in missing.run(request, asyncio.Event())]

    assert raised_events[-1].result is not None
    assert raised_events[-1].result.status == "failed"
    assert raised_events[-1].result.error_code == "provider_failed"
    assert "secret" not in raised_events[-1].model_dump_json()
    assert missing_events[-1].result is not None
    assert missing_events[-1].result.status == "failed"


@pytest.mark.asyncio
async def test_model_step_honors_cancellation_before_provider_call() -> None:
    provider = FakeProvider(())
    cancellation = asyncio.Event()
    cancellation.set()

    events = [
        event
        async for event in ModelStep(provider).run(
            ModelStepRequest(
                scope=_SCOPE,
                messages=({"role": "user", "content": "hi"},),
            ),
            cancellation,
        )
    ]

    assert events[-1].result is not None
    assert events[-1].result.status == "cancelled"
    assert provider.requests == []


@pytest.mark.asyncio
async def test_model_step_accounts_for_tool_schema_in_prompt_budget() -> None:
    provider = FakeProvider((ProviderChunk(finish_reason="stop", terminal=True),))
    large = "x" * 8000
    request = ModelStepRequest(
        scope=_SCOPE,
        messages=(
            {"role": "system", "content": "system"},
            {"role": "user", "content": large},
        ),
        tools=({"name": "tool", "description": large},),
        prompt_budget=1000,
    )

    events = [event async for event in ModelStep(provider).run(request, asyncio.Event())]

    assert events[-1].result is not None
    assert events[-1].result.status == "failed"
    assert events[-1].result.error_code == "context_budget_exceeded"
    assert events[-1].result.schema_tokens_estimated > 0
    assert provider.requests == []


@pytest.mark.asyncio
async def test_system_and_runtime_context_are_ephemeral_provider_messages() -> None:
    provider = FakeProvider((ProviderChunk(finish_reason="stop", terminal=True),))
    durable = ({"role": "user", "content": "current"},)
    request = ModelStepRequest(
        scope=_SCOPE,
        messages=durable,
        system_prompt="system",
        runtime_context="memory",
    )

    events = [event async for event in ModelStep(provider).run(request, asyncio.Event())]

    assert events[-1].result is not None
    assert provider.requests[0].messages == (
        {"role": "system", "content": "system"},
        {"role": "user", "content": "memory"},
        {"role": "user", "content": "current"},
    )
    assert request.messages == durable
