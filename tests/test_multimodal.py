"""Layer 1 (multimodal pipeline) tests: content IR, vision preprocess,
conversation blocks, token estimation, agent attachment handling."""
from __future__ import annotations

import pytest

from pc_assistant.agent import Agent, AgentEvent
from pc_assistant.artifacts import ArtifactStore
from pc_assistant.config import AppConfig
from pc_assistant.context.conversation import ConversationManager
from pc_assistant.llm_provider import StreamChunk
from pc_assistant.model_adapter.content import (
    build_image_block,
    has_image,
    text_block,
    text_content,
    to_anthropic_content,
    to_openai_content,
)
from pc_assistant.model_adapter.types import ImageAttachment
from pc_assistant.tools.base import ToolBase
from pc_assistant.vision.preprocess import estimate_image_tokens, image_block_from_file

DATA_URL = "data:image/jpeg;base64,AAAA"
VALID_IMAGE_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)


# ── Content IR + provider serialization ────────────────────────────────


class TestContentBlocks:
    def test_text_block(self):
        assert text_block("hi") == {"type": "text", "text": "hi"}

    def test_build_image_block(self):
        block = build_image_block(DATA_URL, "image/jpeg")
        assert block["type"] == "image"
        assert block["image_url"] == DATA_URL
        assert block["media_type"] == "image/jpeg"

    def test_text_content_plain_string(self):
        assert text_content("hello") == "hello"

    def test_text_content_blocks(self):
        content = [text_block("a"), build_image_block(DATA_URL), text_block("b")]
        assert text_content(content) == "ab"

    def test_has_image(self):
        assert has_image([text_block("x"), build_image_block(DATA_URL)])
        assert not has_image([text_block("x")])
        assert not has_image("plain")


class TestOpenAISerialization:
    def test_str_passthrough(self):
        assert to_openai_content("hi") == "hi"

    def test_image_block_mapping(self):
        out = to_openai_content([text_block("hi"), build_image_block(DATA_URL, "image/jpeg")])
        assert out == [
            {"type": "text", "text": "hi"},
            {"type": "image_url", "image_url": {"url": DATA_URL}},
        ]


