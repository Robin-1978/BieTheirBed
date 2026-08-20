"""Multimodal content, provider serialization, and image budgeting tests."""
from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from knoa_platform.model_adapter.content import (
    build_image_block,
    ImageNormalizationError,
    normalize_image_messages,
    text_block,
    to_anthropic_content,
    to_openai_content,
)
from knoa_platform.vision.preprocess import estimate_image_tokens

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

    def test_provider_image_is_resized_and_reencoded_before_transport(self):
        source = io.BytesIO()
        Image.new("RGB", (3200, 2400), "#336699").save(source, format="PNG")
        data_url = "data:image/png;base64," + base64.b64encode(source.getvalue()).decode()

        messages = normalize_image_messages([
            {"role": "user", "content": [build_image_block(data_url, "image/png")]},
        ])

        block = messages[0]["content"][0]
        assert block["media_type"] == "image/jpeg"
        assert block["width"] == 1024
        assert block["height"] == 768
        assert len(base64.b64decode(block["image_url"].split(",", 1)[1])) < 2 * 1024 * 1024

    def test_provider_image_hard_pixel_limit_fails_before_model_call(self):
        source = io.BytesIO()
        Image.new("RGB", (100, 100), "white").save(source, format="JPEG")
        data_url = "data:image/jpeg;base64," + base64.b64encode(source.getvalue()).decode()

        with pytest.raises(ImageNormalizationError, match="pixel"):
            normalize_image_messages(
                [{"role": "user", "content": [build_image_block(data_url)]}],
                max_source_pixels=9_999,
            )

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

# ── Token estimation ───────────────────────────────────────────────────


class TestTokenEstimateImages:
    def test_messages_tokens_counts_images(self):
        from knoa_platform.context.token_estimate import TokenEstimator

        est = TokenEstimator("default")
        text_tokens = est.messages_tokens([{"role": "user", "content": "hello"}])
        img_tokens = est.messages_tokens([
            {"role": "user", "content": [text_block("hello"), {"type": "image", "width": 224, "height": 224}]},
        ])
        assert img_tokens > text_tokens


# ── ProviderProfile supports_vision ────────────────────────────────────


class TestProfilesVision:
    def test_default_llamacpp_vision(self):
        from knoa_platform.model_adapter.profiles import resolve_profile

        # OpenAI-compatible/local endpoints do not advertise capabilities
        # reliably; unknown vision support fails closed and must be explicit.
        assert resolve_profile("llamacpp").supports_vision is False
        assert resolve_profile("llamacpp", supports_vision=True).supports_vision is True

    def test_openai_compatible_unknown_vision_fails_closed(self):
        from knoa_platform.model_adapter.profiles import resolve_profile

        assert resolve_profile("openai_compatible").supports_vision is False
        assert resolve_profile("openai_compatible", supports_vision=True).supports_vision is True

    def test_override_disable_vision(self):
        from knoa_platform.model_adapter.profiles import resolve_profile

        assert resolve_profile("openai", supports_vision=False).supports_vision is False

    def test_capability_rejects_unsupported_mime(self):
        from knoa_platform.model_adapter.profiles import resolve_profile

        capability = resolve_profile("openai").vision
        error = capability.validate([{
            "role": "user",
            "content": [{"type": "image", "media_type": "image/gif", "image_url": DATA_URL}],
        }])
        assert "MIME" in error

    def test_capability_exposes_limits(self):
        from knoa_platform.model_adapter.profiles import resolve_profile

        capability = resolve_profile("anthropic").vision
        assert capability.enabled
        assert capability.max_images > 0
        assert "tool" in capability.canonical_roles


class TestProviderRoleAwareImages:
    def test_openai_tool_image_becomes_tool_result_plus_user_observation(self):
        from knoa_platform.model_adapter.parsers.openai import build_chat_payload

        payload = build_chat_payload("m", [{
            "role": "tool",
            "tool_call_id": "call-1",
            "content": [text_block("captured"), build_image_block(DATA_URL)],
        }])
        assert payload["messages"][0]["role"] == "tool"
        assert payload["messages"][1]["role"] == "user"
        assert payload["messages"][1]["content"][-1]["type"] == "image_url"

    def test_anthropic_tool_image_uses_tool_result_block(self):
        from knoa_platform.model_adapter.parsers.anthropic import build_anthropic_payload

        payload = build_anthropic_payload("m", [{
            "role": "tool",
            "tool_call_id": "call-1",
            "content": [build_image_block(DATA_URL)],
        }])
        message = payload["messages"][0]
        assert message["role"] == "user"
        assert message["content"][0]["type"] == "tool_result"
        assert message["content"][0]["tool_use_id"] == "call-1"
