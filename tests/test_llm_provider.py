from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from pc_assistant.llm_provider import LLMMessage, LLMProvider, LLMResponse, StreamChunk


class TestLLMMessage:
    def test_creation(self):
        m = LLMMessage(role="user", content="hello")
        assert m.role == "user"
        assert m.content == "hello"


class TestLLMResponse:
    def test_defaults(self):
        r = LLMResponse(content="hi")
        assert r.content == "hi"
        assert r.tool_calls == []
        assert r.finish_reason == ""
        assert r.usage == {}

    def test_with_tool_calls(self):
        tc = [{"id": "call_1", "function": {"name": "test", "arguments": {}}}]
        r = LLMResponse(content="", tool_calls=tc, finish_reason="tool_calls")
        assert len(r.tool_calls) == 1
        assert r.finish_reason == "tool_calls"


class TestStreamChunk:
    def test_defaults(self):
        c = StreamChunk()
        assert c.delta_content == ""
        assert c.delta_tool_calls == []
        assert c.finish_reason == ""

    def test_with_values(self):
        c = StreamChunk(delta_content="hello", finish_reason="stop")
        assert c.delta_content == "hello"
        assert c.finish_reason == "stop"


class TestLLMProviderInit:
    def test_llamacpp_default(self):
        p = LLMProvider(server_url="http://localhost:8080", model_name="qwen")
        assert p._server_url == "http://localhost:8080"
        assert p._model_name == "qwen"
        assert p._provider == "llamacpp"
        assert p._headers == {}

    def test_trailing_slash_stripped(self):
        p = LLMProvider(server_url="http://localhost:8080/")
        assert p._server_url == "http://localhost:8080"

    def test_defaults(self):
        p = LLMProvider()
        assert p._server_url == "http://127.0.0.1:8080"
        assert p._timeout == 120.0
        assert p._max_retries == 3
        assert p._provider == "llamacpp"
        assert p._headers == {}

    def test_openai_provider(self):
        p = LLMProvider(provider="openai", api_key="sk-test123", model_name="gpt-4")
        assert p._server_url == "https://api.openai.com/v1"
        assert p._headers == {"Authorization": "Bearer sk-test123"}
        assert p._provider == "openai"

    def test_openai_no_key(self):
        p = LLMProvider(provider="openai", model_name="gpt-4")
        assert p._server_url == "https://api.openai.com/v1"
        assert p._headers == {}

    def test_anthropic_provider(self):
        p = LLMProvider(provider="anthropic", api_key="sk-ant-test", model_name="claude-3")
        assert p._server_url == "https://api.anthropic.com"
        assert p._headers == {"x-api-key": "sk-ant-test", "anthropic-version": "2023-06-01"}
        assert p._provider == "anthropic"

    def test_anthropic_no_key(self):
        p = LLMProvider(provider="anthropic", model_name="claude-3")
        assert p._server_url == "https://api.anthropic.com"
        assert p._headers == {}

    def test_openai_compatible_with_api_base(self):
        p = LLMProvider(provider="openai_compatible", api_key="key123", api_base="http://my-server:8000/v1", model_name="local")
        assert p._server_url == "http://my-server:8000/v1"
        assert p._headers == {"Authorization": "Bearer key123"}

    def test_openai_compatible_fallback_to_server_url(self):
        p = LLMProvider(provider="openai_compatible", server_url="http://fallback:8080", model_name="local")
        assert p._server_url == "http://fallback:8080"
        assert p._headers == {}

    def test_openai_compatible_api_base_trailing_slash(self):
        p = LLMProvider(provider="openai_compatible", api_base="http://my-server:8000/v1/", model_name="local")
        assert p._server_url == "http://my-server:8000/v1"