class TestAnthropicSerialization:
    def test_str_passthrough(self):
        assert to_anthropic_content("hi") == "hi"

    def test_image_block_mapping(self):
        out = to_anthropic_content([text_block("hi"), build_image_block(DATA_URL, "image/jpeg")])
        assert out == [
            {"type": "text", "text": "hi"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAAA"}},
        ]

    def test_empty_list(self):
        assert to_anthropic_content([]) == ""


# ── Vision preprocess ──────────────────────────────────────────────────


class TestVisionPreprocess:
    def test_estimate_image_tokens(self):
        assert 0 < estimate_image_tokens(224, 224)
        assert estimate_image_tokens(0, 0) == 0

    def test_image_block_from_file(self, tmp_path):
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("Pillow not installed")
        p = tmp_path / "img.png"
        Image.new("RGB", (64, 64), color="red").save(p)
        block = image_block_from_file(p, max_side=128)
        assert block is not None
        assert block["type"] == "image"
        assert block["image_url"].startswith("data:image/png;base64,")

    def test_image_block_from_missing_file(self, tmp_path):
        assert image_block_from_file(str(tmp_path / "nope.png")) is None


# ── Conversation blocks ────────────────────────────────────────────────


class TestConversationBlocks:
    def test_add_user_with_reference_blocks(self):
        conv = ConversationManager()
        ref = {"type": "image_ref", "artifact_id": "img-1", "media_type": "image/jpeg"}
        msg = conv.add_user_with_blocks("look at this", [ref])
        assert msg.role == "user"
        assert msg.content[0]["type"] == "text"
        assert msg.content[1]["type"] == "image_ref"

    def test_add_user_with_blocks_no_blocks(self):
        conv = ConversationManager()
        msg = conv.add_user_with_blocks("plain", None)
        assert msg.content == "plain"

    def test_add_tool_result_reference_blocks(self):
        conv = ConversationManager()
        ref = {"type": "image_ref", "artifact_id": "img-1", "media_type": "image/jpeg"}
        msg = conv.add_tool_result_blocks("tc-1", [ref], tool_name="system")
        assert msg.role == "tool"
        assert isinstance(msg.content, list)
        assert msg.content[0]["type"] == "image_ref"

    def test_history_preserves_references_without_base64(self):
        conv = ConversationManager()
        conv.add_user_with_blocks("hi", [{"type": "image_ref", "artifact_id": "img-1"}])
        msgs = conv.get_messages_for_llm_raw()
        assert isinstance(msgs[0]["content"], list)
        assert msgs[0]["content"][-1]["type"] == "image_ref"
        assert "base64" not in str(msgs)

    def test_history_rejects_provider_image_payload(self):
        conv = ConversationManager()
        with pytest.raises(ValueError, match="image"):
            conv.add_user_with_blocks("hi", [build_image_block(DATA_URL)])


# ── Token estimation ───────────────────────────────────────────────────


class TestTokenEstimateImages:
    def test_messages_tokens_counts_images(self):
        from pc_assistant.context.token_estimate import TokenEstimator

        est = TokenEstimator("default")
        text_tokens = est.messages_tokens([{"role": "user", "content": "hello"}])
        img_tokens = est.messages_tokens([
            {"role": "user", "content": [text_block("hello"), {"type": "image", "width": 224, "height": 224}]},
        ])
        assert img_tokens > text_tokens


# ── ProviderProfile supports_vision ────────────────────────────────────


class TestProfilesVision:
    def test_default_llamacpp_vision(self):
        from pc_assistant.model_adapter.profiles import resolve_profile

        assert resolve_profile("llamacpp").supports_vision is True

    def test_override_disable_vision(self):
        from pc_assistant.model_adapter.profiles import resolve_profile

        assert resolve_profile("openai", supports_vision=False).supports_vision is False

    def test_capability_rejects_unsupported_mime(self):
        from pc_assistant.model_adapter.profiles import resolve_profile

        capability = resolve_profile("openai").vision
        error = capability.validate([{
            "role": "user",
            "content": [{"type": "image", "media_type": "image/gif", "image_url": DATA_URL}],
        }])
        assert "MIME" in error

    def test_capability_exposes_limits(self):
        from pc_assistant.model_adapter.profiles import resolve_profile

        capability = resolve_profile("anthropic").vision
        assert capability.enabled
        assert capability.max_images > 0
        assert "tool" in capability.canonical_roles


class TestProviderRoleAwareImages:
    def test_openai_tool_image_becomes_tool_result_plus_user_observation(self):
        from pc_assistant.model_adapter.parsers.openai import build_chat_payload

        payload = build_chat_payload("m", [{
            "role": "tool",
            "tool_call_id": "call-1",
            "content": [text_block("captured"), build_image_block(DATA_URL)],
        }])
        assert payload["messages"][0]["role"] == "tool"
        assert payload["messages"][1]["role"] == "user"
        assert payload["messages"][1]["content"][-1]["type"] == "image_url"

    def test_anthropic_tool_image_uses_tool_result_block(self):
        from pc_assistant.model_adapter.parsers.anthropic import build_anthropic_payload

        payload = build_anthropic_payload("m", [{
            "role": "tool",
            "tool_call_id": "call-1",
            "content": [build_image_block(DATA_URL)],
        }])
        message = payload["messages"][0]
        assert message["role"] == "user"
        assert message["content"][0]["type"] == "tool_result"
        assert message["content"][0]["tool_use_id"] == "call-1"


# ── Agent attachments ──────────────────────────────────────────────────


def _capture_stream(captured: list):
    captured_blocks: list = []

    async def _stream(*args, **kwargs):
        captured.append(args)
        captured_blocks.append(args[0])
        yield StreamChunk(delta_content="The image shows a red square.", finish_reason="stop")

    return _stream


async def _collect(agent: Agent, text: str, **kwargs) -> list[AgentEvent]:
    events: list[AgentEvent] = []
    async for e in agent.run(text, **kwargs):
        events.append(e)
    return events


class TestAgentAttachments:
    @pytest.mark.asyncio
    async def test_attachment_blocks_reach_llm(self, tmp_path):
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("Pillow not installed")
        img = tmp_path / "att.png"
        Image.new("RGB", (32, 32), color="blue").save(img)

        captured: list = []
        agent = Agent(
            config=AppConfig(),
            artifact_store=ArtifactStore(tmp_path / "attachments"),
        )
        agent._llm.chat_stream = _capture_stream(captured)

        events = await _collect(
            agent,
            "what is in this image?",
            attachments=[ImageAttachment.from_path(str(img), caption="test")],
        )
        assert any(e.type == "final_answer" for e in events)
        last_messages = captured[0][0]
        # The current user turn is the last message and must carry an image block.
        last = last_messages[-1]
        assert last["role"] == "user"
        assert has_image(last["content"])
        assert "Image evidence requirement" not in str(last_messages)
        assert "image_inspect" not in agent.registry.list_tools()
        history = agent._get_state("").conversation.get_messages_for_llm_raw()
        assert "data:image" not in str(history)
        assert "image_ref" in str(history)

    @pytest.mark.asyncio
    async def test_vision_unsupported_errors(self, tmp_path):
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("Pillow not installed")
        img = tmp_path / "att.png"
        Image.new("RGB", (32, 32)).save(img)

        agent = Agent(
            config=AppConfig(supports_vision=False, vision_enabled=False),
            artifact_store=ArtifactStore(tmp_path / "attachments"),
        )
        agent._llm.chat_stream = _capture_stream([])
        events = await _collect(agent, "hi", attachments=[ImageAttachment.from_path(str(img))])
        errors = [e for e in events if e.type == "error"]
        assert errors and "vision" in errors[0].content.lower()

    @pytest.mark.asyncio
    async def test_bad_attachment_errors(self):
        agent = Agent(config=AppConfig())
        agent._llm.chat_stream = _capture_stream([])
        events = await _collect(
            agent,
            "hi",
            attachments=[ImageAttachment.from_path("/nonexistent/nope.png")],
        )
        errors = [e for e in events if e.type == "error"]
        assert errors

    @pytest.mark.asyncio
    async def test_no_attachments_no_blocks(self):
        captured: list = []
        agent = Agent(config=AppConfig())
        agent._llm.chat_stream = _capture_stream(captured)
        await _collect(agent, "hi")
        assert isinstance(captured[0][0][-1]["content"], str)


# ── Agent inline image tool result ─────────────────────────────────────


class _InlineImageTool(ToolBase):
    name = "imgtool"
    description = "Returns an inline image"
    is_side_effecting = False

    async def execute(self, **kwargs):
        return {
            "success": True,
            "path": "/tmp/out.png",
            "image": build_image_block(VALID_IMAGE_DATA_URL, "image/png"),
        }

    def schema(self):
        return {"name": self.name, "parameters": {"type": "object", "properties": {}}}


class TestInlineImageToolResult:
    @pytest.mark.asyncio
    async def test_inline_image_hydrated_for_request_but_stored_as_reference(self, tmp_path):
        agent = Agent(
            config=AppConfig(),
            artifact_store=ArtifactStore(tmp_path / "attachments"),
        )
        agent.register_tool(_InlineImageTool())

        captured: list = []
        calls = {"n": 0}

        async def _stream(*args, **kwargs):
            captured.append(args[0])
            calls["n"] += 1
            if calls["n"] == 1:
                yield StreamChunk(delta_tool_calls=[{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "imgtool", "arguments": "{}"},
                }], finish_reason="")
                yield StreamChunk(finish_reason="tool_calls")
            else:
                yield StreamChunk(delta_content="done", finish_reason="stop")

        agent._llm.chat_stream = _stream
        events = await _collect(agent, "use imgtool")
        assert any(e.type == "tool_result" for e in events)
        assert len(captured) == 2

        # The tool message (in the follow-up call) must contain the image block.
        tool_msgs = [m for m in captured[1] if m["role"] == "tool"]
        assert tool_msgs
        assert has_image(tool_msgs[-1]["content"])
        history = agent.conversation.get_messages_for_llm_raw()
        assert "data:image" not in str(history)
        assert "image_ref" in str(history)
        assert all("data:image" not in str(e.model_dump()) for e in events)


def _make_tool_stream(agent: Agent):  # noqa: ARG001 - kept for clarity
    async def _stream(*args, **kwargs):
        yield StreamChunk(delta_tool_calls=[{
            "id": "call_1",
            "type": "function",
            "function": {"name": "imgtool", "arguments": "{}"},
        }], finish_reason="")
        yield StreamChunk(finish_reason="tool_calls")
    return _stream
