from __future__ import annotations

import asyncio

import pytest

from pc_assistant.agent_runtime.contracts import RunEvent, RuntimeEventPayload
from pc_assistant.channels.feishu import (
    FeishuChannel,
    _principal_for_log,
    _render_card_markdown,
)
from pc_assistant.config import AppConfig
from pc_assistant.service.core_api import ConfirmationRequestedMessage


class _CoreClient:
    def __init__(self) -> None:
        self.created = 0
        self.runs = []
        self.is_connected = True

    async def create_session(self) -> str:
        self.created += 1
        return f"session-{self.created}"

    async def run(self, session, text, attachments):
        self.runs.append((session, text, attachments))
        yield RunEvent(
            run_id="run-a",
            event_seq=1,
            event_type="content_delta",
            payload=RuntimeEventPayload(content="完成"),
        )
        yield RunEvent(
            run_id="run-a",
            event_seq=2,
            event_type="completed",
            payload=RuntimeEventPayload(),
        )


def _config(tmp_path) -> AppConfig:
    return AppConfig(
        fallback_enabled=False,
        runtime_root=str(tmp_path),
        feishu_enabled=True,
        feishu_app_id="app-id",
        feishu_app_secret="app-secret",
    )


@pytest.mark.asyncio
async def test_feishu_routes_text_through_core_client(tmp_path) -> None:
    channel = FeishuChannel(_config(tmp_path))
    client = _CoreClient()
    channel._clients["ou-user"] = client
    sent_text = []
    sent_cards = []
    channel._send_text = lambda recipient, text: sent_text.append((recipient, text)) or True
    channel._send_card = (
        lambda recipient, text, *args: sent_cards.append((recipient, text)) or True
    )

    await channel._handle_text("ou-user", "你好")

    assert client.runs == [("session-1", "你好", ())]
    assert sent_text[-1] == ("ou-user", "⏳ 正在处理...")
    assert sent_cards == [("ou-user", "完成")]
    assert channel._sessions == {"ou-user": "session-1"}
    assert (tmp_path / "data" / "feishu_sessions.json").is_file()


@pytest.mark.asyncio
async def test_feishu_confirmation_round_trip_stays_in_channel(tmp_path) -> None:
    channel = FeishuChannel(_config(tmp_path))
    channel._clients["ou-user"] = _CoreClient()
    channel._session_users["session-a"] = "ou-user"
    sent = []
    channel._send_text = lambda recipient, text: sent.append((recipient, text)) or True
    channel._send_card = lambda recipient, text, *args: sent.append((recipient, text)) or True
    request = ConfirmationRequestedMessage(
        request_id="confirmation-request",
        confirmation_id="confirmation-a",
        session_handle="session-a",
        tool_name="mouse",
        arguments={"action": "click"},
        reason="state-changing desktop action",
    )

    pending = asyncio.create_task(channel._confirm_tool("ou-user", request))
    await asyncio.sleep(0)
    await channel._handle_text("ou-user", "确认")

    assert await pending is True
    assert any("mouse" in text for _recipient, text in sent)
    assert sent[-1] == ("ou-user", "✅ 已批准执行")


def test_feishu_principal_log_identifier_is_not_reversible() -> None:
    identifier = _principal_for_log("ou-sensitive-user")

    assert identifier == _principal_for_log("ou-sensitive-user")
    assert len(identifier) == 10
    assert "ou-sensitive-user" not in identifier


def test_feishu_card_replaces_core_artifact_image_reference() -> None:
    rendered = _render_card_markdown(
        "操作完成\n\n![屏幕截图](https://api.artifact.local/artifact-a)"
    )

    assert rendered == "操作完成\n\n🖼️ 屏幕截图（见附件）"
    assert "api.artifact.local" not in rendered
