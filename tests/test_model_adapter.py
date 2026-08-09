from __future__ import annotations


from pc_assistant.model_adapter.parsers.anthropic import (
    AnthropicStreamAccumulator,
    build_anthropic_payload,
    convert_tools_to_anthropic,
)
from pc_assistant.model_adapter.parsers.openai import (
    OpenAIStreamAccumulator,
    build_chat_payload,
)
from pc_assistant.model_adapter.profiles import resolve_profile


class TestResolveProfile:
    def test_llamacpp_defaults(self):
        p = resolve_profile("llamacpp", server_url="http://localhost:8080/")
        assert p.server_url == "http://localhost:8080"
        assert p.chat_url == "http://localhost:8080/v1/chat/completions"
        assert p.health_url == "http://localhost:8080/v1/models"
        assert p.cache_prompt is True
        assert p.stream_options is True
        assert p.headers == {}

    def test_openai_no_double_v1(self):
        p = resolve_profile("openai", api_key="sk-test")
        assert p.server_url == "https://api.openai.com/v1"
        assert p.chat_url == "https://api.openai.com/v1/chat/completions"
        assert p.health_url == "https://api.openai.com/v1/models"
        assert p.headers == {"Authorization": "Bearer sk-test"}
        assert p.requires_api_key is True

    def test_anthropic(self):
        p = resolve_profile("anthropic", api_key="sk-ant")
        assert p.server_url == "https://api.anthropic.com"
        assert p.chat_url == "https://api.anthropic.com/v1/messages"
        assert p.health_url == "https://api.anthropic.com/v1/models"
        assert p.anthropic_style is True
        assert p.headers["x-api-key"] == "sk-ant"

    def test_openai_compatible_v1_base_no_double_v1(self):
        p = resolve_profile("openai_compatible", api_base="http://my-server:8000/v1")
        assert p.server_url == "http://my-server:8000/v1"
        assert p.chat_url == "http://my-server:8000/v1/chat/completions"
        assert p.health_url == "http://my-server:8000/v1/models"

    def test_openai_compatible_plain_base(self):
        p = resolve_profile("openai_compatible", server_url="http://fallback:8080")
        assert p.server_url == "http://fallback:8080"
        assert p.chat_url == "http://fallback:8080/v1/chat/completions"

    def test_openai_compatible_explicit_vendor_base_is_authoritative(self):
        p = resolve_profile(
            "openai_compatible",
            api_base="https://ark.example/api/coding/v3",
        )
        assert p.chat_url == "https://ark.example/api/coding/v3/chat/completions"
        assert p.health_url == "https://ark.example/api/coding/v3/models"


class TestAnthropicParser:
    def test_build_payload_system_as_blocks_with_cache_control(self):
        payload = build_anthropic_payload(
            "claude-3",
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
            cache_control={"type": "ephemeral"},
        )
        assert payload["system"] == [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]
        assert payload["messages"] == [{"role": "user", "content": "hi"}]

    def test_build_payload_system_plain_string_without_cache(self):
        payload = build_anthropic_payload("claude-3", [{"role": "system", "content": "sys"}])
        assert payload["system"] == "sys"

    def test_convert_tools_to_anthropic_with_cache_control(self):
        tools = [{
            "name": "web_search",
            "description": "d",
            "inputSchema": {"type": "object", "properties": {}},
        }]
        converted = convert_tools_to_anthropic(tools, {"type": "ephemeral"})
        assert converted[0]["name"] == "web_search"
        assert converted[0]["cache_control"] == {"type": "ephemeral"}

    def test_stream_accumulator_text_and_stop(self):
        acc = AnthropicStreamAccumulator()
        assert acc.process("content_block_start", {"index": 0, "content_block": {"type": "text"}}) == []
        chunks = acc.process("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "hel"}})
        assert chunks[0].delta_content == "hel"
        final = acc.process("message_stop", {})
        assert final[0].finish_reason == ""


class TestOpenAIParser:
    def test_build_chat_payload_flags(self):
        payload = build_chat_payload(
            "qwen", [{"role": "user", "content": "hi"}],
            cache_prompt=True, stream_options=True,
        )
        assert payload["cache_prompt"] is True
        assert payload["stream_options"] == {"include_usage": True}

    def test_build_chat_payload_includes_model_thinking_mode(self):
        payload = build_chat_payload(
            "ark-code-latest",
            [{"role": "user", "content": "hi"}],
            thinking={"type": "enabled"},
        )
        assert payload["thinking"] == {"type": "enabled"}

    def test_stream_accumulator_tool_calls_and_finish(self):
        acc = OpenAIStreamAccumulator()
        acc.process_chunk({
            "choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "web_search", "arguments": '{"q"'}}]}}],
        })
        acc.process_chunk({
            "choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ':"x"}'}}]}}],
        })
        final = acc.finish()
        assert final.delta_tool_calls[0]["function"]["arguments"] == {"q": "x"}
