from __future__ import annotations

import asyncio
import json

import pytest

from pc_assistant.agent_runtime.http_provider import (
    FailoverModelProvider,
    HttpModelProvider,
)
from pc_assistant.agent_runtime.model_step import ProviderCallRequest, ProviderChunk
from pc_assistant.config import ResolvedModelConfig


class FakeResponse:
    def __init__(self, lines: list[str], *, status_error: Exception | None = None):
        self.lines = lines
        self.status_error = status_error
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.closed = True

    def raise_for_status(self) -> None:
        if self.status_error is not None:
            raise self.status_error

    async def aiter_bytes(self):
        yield ("\n".join(self.lines) + "\n").encode()

    async def aclose(self) -> None:
        self.closed = True


class FakeClient:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    def stream(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.response

    async def get(self, url, **kwargs):
        self.requests.append(("GET", url, kwargs))
        return self.response


class ClientFactory:
    def __init__(self, client: FakeClient):
        self.client = client

    def __call__(self, **kwargs):
        return self.client


def _model(driver: str = "openai_compatible") -> ResolvedModelConfig:
    return ResolvedModelConfig(
        alias="main",
        provider_name="provider",
        driver=driver,
        server_url="http://model.local",
        api_base="",
        api_key="secret",
        model="model-a",
        supports_vision=False,
        context_window=8192,
        timeout=30,
    )


def _request() -> ProviderCallRequest:
    return ProviderCallRequest(
        call_id="model-call-a",
        purpose="react",
        messages=({"role": "user", "content": "hello"},),
    )


@pytest.mark.asyncio
async def test_openai_provider_streams_normalized_content_and_tool_call() -> None:
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": "hi"}}]}),
        "data: "
        + json.dumps(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call-a",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"path":"a.txt"}',
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        ),
        "data: "
        + json.dumps(
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}
        ),
        "data: [DONE]",
    ]
    client = FakeClient(FakeResponse(lines))
    provider = HttpModelProvider(_model(), client_factory=ClientFactory(client))

    chunks = [chunk async for chunk in provider.stream(_request(), asyncio.Event())]

    assert chunks[0].content_delta == "hi"
    assert chunks[-1].terminal
    assert chunks[-1].finish_reason == "tool_calls"
    assert chunks[-1].tool_calls[0].arguments == {"path": "a.txt"}
    assert chunks[-1].provider_model == "main"
    payload = client.requests[0][2]["json"]
    assert payload["stream"] is True
    assert payload["model"] == "model-a"


@pytest.mark.asyncio
async def test_anthropic_provider_streams_normalized_terminal_call() -> None:
    events = [
        ("content_block_start", {"index": 0, "content_block": {"type": "text"}}),
        ("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "hi"}}),
        (
            "content_block_start",
            {"index": 1, "content_block": {"type": "tool_use", "id": "call-a", "name": "read_file"}},
        ),
        (
            "content_block_delta",
            {"index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"path":"a.txt"}'}},
        ),
        ("content_block_stop", {"index": 1}),
        ("message_delta", {"delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 2}}),
        ("message_stop", {}),
    ]
    lines = []
    for event_type, data in events:
        lines.extend([f"event: {event_type}", "data: " + json.dumps(data), ""])
    provider = HttpModelProvider(
        _model("anthropic"),
        client_factory=ClientFactory(FakeClient(FakeResponse(lines))),
    )

    chunks = [chunk async for chunk in provider.stream(_request(), asyncio.Event())]

    assert chunks[0].content_delta == "hi"
    assert chunks[-1].finish_reason == "tool_calls"
    assert chunks[-1].tool_calls[0].name == "read_file"


@pytest.mark.asyncio
async def test_provider_errors_are_redacted_and_health_is_typed() -> None:
    response = FakeResponse([], status_error=RuntimeError("credential secret"))
    provider = HttpModelProvider(
        _model(),
        client_factory=ClientFactory(FakeClient(response)),
    )

    chunks = [chunk async for chunk in provider.stream(_request(), asyncio.Event())]
    health = await provider.health_check()

    assert chunks[-1].error_code == "provider_failed"
    assert "credential" not in chunks[-1].model_dump_json()
    assert not health.healthy
    assert "credential" not in health.detail


class StaticProvider:
    def __init__(self, chunks: tuple[ProviderChunk, ...]):
        self.chunks = chunks
        self.calls = 0

    def stream(self, request, cancellation):
        async def iterate():
            self.calls += 1
            for chunk in self.chunks:
                yield chunk

        return iterate()


@pytest.mark.asyncio
async def test_failover_never_replays_after_partial_primary_output() -> None:
    primary = StaticProvider(
        (
            ProviderChunk(content_delta="partial"),
            ProviderChunk(finish_reason="error", terminal=True, error_code="provider_failed"),
        )
    )
    fallback = StaticProvider((ProviderChunk(finish_reason="stop", terminal=True),))
    provider = FailoverModelProvider(primary, fallback)

    chunks = [chunk async for chunk in provider.stream(_request(), asyncio.Event())]

    assert [chunk.content_delta for chunk in chunks if chunk.content_delta] == ["partial"]
    assert chunks[-1].finish_reason == "error"
    assert fallback.calls == 0


@pytest.mark.asyncio
async def test_failover_uses_fallback_before_any_primary_output() -> None:
    primary = StaticProvider(
        (ProviderChunk(finish_reason="error", terminal=True, error_code="provider_failed"),)
    )
    fallback = StaticProvider((ProviderChunk(finish_reason="stop", terminal=True),))
    provider = FailoverModelProvider(primary, fallback)

    chunks = [chunk async for chunk in provider.stream(_request(), asyncio.Event())]

    assert chunks[-1].finish_reason == "stop"
    assert fallback.calls == 1
    assert chunks[-1].failover_used


@pytest.mark.asyncio
async def test_failover_uses_fallback_when_primary_ends_without_terminal() -> None:
    primary = StaticProvider(())
    fallback = StaticProvider((ProviderChunk(finish_reason="stop", terminal=True),))
    provider = FailoverModelProvider(primary, fallback)

    chunks = [chunk async for chunk in provider.stream(_request(), asyncio.Event())]

    assert chunks == [
        ProviderChunk(
            finish_reason="stop",
            terminal=True,
            failover_used=True,
        )
    ]
    assert fallback.calls == 1


@pytest.mark.asyncio
async def test_failover_reports_partial_primary_without_terminal_as_failure() -> None:
    primary = StaticProvider((ProviderChunk(content_delta="partial"),))
    fallback = StaticProvider((ProviderChunk(finish_reason="stop", terminal=True),))
    provider = FailoverModelProvider(primary, fallback)

    chunks = [chunk async for chunk in provider.stream(_request(), asyncio.Event())]

    assert chunks[0].content_delta == "partial"
    assert chunks[-1].terminal
    assert chunks[-1].finish_reason == "error"
    assert chunks[-1].error_code == "provider_failed"
    assert fallback.calls == 0
