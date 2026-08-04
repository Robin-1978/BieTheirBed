from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pc_assistant.agent import AgentEvent
from pc_assistant.channels.feishu import FeishuChannel
from pc_assistant.model_adapter.types import ImageAttachment


class _ImageAgent:
    async def run(self, *args, **kwargs):
        yield AgentEvent(
            type="tool_result",
            tool_name="screen",
            tool_result={
                "success": True,
                "path": "/tmp/capture.png",
                "artifact": {
                    "kind": "image",
                    "path": "/tmp/capture.png",
                    "media_type": "image/png",
                    "temporary": True,
                },
            },
        )
        yield AgentEvent(type="final_answer", content="截图已发送")


class _PlainPathAgent:
    async def run(self, *args, **kwargs):
        yield AgentEvent(
            type="tool_result",
            tool_name="filesystem",
            tool_result={"path": "/tmp/not-an-image.txt"},
        )
        yield AgentEvent(type="final_answer", content="done")


class _TwoImageAgent:
    async def run(self, *args, **kwargs):
        yield AgentEvent(
            type="tool_result",
            tool_name="window",
            tool_result={
                "artifact": {"kind": "image", "path": "/tmp/window.png"},
            },
        )
        yield AgentEvent(
            type="tool_result",
            tool_name="screen",
            tool_result={
                "artifact": {"kind": "image", "path": "/tmp/grid.png"},
                "grid": {"enabled": True},
            },
        )
        yield AgentEvent(type="final_answer", content="done")


class _AttachmentAgent:
    def __init__(self):
        self.stored = []

    def store_attachment(self, session_id, attachment):
        self.stored.append((session_id, attachment))
        return {"attachment_id": "attachment-1"}


@pytest.mark.asyncio
async def test_feishu_sends_declared_image_artifact():
    channel = FeishuChannel()
    channel._agent = _ImageAgent()
    channel._send_image = MagicMock(return_value=True)
    channel._send_card = MagicMock(return_value=True)

    await channel._process_with_agent_locked("ou-user", "截屏发我")

    channel._send_image.assert_called_once_with("ou-user", "/tmp/capture.png")


@pytest.mark.asyncio
async def test_feishu_does_not_send_arbitrary_tool_paths_as_images():
    channel = FeishuChannel()
    channel._agent = _PlainPathAgent()
    channel._send_image = MagicMock(return_value=True)
    channel._send_card = MagicMock(return_value=True)

    await channel._process_with_agent_locked("ou-user", "read it")

    channel._send_image.assert_not_called()


def test_feishu_inbox_uses_runtime_attachment_directory(tmp_path):
    channel = FeishuChannel(runtime_root=str(tmp_path))
    assert channel._inbox_dir == tmp_path / "attachments" / "feishu-inbox"


def test_feishu_download_image_uses_installed_sdk_contract(tmp_path):
    from PIL import Image

    channel = FeishuChannel(runtime_root=str(tmp_path))
    encoded = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(encoded, format="PNG")
    response = MagicMock()
    response.success.return_value = True
    response.file = io.BytesIO(encoded.getvalue())
    client = MagicMock()
    client.im.v1.message_resource.get.return_value = response
    channel._get_lark_client = MagicMock(return_value=client)

    path = channel._download_image("img-key", "message-id")

    assert (tmp_path / "attachments" / "feishu-inbox") in Path(path).parents
    assert Path(path).suffix == ".png"
    assert Path(path).read_bytes() == encoded.getvalue()
    client.im.v1.message_resource.get.assert_called_once()
    request = client.im.v1.message_resource.get.call_args.args[0]
    assert request.message_id == "message-id"
    assert request.file_key == "img-key"
    assert request.type == "image"


def test_feishu_reaction_is_removed_even_when_agent_is_not_ready():
    channel = FeishuChannel()
    channel._send_text = MagicMock(return_value=True)
    channel._remove_reaction = MagicMock()

    channel._handle_message(
        "ou-user",
        "hello",
        msg_id="message-id",
        reaction_id="reaction-id",
    )

    channel._remove_reaction.assert_called_once_with("message-id", "reaction-id")


@pytest.mark.asyncio
async def test_feishu_delivers_at_most_one_non_grid_screenshot():
    channel = FeishuChannel()
    channel._agent = _TwoImageAgent()
    channel._send_image = MagicMock(return_value=True)
    channel._send_card = MagicMock(return_value=True)

    await channel._process_with_agent_locked("ou-user", "截个图发我")

    channel._send_image.assert_called_once_with("ou-user", "/tmp/window.png")


@pytest.mark.asyncio
async def test_feishu_does_not_send_internal_screenshot_for_input_image():
    channel = FeishuChannel()
    channel._agent = _ImageAgent()
    channel._send_image = MagicMock(return_value=True)
    channel._send_card = MagicMock(return_value=True)

    await channel._process_with_agent_locked(
        "ou-user",
        "请看这张图片并描述/分析它的内容。",
        attachments=[object()],
    )

    channel._send_image.assert_not_called()


def test_feishu_image_waits_for_user_question(tmp_path):
    channel = FeishuChannel(runtime_root=str(tmp_path))
    channel._agent = _AttachmentAgent()
    channel._download_image = MagicMock(return_value=str(tmp_path / "attachments" / "image.png"))
    channel._add_reaction = MagicMock(return_value="reaction-id")
    channel._remove_reaction = MagicMock()
    channel._send_text = MagicMock(return_value=True)

    assert channel._accept_image_message("ou-user", "image-message", "image-key") is True

    channel._send_text.assert_called_once()
    assert "请直接发送问题" in channel._send_text.call_args.args[1]
    attachments = channel._attachments_for_text("ou-user")
    assert attachments == [ImageAttachment.from_ref("attachment-1", caption="feishu image")]
    assert channel._attachments_for_text("ou-user") is None


def test_feishu_duplicate_image_message_is_acknowledged_once(tmp_path):
    channel = FeishuChannel(runtime_root=str(tmp_path))
    channel._agent = _AttachmentAgent()
    channel._download_image = MagicMock(return_value=str(tmp_path / "attachments" / "image.png"))
    channel._add_reaction = MagicMock(return_value="reaction-id")
    channel._remove_reaction = MagicMock()
    channel._send_text = MagicMock(return_value=True)

    assert channel._accept_image_message("ou-user", "same-message", "image-key") is True
    assert channel._accept_image_message("ou-user", "same-message", "image-key") is False

    channel._download_image.assert_called_once()
    channel._send_text.assert_called_once()


def test_feishu_reply_targets_exact_image():
    channel = FeishuChannel()
    channel._remember_image_ref("ou-user", "image-a", "attachment-a")
    channel._remember_image_ref("ou-user", "image-b", "attachment-b")

    attachments = channel._attachments_for_text("ou-user", parent_id="image-a")

    assert attachments == [ImageAttachment.from_ref("attachment-a", caption="feishu image")]


def test_feishu_reply_to_agent_answer_keeps_active_image_context():
    channel = FeishuChannel()
    channel._remember_image_ref("ou-user", "image-a", "attachment-a")
    channel._attachments_for_text("ou-user")

    attachments = channel._attachments_for_text("ou-user", parent_id="bot-answer")

    assert attachments == [ImageAttachment.from_ref("attachment-a", caption="feishu image")]