class TestLLMProviderNormalize:
    def test_normalize_tool_calls_string_args(self):
        p = LLMProvider()
        raw = [{"function": {"name": "test", "arguments": '{"key": "value"}'}}]
        result = p._normalize_tool_calls(raw)
        assert result[0]["function"]["arguments"] == {"key": "value"}

    def test_normalize_tool_calls_dict_args(self):
        p = LLMProvider()
        raw = [{"function": {"name": "test", "arguments": {"key": "value"}}}]
        result = p._normalize_tool_calls(raw)
        assert result[0]["function"]["arguments"] == {"key": "value"}

    def test_normalize_tool_calls_invalid_json(self):
        p = LLMProvider()
        raw = [{"function": {"name": "test", "arguments": "not json"}}]
        result = p._normalize_tool_calls(raw)
        assert result[0]["function"]["arguments"] == {}

    def test_normalize_tool_calls_none_args(self):
        p = LLMProvider()
        raw = [{"function": {"name": "test", "arguments": None}}]
        result = p._normalize_tool_calls(raw)
        assert result[0]["function"]["arguments"] == {}

    def test_normalize_tool_calls_empty(self):
        p = LLMProvider()
        result = p._normalize_tool_calls([])
        assert result == []

    def test_format_tool_result_message(self):
        msg = LLMProvider.format_tool_result_message("call_123", "result data")
        assert msg["role"] == "tool"
        assert msg["tool_call_id"] == "call_123"
        assert msg["content"] == "result data"


