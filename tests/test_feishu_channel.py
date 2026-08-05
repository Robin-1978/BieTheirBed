from __future__ import annotations

import io
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pc_assistant.agent import AgentEvent
from pc_assistant.channels.feishu import (
    FeishuChannel,
    _principal_for_log,
    render_feishu_markdown,
)
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


class _ArtifactAgent:
    def __init__(self):
        self.delivered = []

    async def run(self, *args, **kwargs):
        yield AgentEvent(
            type="artifact",
            tool_name="screenshot",
            artifact={
                "artifact_id": "artifact-1",
                "kind": "image",
                "name": "capture.png",
                "media_type": "image/png",
                "size": 123,
                "visibility": "user",
            },
        )
        yield AgentEvent(type="final_answer", content="截图已生成")

    def resolve_artifact(self, session_id, artifact_id):
        assert session_id == "feishu:ou-user"
        assert artifact_id == "artifact-1"
        return {
            "artifact_id": artifact_id,
            "path": "/tmp/capture.png",
            "name": "capture.png",
            "media_type": "image/png",
        }

    def mark_artifact_delivered(self, session_id, artifact_id):
        self.delivered.append((session_id, artifact_id))


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
            type="artifact",
            artifact={
                "artifact_id": "window",
                "kind": "image",
                "name": "window.png",
                "media_type": "image/png",
                "size": 123,
            },
        )
        yield AgentEvent(
            type="artifact",
            artifact={
                "artifact_id": "report",
                "kind": "file",
                "name": "report.pdf",
                "media_type": "application/pdf",
                "size": 456,
            },
        )
        yield AgentEvent(type="final_answer", content="done")

    def resolve_artifact(self, session_id, artifact_id):
        if artifact_id == "window":
            return {"path": "/tmp/window.png", "name": "window.png", "media_type": "image/png"}
        return {"path": "/tmp/report.pdf", "name": "report.pdf", "media_type": "application/pdf"}


class _AttachmentAgent:
    def __init__(self):
        self.stored = []

    def store_artifact(self, session_id, attachment):
        self.stored.append((session_id, attachment))
        return {"artifact_id": "attachment-1"}


@pytest.mark.asyncio
async def test_feishu_sends_declared_image_artifact():
    channel = FeishuChannel()
    channel._agent = _ArtifactAgent()
    channel._send_image = MagicMock(return_value=True)
    channel._send_card = MagicMock(return_value=True)

    await channel._process_with_agent_locked("ou-user", "你截一下屏幕")

    channel._send_image.assert_called_once_with("ou-user", "/tmp/capture.png")
    assert channel._agent.delivered == [("feishu:ou-user", "artifact-1")]


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


def test_feishu_send_file_uses_installed_sdk_contract(tmp_path, caplog):
    source = tmp_path / "report.txt"
    source.write_text("hello", encoding="utf-8")
    upload_response = MagicMock()
    upload_response.success.return_value = True
    upload_response.data.file_key = "file-key"
    message_response = MagicMock(code=0)
    client = MagicMock()
    client.im.v1.file.create.return_value = upload_response
    client.im.v1.message.create.return_value = message_response
    channel = FeishuChannel(runtime_root=str(tmp_path))
    channel._get_lark_client = MagicMock(return_value=client)

    with caplog.at_level(logging.INFO, logger="pc_assistant.channels.feishu"):
        assert channel._send_file("ou-user", str(source), "renamed.txt") is True

    upload_request = client.im.v1.file.create.call_args.args[0]
    assert upload_request.request_body.file_type == "stream"
    assert upload_request.request_body.file_name == "renamed.txt"
    message_request = client.im.v1.message.create.call_args.args[0]
    assert message_request.receive_id_type == "open_id"
    assert message_request.request_body.receive_id == "ou-user"
    assert message_request.request_body.msg_type == "file"
    assert message_request.request_body.content == '{"file_key": "file-key"}'
    assert "ou-user" not in caplog.text
    assert _principal_for_log("ou-user") in caplog.text
    assert "renamed.txt" in caplog.text


def test_feishu_principal_log_id_is_stable_and_non_reversible():
    principal = _principal_for_log("ou-user-sensitive")
    assert principal == _principal_for_log("ou-user-sensitive")
    assert len(principal) == 10
    assert "ou-user-sensitive" not in principal


def test_feishu_markdown_adapts_headings_tables_and_code():
    source = """# 标题

| 项目 | 状态 |
| --- | --- |
| 服务 | 正常 |

```bash
pc-assistant --status
```"""

    rendered = render_feishu_markdown(source)

    assert "# 标题" not in rendered
    assert "**标题**" in rendered
    assert "| 项目 | 状态 |" in rendered
    assert "| 服务 | 正常 |" in rendered
    assert "`pc-assistant --status`" in rendered


def test_response_card_uses_json20_markdown_for_tables_and_inline_formatting():
    channel = FeishuChannel()
    card = channel._build_response_card(
        "# 标题\n\n| **A** | B |\n|---|---|\n| **1** | [`two`](https://example.com) |",
        [],
        False,
    )
    assert card["schema"] == "2.0"
    elements = card["body"]["elements"]
    assert elements[0]["tag"] == "markdown"
    content = elements[0]["content"]
    assert "# 标题" not in content
    assert "**标题**" in content
    assert len(elements) == 1
    assert "| **A** | B |" in content
    assert "| **1** | [`two`](https://example.com) |" in content


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


def test_feishu_unlock_is_handled_before_agent_dispatch():
    channel = FeishuChannel()
    channel._agent_loop = object()
    channel._unlock_broker.verify_and_unlock = MagicMock(
        return_value=(True, "Desktop unlock requested")
    )
    channel._send_text = MagicMock(return_value=True)
    channel._process_with_agent = MagicMock()

    channel._handle_message("ou-user", "/unlock 123456")

    channel._unlock_broker.verify_and_unlock.assert_called_once_with(
        "ou-user", "123456"
    )
    channel._send_text.assert_called_once_with(
        "ou-user", "✅ Desktop unlock requested"
    )
    channel._process_with_agent.assert_not_called()


def test_feishu_text_confirmation_does_not_hold_pending_lock():
    """A text reply must reach the confirmation handler without deadlocking."""
    channel = FeishuChannel()
    channel._agent_loop = object()
    channel._pending_confirm["ou-user"] = {
        "code": "1234",
        "fn": lambda: None,
        "ts": 0,
    }
    channel._handle_confirm = MagicMock()
    channel._remove_reaction = MagicMock()

    channel._handle_message("ou-user", "确认 1234")

    channel._handle_confirm.assert_called_once()
    channel._remove_reaction.assert_called_once()


@pytest.mark.asyncio
async def test_feishu_delivers_each_explicit_core_artifact():
    channel = FeishuChannel()
    channel._agent = _TwoImageAgent()
    channel._send_image = MagicMock(return_value=True)
    channel._send_file = MagicMock(return_value=True)
    channel._send_card = MagicMock(return_value=True)

    await channel._process_with_agent_locked("ou-user", "截个图发我")

    channel._send_image.assert_called_once_with("ou-user", "/tmp/window.png")
    channel._send_file.assert_called_once_with("ou-user", "/tmp/report.pdf", "report.pdf")


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
