from __future__ import annotations

import asyncio
import json
import threading

import pytest

from pc_assistant.agent_runtime.contracts import (
    RunEvent,
    RuntimeEventPayload,
    RuntimeStatus,
)
from pc_assistant.channels.feishu import (
    FeishuChannel,
    _StreamingCardState,
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

    async def status(self, _session) -> RuntimeStatus:
        return RuntimeStatus(
            status="ready",
            connected=True,
            details={
                "provider": "volcengine",
                "model": "model-a",
                "prompt_tokens": 1234,
                "completion_tokens": 56,
                "total_tokens": 1290,
                "cached_tokens": 200,
                "turns": 3,
                "model_calls": 4,
                "tool_calls": 2,
                "messages": 9,
                "sessions": 1,
                "available_tools": 12,
            },
        )

    async def run(self, session, text, attachments):
        self.runs.append((session, text, attachments))
        yield RunEvent(
            run_id="run-a",
            event_seq=1,
            event_type="run_started",
            payload=RuntimeEventPayload(),
        )
        yield RunEvent(
            run_id="run-a",
            event_seq=2,
            event_type="reasoning_delta",
            payload=RuntimeEventPayload(content="分析中"),
        )
        yield RunEvent(
            run_id="run-a",
            event_seq=3,
            event_type="tool_call",
            payload=RuntimeEventPayload(
                tool_name="mouse",
                tool_args={"action": "move"},
            ),
        )
        yield RunEvent(
            run_id="run-a",
            event_seq=4,
            event_type="tool_result",
            payload=RuntimeEventPayload(
                tool_name="mouse",
                tool_result={"status": "completed", "output": {"success": True}},
            ),
        )
        yield RunEvent(
            run_id="run-a",
            event_seq=5,
            event_type="content_delta",
            payload=RuntimeEventPayload(content="完成"),
        )
        yield RunEvent(
            run_id="run-a",
            event_seq=6,
            event_type="final_output",
            payload=RuntimeEventPayload(content="完成"),
        )
        yield RunEvent(
            run_id="run-a",
            event_seq=7,
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
    created_cards = []
    updated_cards = []
    reactions = []
    channel._send_text = lambda recipient, text: sent_text.append((recipient, text)) or True
    channel._send_card_returning_id = (
        lambda recipient, card: created_cards.append((recipient, card)) or "card-a"
    )
    channel._update_card = (
        lambda message_id, card: updated_cards.append((message_id, card)) or True
    )
    channel._add_reaction = (
        lambda message_id, emoji: reactions.append(("add", message_id, emoji))
        or "reaction-a"
    )
    channel._remove_reaction = (
        lambda message_id, reaction_id: reactions.append(
            ("remove", message_id, reaction_id)
        )
    )

    await channel._handle_text("ou-user", "你好", "message-a")

    assert client.runs == [("session-1", "你好", ())]
    assert sent_text == []
    assert len(created_cards) == 1
    rendered_updates = json.dumps(updated_cards, ensure_ascii=False)
    assert "分析中" in rendered_updates
    assert "mouse" in rendered_updates
    assert "完成" in rendered_updates
    assert reactions == [
        ("add", "message-a", "Typing"),
        ("remove", "message-a", "reaction-a"),
    ]
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


@pytest.mark.asyncio
async def test_feishu_status_renders_core_usage_without_channel_metrics(
    tmp_path,
) -> None:
    channel = FeishuChannel(_config(tmp_path))
    channel._clients["ou-user"] = _CoreClient()
    channel._sessions["ou-user"] = "session-a"
    cards = []
    channel._send_card = lambda *args: cards.append(args) or True

    await channel._run_text("ou-user", "/status")

    assert len(cards) == 1
    rendered = cards[0][1]
    assert "输入：1,234 tokens" in rendered
    assert "输出：56 tokens" in rendered
    assert "合计：1,290 tokens" in rendered
    assert "工具调用：2" in rendered
    assert cards[0][3] == "状态"


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


def test_feishu_tool_success_uses_plain_check_without_completion_copy() -> None:
    state = _StreamingCardState()
    state.add_tool_call("mouse", {"action": "click"})
    state.add_tool_result(
        "mouse",
        {"status": "completed", "output": {"success": True}},
        blocked=False,
    )

    rendered = json.dumps(state.build_card(), ensure_ascii=False)

    assert "✓ `mouse`" in rendered
    assert "✅" not in rendered
    assert "⚙️" not in rendered
    assert "— 完成" not in rendered
    assert rendered.count("mouse") == 1


def test_feishu_long_card_output_is_split_without_data_loss(tmp_path) -> None:
    channel = FeishuChannel(_config(tmp_path))
    cards = []
    channel._send_card_returning_id = (
        lambda recipient, card: cards.append((recipient, card))
        or f"card-{len(cards)}"
    )
    text = "第一段。\n\n" + ("完整输出内容 " * 1200)

    assert channel._send_card("ou-user", text)

    contents = [
        card["body"]["elements"][0]["content"]
        for _recipient, card in cards
    ]
    assert len(contents) > 1
    assert "".join(contents) == _render_card_markdown(text)


@pytest.mark.asyncio
async def test_feishu_card_io_does_not_block_core_event_consumption(tmp_path) -> None:
    class BurstClient:
        def __init__(self) -> None:
            self.consumed = asyncio.Event()

        async def run(self, _session, _text, _attachments):
            yield RunEvent(
                run_id="run-burst",
                event_seq=1,
                event_type="run_started",
                payload=RuntimeEventPayload(),
            )
            for index in range(2, 1002):
                yield RunEvent(
                    run_id="run-burst",
                    event_seq=index,
                    event_type="reasoning_delta",
                    payload=RuntimeEventPayload(content="x"),
                )
            yield RunEvent(
                run_id="run-burst",
                event_seq=1002,
                event_type="final_output",
                payload=RuntimeEventPayload(content="完成"),
            )
            yield RunEvent(
                run_id="run-burst",
                event_seq=1003,
                event_type="completed",
                payload=RuntimeEventPayload(),
            )
            self.consumed.set()

    channel = FeishuChannel(_config(tmp_path))
    release_card = threading.Event()

    def slow_card(_recipient, _card):
        release_card.wait(timeout=2)
        return "card-a"

    channel._send_card_returning_id = slow_card
    channel._update_card = lambda _message_id, _card: True
    client = BurstClient()
    task = asyncio.create_task(
        channel._stream_core_run("ou-user", client, "session-a", "hello", ())
    )

    await asyncio.wait_for(client.consumed.wait(), timeout=0.5)
    assert not task.done()
    release_card.set()
    await asyncio.wait_for(task, timeout=2)