class TestAnthropicAdapter:
    def test_build_anthropic_payload_system_extraction(self):
        p = LLMProvider(provider="anthropic", api_key="key", model_name="claude-3")
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        payload = p._build_anthropic_payload(messages)
        assert payload["system"] == "You are helpful."
        assert len(payload["messages"]) == 1
        assert payload["messages"][0]["role"] == "user"

    def test_build_anthropic_payload_multiple_system(self):
        p = LLMProvider(provider="anthropic", api_key="key", model_name="claude-3")
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "Hello"},
        ]
        payload = p._build_anthropic_payload(messages)
        assert "You are helpful." in payload["system"]
        assert "Be concise." in payload["system"]
        assert len(payload["messages"]) == 1

    def test_build_anthropic_payload_with_tools(self):
        p = LLMProvider(provider="anthropic", api_key="key", model_name="claude-3")
        tools = [
            {"type": "function", "function": {"name": "test", "description": "A test tool", "parameters": {"type": "object", "properties": {"x": {"type": "string"}}}}},
        ]
        payload = p._build_anthropic_payload([], tools=tools)
        assert len(payload["tools"]) == 1
        assert payload["tools"][0]["name"] == "test"
        assert payload["tools"][0]["input_schema"]["properties"]["x"]["type"] == "string"

    def test_convert_tools_to_anthropic(self):
        p = LLMProvider(provider="anthropic", api_key="key", model_name="claude-3")
        tools = [
            {"type": "function", "function": {"name": "read_file", "description": "Read a file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
        ]
        result = p._convert_tools_to_anthropic(tools)
        assert len(result) == 1
        assert result[0]["name"] == "read_file"
        assert result[0]["description"] == "Read a file"
        assert "input_schema" in result[0]

    def test_convert_tools_to_anthropic_default_parameters(self):
        p = LLMProvider(provider="anthropic", api_key="key", model_name="claude-3")
        tools = [
            {"type": "function", "function": {"name": "simple", "description": "No params"}},
        ]
        result = p._convert_tools_to_anthropic(tools)
        assert result[0]["input_schema"] == {"type": "object", "properties": {}}

    def test_parse_anthropic_response_text_only(self):
        p = LLMProvider(provider="anthropic", api_key="key", model_name="claude-3")
        data = {
            "content": [{"type": "text", "text": "Hello world"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        result = p._parse_anthropic_response(data)
        assert result.content == "Hello world"
        assert result.finish_reason == "end_turn"
        assert result.tool_calls == []

    def test_parse_anthropic_response_with_tool_use(self):
        p = LLMProvider(provider="anthropic", api_key="key", model_name="claude-3")
        data = {
            "content": [
                {"type": "text", "text": "Let me check."},
                {"type": "tool_use", "id": "toolu_123", "name": "read_file", "input": {"path": "/tmp/test.txt"}},
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 20, "output_tokens": 10},
        }
        result = p._parse_anthropic_response(data)
        assert "Let me check." in result.content
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["id"] == "toolu_123"
        assert result.tool_calls[0]["function"]["name"] == "read_file"
        assert result.tool_calls[0]["function"]["arguments"] == {"path": "/tmp/test.txt"}
        assert result.finish_reason == "tool_use"


class TestLLMProviderChat:
    @pytest.mark.asyncio
    async def test_chat_success(self):
        p = LLMProvider()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Hello!", "tool_calls": []}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(p, "_request_with_retry", return_value=mock_response):
            result = await p.chat([{"role": "user", "content": "hi"}])
            assert result.content == "Hello!"
            assert result.finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_chat_with_tools(self):
        p = LLMProvider()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": "",
                    "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "test", "arguments": "{}"}}],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {},
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(p, "_request_with_retry", return_value=mock_response):
            result = await p.chat(
                [{"role": "user", "content": "do something"}],
                tools=[{"type": "function", "function": {"name": "test", "parameters": {}}}],
            )
            assert len(result.tool_calls) == 1

    @pytest.mark.asyncio
    async def test_chat_connection_error(self):
        p = LLMProvider()
        with patch.object(p, "_request_with_retry", side_effect=httpx.ConnectError("Connection refused")):
            result = await p.chat([{"role": "user", "content": "hi"}])
            assert result.finish_reason == "error"
            assert "failed" in result.content.lower()

    @pytest.mark.asyncio
    async def test_chat_generic_error(self):
        p = LLMProvider()
        with patch.object(p, "_request_with_retry", side_effect=RuntimeError("unexpected")):
            result = await p.chat([{"role": "user", "content": "hi"}])
            assert result.finish_reason == "error"

    @pytest.mark.asyncio
    async def test_chat_anthropic_success(self):
        p = LLMProvider(provider="anthropic", api_key="key", model_name="claude-3")
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "content": [{"type": "text", "text": "Hi from Claude!"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(p, "_request_with_retry", return_value=mock_response):
            result = await p.chat([{"role": "user", "content": "hi"}])
            assert result.content == "Hi from Claude!"
            assert result.finish_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_chat_anthropic_with_tools(self):
        p = LLMProvider(provider="anthropic", api_key="key", model_name="claude-3")
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "content": [
                {"type": "text", "text": "Let me look."},
                {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {"path": "test.txt"}},
            ],
            "stop_reason": "tool_use",
            "usage": {},
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(p, "_request_with_retry", return_value=mock_response):
            result = await p.chat(
                [{"role": "user", "content": "read file"}],
                tools=[{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
            )
            assert len(result.tool_calls) == 1
            assert result.tool_calls[0]["function"]["name"] == "read_file"

    @pytest.mark.asyncio
    async def test_chat_anthropic_error(self):
        p = LLMProvider(provider="anthropic", api_key="key", model_name="claude-3")
        with patch.object(p, "_request_with_retry", side_effect=httpx.ConnectError("Connection refused")):
            result = await p.chat([{"role": "user", "content": "hi"}])
            assert result.finish_reason == "error"

    @pytest.mark.asyncio
    async def test_chat_openai_sends_headers(self):
        p = LLMProvider(provider="openai", api_key="sk-test", model_name="gpt-4")
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Hi!"}, "finish_reason": "stop"}],
            "usage": {},
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(p, "_request_with_retry", return_value=mock_response) as mock_retry:
            await p.chat([{"role": "user", "content": "hi"}])
            call_kwargs = mock_retry.call_args
            assert call_kwargs[1].get("headers") == {"Authorization": "Bearer sk-test"} or \
                   (len(call_kwargs) > 1 and call_kwargs[1].get("headers") == {"Authorization": "Bearer sk-test"})


class TestLLMProviderHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_success(self):
        p = LLMProvider()
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch.object(httpx.AsyncClient, "__aenter__", return_value=MagicMock()) as mock_enter:
            mock_client = MagicMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_enter.return_value = mock_client

            result = await p.health_check()
            assert result is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self):
        p = LLMProvider()
        with patch.object(httpx.AsyncClient, "__aenter__", side_effect=httpx.ConnectError("no")):
            result = await p.health_check()
            assert result is False

    @pytest.mark.asyncio
    async def test_health_check_anthropic(self):
        p = LLMProvider(provider="anthropic", api_key="key", model_name="claude-3")
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch.object(httpx.AsyncClient, "__aenter__", return_value=MagicMock()) as mock_enter:
            mock_client = MagicMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_enter.return_value = mock_client

            result = await p.health_check()
            assert result is True
            mock_client.get.assert_called_once()
            call_args = mock_client.get.call_args
            assert "anthropic.com" in call_args[0][0]


class TestLLMProviderChatStream:
    @pytest.mark.asyncio
    async def test_chat_stream_basic(self):
        p = LLMProvider()
        sse_lines = [
            'data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":""}]}',
            'data: {"choices":[{"delta":{"content":" world"},"finish_reason":""}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
            'data: [DONE]',
        ]

        async def mock_aiter_lines():
            for line in sse_lines:
                yield line

        class MockStreamResponse:
            def raise_for_status(self):
                pass
            def aiter_lines(self):
                return mock_aiter_lines()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            def stream(self, *args, **kwargs):
                return MockStreamResponse()

        with patch("pc_assistant.llm_provider.httpx.AsyncClient", return_value=MockClient()):
            chunks = []
            async for chunk in p.chat_stream([{"role": "user", "content": "hi"}]):
                chunks.append(chunk)
            assert any(c.delta_content for c in chunks)

    @pytest.mark.asyncio
    async def test_chat_stream_with_tool_calls(self):
        p = LLMProvider()
        sse_lines = [
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"test","arguments":""}}]},"finish_reason":""}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"key\\": \\"val\\"}"}}]},"finish_reason":""}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
            'data: [DONE]',
        ]

        async def mock_aiter_lines():
            for line in sse_lines:
                yield line

        class MockStreamResponse:
            def raise_for_status(self):
                pass
            def aiter_lines(self):
                return mock_aiter_lines()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            def stream(self, *args, **kwargs):
                return MockStreamResponse()

        with patch("pc_assistant.llm_provider.httpx.AsyncClient", return_value=MockClient()):
            chunks = []
            async for chunk in p.chat_stream([{"role": "user", "content": "do it"}]):
                chunks.append(chunk)
            final_chunks = [c for c in chunks if c.finish_reason == "tool_calls"]
            assert len(final_chunks) >= 1
            assert len(final_chunks[0].delta_tool_calls) >= 1

    @pytest.mark.asyncio
    async def test_chat_stream_error(self):
        p = LLMProvider()
        with patch.object(httpx.AsyncClient, "__aenter__", side_effect=httpx.ConnectError("fail")):
            chunks = []
            async for chunk in p.chat_stream([{"role": "user", "content": "hi"}]):
                chunks.append(chunk)
            assert len(chunks) >= 1
            assert chunks[0].finish_reason == "error"

    @pytest.mark.asyncio
    async def test_chat_stream_anthropic_fallback(self):
        p = LLMProvider(provider="anthropic", api_key="key", model_name="claude-3")

        # Simulate Anthropic SSE streaming events
        sse_events = [
            'event: message_start',
            'data: {"type": "message_start", "message": {"id": "msg_1", "content": [], "usage": {"input_tokens": 10}}}',
            '',
            'event: content_block_start',
            'data: {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}',
            '',
            'event: content_block_delta',
            'data: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello from Claude!"}}',
            '',
            'event: content_block_stop',
            'data: {"type": "content_block_stop", "index": 0}',
            '',
            'event: message_delta',
            'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 5}}',
            '',
            'event: message_stop',
            'data: {"type": "message_stop"}',
            '',
        ]

        class MockStreamResponse:
            def raise_for_status(self):
                pass
            async def aiter_lines(self):
                for line in sse_events:
                    yield line
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            def stream(self, method, url, **kwargs):
                return MockStreamResponse()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        with patch("httpx.AsyncClient", return_value=MockClient()):
            chunks = []
            async for chunk in p.chat_stream([{"role": "user", "content": "hi"}]):
                chunks.append(chunk)
            assert len(chunks) == 2
            assert chunks[0].delta_content == "Hello from Claude!"
            assert chunks[1].finish_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_chat_stream_anthropic_with_tools(self):
        p = LLMProvider(provider="anthropic", api_key="key", model_name="claude-3")

        sse_events = [
            'event: message_start',
            'data: {"type": "message_start", "message": {"id": "msg_1", "content": [], "usage": {"input_tokens": 10}}}',
            '',
            'event: content_block_start',
            'data: {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}',
            '',
            'event: content_block_stop',
            'data: {"type": "content_block_stop", "index": 0}',
            '',
            'event: content_block_start',
            'data: {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "test", "input": {}}}',
            '',
            'event: content_block_delta',
            'data: {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\\"x\\": 1}"}}',
            '',
            'event: content_block_stop',
            'data: {"type": "content_block_stop", "index": 1}',
            '',
            'event: message_delta',
            'data: {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 5}}',
            '',
            'event: message_stop',
            'data: {"type": "message_stop"}',
            '',
        ]

        class MockStreamResponse:
            def raise_for_status(self):
                pass
            async def aiter_lines(self):
                for line in sse_events:
                    yield line
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            def stream(self, method, url, **kwargs):
                return MockStreamResponse()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        with patch("httpx.AsyncClient", return_value=MockClient()):
            chunks = []
            async for chunk in p.chat_stream(
                [{"role": "user", "content": "do it"}],
                tools=[{"type": "function", "function": {"name": "test", "parameters": {}}}],
            ):
                chunks.append(chunk)
            # Expect: text empty + tool call delta + message_stop
            tool_call_chunks = [c for c in chunks if c.delta_tool_calls]
            assert len(tool_call_chunks) >= 1
            assert tool_call_chunks[0].delta_tool_calls[0]["function"]["name"] == "test"
            assert tool_call_chunks[0].delta_tool_calls[0]["function"]["arguments"] == {"x": 1}
            stop_chunks = [c for c in chunks if c.finish_reason]
            assert len(stop_chunks) >= 1
            assert stop_chunks[0].finish_reason == "tool_use"

    @pytest.mark.asyncio
    async def test_chat_stream_with_usage(self):
        p = LLMProvider()
        sse_lines = [
            'data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":""}]}',
            'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}',
            'data: [DONE]',
        ]

        async def mock_aiter_lines():
            for line in sse_lines:
                yield line

        class MockStreamResponse:
            def raise_for_status(self):
                pass
            def aiter_lines(self):
                return mock_aiter_lines()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            def stream(self, *args, **kwargs):
                return MockStreamResponse()

        with patch("pc_assistant.llm_provider.httpx.AsyncClient", return_value=MockClient()):
            chunks = []
            async for chunk in p.chat_stream([{"role": "user", "content": "hi"}]):
                chunks.append(chunk)
            final_chunks = [c for c in chunks if c.usage]
            assert len(final_chunks) >= 1
            assert final_chunks[0].usage.get("prompt_tokens") == 10
            assert final_chunks[0].usage.get("completion_tokens") == 5


class TestLLMProviderCancel:
    @pytest.mark.asyncio
    async def test_cancel_aborts_stalled_stream(self):
        """Ctrl+C must interrupt a stream even when the server stalls
        (no new SSE chunks arriving)."""
        import asyncio
        import json as _json

        async def handle(reader, writer):
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n")
            await writer.drain()
            chunk = {"choices": [{"delta": {"content": "hi"}}]}
            writer.write(("data: " + _json.dumps(chunk) + "\n\n").encode())
            await writer.drain()
            await asyncio.sleep(30)  # stall: no more chunks
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            p = LLMProvider(server_url=f"http://127.0.0.1:{port}", model_name="test", provider="llamacpp")
            gen = p.chat_stream([{"role": "user", "content": "hello"}])
            first = await gen.__anext__()
            assert first.delta_content == "hi"

            # The next chunk blocks until the server sends more data.
            blocked = asyncio.get_running_loop().create_task(gen.__anext__())
            await asyncio.sleep(0.2)
            p.cancel()  # what the UI's Ctrl+C calls
            try:
                await asyncio.wait_for(blocked, timeout=3.0)
                raise AssertionError("stream yielded an unexpected value after cancel")
            except StopAsyncIteration:
                pass  # stream ended promptly after cancel — expected
            except asyncio.TimeoutError:
                raise AssertionError("stream did not unblock within 3s after cancel")
        finally:
            server.close()
