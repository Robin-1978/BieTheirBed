from __future__ import annotations

import asyncio
import json

import pytest

from knoa_platform.agent_runtime.http_provider import (
    FailoverModelProvider,
    HttpModelProvider,
)
from knoa_platform.agent_runtime.model_step import ProviderCallRequest, ProviderChunk
from knoa_platform.config import ResolvedModelConfig


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
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
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


def _request(
    *,
    tools: tuple[dict, ...] = (),
) -> ProviderCallRequest:
    return ProviderCallRequest(
        call_id="model-call-a",
        purpose="react",
        messages=({"role": "user", "content": "hello"},),
        tools=tools,
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

    request = _request(
        tools=(
            {
                "name": "read_file",
                "description": "Read a file",
                "inputSchema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        )
    )
    chunks = [chunk async for chunk in provider.stream(request, asyncio.Event())]

    assert chunks[0].content_delta == "hi"
    assert chunks[-1].terminal
    assert chunks[-1].finish_reason == "tool_calls"
    assert chunks[-1].tool_calls[0].arguments == {"path": "a.txt"}
    assert chunks[-1].provider_model == "main"
    payload = client.requests[0][2]["json"]
    assert payload["stream"] is True
    assert payload["model"] == "model-a"
    assert payload["tools"][0]["function"]["parameters"] == {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }


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
    client = FakeClient(FakeResponse(lines))
    provider = HttpModelProvider(
        _model("anthropic"),
        client_factory=ClientFactory(client),
    )

    request = _request(
        tools=(
            {
                "name": "read_file",
                "description": "Read a file",
                "inputSchema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            },
        )
    )
    chunks = [chunk async for chunk in provider.stream(request, asyncio.Event())]

    assert chunks[0].content_delta == "hi"
    assert chunks[-1].finish_reason == "tool_calls"
    assert chunks[-1].tool_calls[0].name == "read_file"
    assert client.requests[0][2]["json"]["tools"][0]["input_schema"] == {
        "type": "object",
        "properties": {"path": {"type": "string"}},
    }


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


@pytest.mark.asyncio
async def test_responses_provider_posts_input_and_streams_tool_call() -> None:
    lines = [
        "data: " + json.dumps({"type": "response.output_text.delta", "delta": "hi"}),
        "data: "
        + json.dumps(
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {"type": "function_call", "call_id": "call-a", "name": "read_file"},
            }
        ),
        "data: "
        + json.dumps(
            {
                "type": "response.function_call_arguments.delta",
                "output_index": 0,
                "delta": '{"path":',
            }
        ),
        "data: "
        + json.dumps(
            {
                "type": "response.function_call_arguments.delta",
                "output_index": 0,
                "delta": '"a.txt"}',
            }
        ),
        "data: "
        + json.dumps(
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                },
            }
        ),
    ]
    client = FakeClient(FakeResponse(lines))
    provider = HttpModelProvider(
        _model("openai_responses"),
        client_factory=ClientFactory(client),
    )

    request = _request(
        tools=(
            {
                "name": "read_file",
                "description": "Read a file",
                "inputSchema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            },
        )
    )
    chunks = [chunk async for chunk in provider.stream(request, asyncio.Event())]

    assert chunks[0].content_delta == "hi"
    assert chunks[-1].terminal
    assert chunks[-1].finish_reason == "tool_calls"
    assert chunks[-1].tool_calls[0].name == "read_file"
    assert chunks[-1].tool_calls[0].arguments == {"path": "a.txt"}
    assert chunks[-1].usage == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }
    method, url, kwargs = client.requests[0]
    assert method == "POST"
    assert url.endswith("/responses")
    payload = kwargs["json"]
    assert payload["stream"] is True
    assert payload["model"] == "model-a"
    assert payload["input"] == [{"role": "user", "content": "hello"}]
    assert payload["max_output_tokens"] == request.max_output_tokens
    assert payload["tools"] == [
        {
            "type": "function",
            "name": "read_file",
            "description": "Read a file",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
        }
    ]


@pytest.mark.asyncio
async def test_responses_provider_replays_tool_history_as_input_items() -> None:
    lines = [
        "data: "
        + json.dumps(
            {
                "type": "response.completed",
                "response": {"status": "completed", "usage": {}},
            }
        ),
    ]
    client = FakeClient(FakeResponse(lines))
    provider = HttpModelProvider(
        _model("openai_responses"),
        client_factory=ClientFactory(client),
    )

    request = ProviderCallRequest(
        call_id="model-call-b",
        purpose="react",
        messages=(
            {"role": "user", "content": "read it"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-a",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path":"a.txt"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-a", "content": "file-bytes"},
        ),
    )
    chunks = [chunk async for chunk in provider.stream(request, asyncio.Event())]

    assert chunks[-1].terminal
    assert chunks[-1].finish_reason == "stop"
    payload = client.requests[0][2]["json"]
    assert payload["input"] == [
        {"role": "user", "content": "read it"},
        {
            "type": "function_call",
            "call_id": "call-a",
            "name": "read_file",
            "arguments": '{"path":"a.txt"}',
        },
        {"type": "function_call_output", "call_id": "call-a", "output": "file-bytes"},
    ]


def _model_with_proxy() -> ResolvedModelConfig:
    model = _model()
    return model.model_copy(update={"proxy_url": "http://proxy.local:8080"})


@pytest.mark.asyncio
async def test_provider_passes_proxy_to_client_factory_only_when_set() -> None:
    lines = ["data: " + json.dumps({"choices": [{"delta": {"content": "hi"}}]}), "data: [DONE]"]
    proxied = FakeClient(FakeResponse(list(lines)))
    proxied_factory = ClientFactory(proxied)
    awaitable = HttpModelProvider(
        _model_with_proxy(), client_factory=proxied_factory
    )
    chunks = [chunk async for chunk in awaitable.stream(_request(), asyncio.Event())]
    assert chunks[0].content_delta == "hi"
    factory_calls = proxied_factory.calls
    assert factory_calls[0]["proxy"] == "http://proxy.local:8080"

    direct = FakeClient(FakeResponse(list(lines)))
    direct_factory = ClientFactory(direct)
    plain = HttpModelProvider(_model(), client_factory=direct_factory)
    [chunk async for chunk in plain.stream(_request(), asyncio.Event())]
    assert "proxy" not in direct_factory.calls[0]


def _model_with_session() -> ResolvedModelConfig:
    model = _model()
    return model.model_copy(update={"session_header": "x-opencode-session"})


@pytest.mark.asyncio
async def test_provider_sends_stable_session_and_product_ua_only_when_configured() -> None:
    from knoa_platform import __version__

    lines = ["data: " + json.dumps({"choices": [{"delta": {"content": "hi"}}]}), "data: [DONE]"]
    sessioned = FakeClient(FakeResponse(list(lines)))
    provider = HttpModelProvider(
        _model_with_session(), client_factory=ClientFactory(sessioned)
    )
    [chunk async for chunk in provider.stream(_request(), asyncio.Event())]
    sent = sessioned.requests[0][2]["headers"]
    assert sent["x-opencode-session"]
    assert len(sent["x-opencode-session"]) == 32
    assert sent["User-Agent"] == f"knoa-node/{__version__}"
    # stable across calls on the same instance
    second = FakeClient(FakeResponse(list(lines)))
    provider._client_factory = ClientFactory(second)
    [chunk async for chunk in provider.stream(_request(), asyncio.Event())]
    assert second.requests[0][2]["headers"]["x-opencode-session"] == sent["x-opencode-session"]

    direct = FakeClient(FakeResponse(list(lines)))
    plain = HttpModelProvider(_model(), client_factory=ClientFactory(direct))
    [chunk async for chunk in plain.stream(_request(), asyncio.Event())]
    assert "x-opencode-session" not in direct.requests[0][2]["headers"]
    assert "User-Agent" not in direct.requests[0][2]["headers"]
