from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest

from pc_assistant.agent_runtime.contracts import RuntimeStatus
from pc_assistant.channels.feishu import (
    FeishuChannel,
    _ActiveTaskPresentation,
    _StreamingCardState,
    _markdown_table_count,
    _patch_ws_card_dispatch,
    _principal_for_log,
    _render_card_markdown,
    _service_notice,
    _split_text,
)
from pc_assistant.config import AppConfig
from pc_assistant.service.core_api import TaskCancelResultMessage
from pc_assistant.tasks import (
    PrincipalTaskEvent,
    TaskCancelResult,
    TaskEvent,
    TaskEventPayload,
    TaskState,
)


class _CoreClient:
    def __init__(self) -> None:
        self.created = 0
        self.cancelled = 0
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

    async def cancel_active_task(self) -> TaskCancelResultMessage:
        self.cancelled += 1
        return TaskCancelResultMessage(
            request_id="cancel-request",
            result=TaskCancelResult(accepted=True, state=TaskState.RUNNING),
        )

    async def execute_task(self, session, text, attachments):
        self.runs.append((session, text, attachments))
        yield TaskEvent(
            task_id="task-a",
            event_seq=1,
            occurred_at=1.0,
            event_type="task_created",
            payload=TaskEventPayload(),
        )
        yield TaskEvent(
            task_id="task-a",
            event_seq=2,
            occurred_at=2.0,
            event_type="reasoning_delta",
            payload=TaskEventPayload(content="分析中"),
        )
        yield TaskEvent(
            task_id="task-a",
            event_seq=3,
            occurred_at=3.0,
            event_type="tool_call",
            payload=TaskEventPayload(
                tool_call_id="call-mouse",
                tool_name="mouse",
                tool_args={"action": "move"},
            ),
        )
        yield TaskEvent(
            task_id="task-a",
            event_seq=4,
            occurred_at=4.0,
            event_type="tool_result",
            payload=TaskEventPayload(
                tool_call_id="call-mouse",
                tool_name="mouse",
                tool_result={"status": "completed", "output": {"success": True}},
            ),
        )
        yield TaskEvent(
            task_id="task-a",
            event_seq=5,
            occurred_at=5.0,
            event_type="content_delta",
            payload=TaskEventPayload(content="完成"),
        )
        yield TaskEvent(
            task_id="task-a",
            event_seq=6,
            occurred_at=6.0,
            event_type="final_output",
            payload=TaskEventPayload(content="完成"),
        )
        yield TaskEvent(
            task_id="task-a",
            event_seq=7,
            occurred_at=7.0,
            event_type="completed",
            payload=TaskEventPayload(),
        )


def _config(tmp_path) -> AppConfig:
    return AppConfig(
        fallback_enabled=False,
        runtime_root=str(tmp_path),
        feishu_enabled=True,
        feishu_app_id="app-id",
        feishu_app_secret="app-secret",
    )


def _approval_event(
    *,
    task_id: str = "task-a",
    approval_id: str = "confirmation-a",
    call_id: str = "call-mouse",
    tool_name: str = "mouse",
    arguments: dict | None = None,
    reason: str = "state-changing desktop action",
) -> TaskEvent:
    return TaskEvent(
        task_id=task_id,
        event_seq=3,
        occurred_at=3.0,
        event_type="approval_requested",
        payload=TaskEventPayload(
            state=TaskState.WAITING_APPROVAL,
            approval_id=approval_id,
            tool_call_id=call_id,
            tool_name=tool_name,
            tool_args=arguments or {"action": "click"},
            reason=reason,
        ),
    )


def _active_presentation(
    channel: FeishuChannel,
) -> tuple[_ActiveTaskPresentation, _StreamingCardState]:
    state = _StreamingCardState()
    state.add_tool_call(
        "call-mouse",
        "mouse",
        {"action": "click"},
        iteration=1,
    )
    presentation = _ActiveTaskPresentation(
        session_handle="session-a",
        state=state,
        update_requested=asyncio.Event(),
        task_id="task-a",
    )
    channel._active_task_presentations["task-a"] = presentation
    channel._active_session_presentations["session-a"] = presentation
    return presentation, state


@pytest.mark.asyncio
async def test_feishu_start_and_stop_notifications_include_version(
    tmp_path,
) -> None:
    channel = FeishuChannel(_config(tmp_path))
    channel._receive_id = "ou-user"
    channel._get_lark_client = lambda: object()
    channel._run_websocket = lambda: None
    sent = []
    channel._send_text = lambda *args: sent.append(args) or True

    await channel.start()
    await channel.stop()

    assert sent == [
        ("ou-user", _service_notice("已启动")),
        ("ou-user", _service_notice("已停止")),
    ]
    assert "v0.1.1" in sent[0][1]


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
    channel._session_users["session-a"] = "ou-user"
    presentation, state = _active_presentation(channel)
    request = _approval_event()

    pending = asyncio.create_task(channel._confirm_tool("ou-user", request))
    await asyncio.sleep(0)
    resolved = channel._resolve_confirmation(
        "ou-user",
        "confirmation-a",
        True,
    )

    pending_card = state.build_card()
    rendered_pending = json.dumps(pending_card, ensure_ascii=False)
    assert pending_card["header"]["title"]["content"] == "小诺 · 等待确认"
    assert "确认" in rendered_pending
    assert "取消" in rendered_pending
    assert "behaviors" in rendered_pending
    assert presentation.update_requested.is_set()

    assert resolved is not None
    assert await pending is True
    rendered_resolved = json.dumps(state.build_card(), ensure_ascii=False)
    assert "已确认" in rendered_resolved
    assert "behaviors" not in rendered_resolved


@pytest.mark.asyncio
async def test_feishu_confirmation_updates_the_single_streaming_card(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "pc_assistant.channels.feishu._STREAM_PATCH_INTERVAL_SECONDS",
        0,
    )
    channel = FeishuChannel(_config(tmp_path))
    channel._session_users["session-a"] = "ou-user"
    created_cards = []
    updated_cards = []
    channel._send_card_returning_id = (
        lambda recipient, card: created_cards.append((recipient, card))
        or "card-a"
    )
    channel._update_card = (
        lambda message_id, card: updated_cards.append((message_id, card))
        or True
    )

    class ConfirmingClient:
        async def execute_task(self, _session, _text, _attachments):
            yield TaskEvent(
                task_id="task-a",
                event_seq=1,
                occurred_at=1.0,
                event_type="task_created",
                payload=TaskEventPayload(),
            )
            yield TaskEvent(
                task_id="task-a",
                event_seq=2,
                occurred_at=2.0,
                event_type="tool_call",
                payload=TaskEventPayload(
                    tool_call_id="call-mouse",
                    tool_name="mouse",
                    tool_args={"action": "click"},
                    iteration=1,
                ),
            )
            approved = await channel._confirm_tool(
                "ou-user",
                _approval_event(reason="desktop_control:high"),
            )
            assert approved
            yield TaskEvent(
                task_id="task-a",
                event_seq=3,
                occurred_at=3.0,
                event_type="tool_result",
                payload=TaskEventPayload(
                    tool_call_id="call-mouse",
                    tool_name="mouse",
                    tool_result={"status": "completed"},
                    iteration=1,
                ),
            )
            yield TaskEvent(
                task_id="task-a",
                event_seq=4,
                occurred_at=4.0,
                event_type="final_output",
                payload=TaskEventPayload(content="完成", iteration=2),
            )
            yield TaskEvent(
                task_id="task-a",
                event_seq=5,
                occurred_at=5.0,
                event_type="completed",
                payload=TaskEventPayload(),
            )

    run = asyncio.create_task(
        channel._stream_core_task(
            "ou-user",
            ConfirmingClient(),
            "session-a",
            "点击",
            (),
        )
    )
    while "ou-user" not in channel._pending_confirmations:
        await asyncio.sleep(0)
    while not any(
        "behaviors" in json.dumps(card, ensure_ascii=False)
        for _, card in updated_cards
    ):
        await asyncio.sleep(0)

    assert channel._resolve_confirmation(
        "ou-user",
        "confirmation-a",
        True,
        task_id="task-a",
    )
    await run

    assert len(created_cards) == 1
    assert {message_id for message_id, _ in updated_cards} == {"card-a"}
    assert "behaviors" not in json.dumps(updated_cards[-1][1], ensure_ascii=False)


@pytest.mark.asyncio
async def test_feishu_card_callback_confirms_and_replaces_buttons(tmp_path) -> None:
    channel = FeishuChannel(_config(tmp_path))
    channel._session_users["session-a"] = "ou-user"
    _presentation, state = _active_presentation(channel)
    request = _approval_event()
    pending = asyncio.create_task(channel._confirm_tool("ou-user", request))
    await asyncio.sleep(0)
    handler = channel._create_event_handler()
    processor = handler._callback_processor_map["p2.card.action.trigger"]
    from lark_oapi.event.callback.model.p2_card_action_trigger import (
        P2CardActionTrigger,
    )

    response = processor.do(
        P2CardActionTrigger(
            {
                "event": {
                    "operator": {"open_id": "ou-user"},
                    "action": {
                        "value": {
                            "action": "confirm",
                            "task_id": "task-a",
                            "approval_id": "confirmation-a",
                        }
                    },
                }
            }
        )
    )

    assert await pending is True
    assert response.toast.content == "已确认"
    assert getattr(response, "card", None) is None
    rendered = json.dumps(state.build_card(), ensure_ascii=False)
    assert "已确认" in rendered
    assert "behaviors" not in rendered


@pytest.mark.asyncio
async def test_feishu_confirmation_rejects_wrong_user_id_and_double_click(
    tmp_path,
) -> None:
    channel = FeishuChannel(_config(tmp_path))
    channel._session_users["session-a"] = "ou-user"
    _active_presentation(channel)
    request = _approval_event()
    pending = asyncio.create_task(channel._confirm_tool("ou-user", request))
    await asyncio.sleep(0)

    assert channel._resolve_confirmation("ou-other", "confirmation-a", True) is None
    assert channel._resolve_confirmation("ou-user", "wrong-id", True) is None
    assert not pending.done()
    assert channel._resolve_confirmation("ou-user", "confirmation-a", False) is not None
    assert channel._resolve_confirmation("ou-user", "confirmation-a", True) is None
    assert await pending is False


def test_feishu_streaming_card_uses_native_v2_confirmation_buttons() -> None:
    state = _StreamingCardState()
    state.add_tool_call(
        "call-mouse",
        "mouse",
        {"action": "click"},
        iteration=1,
    )
    request = _approval_event()

    assert state.request_confirmation(request)
    card = state.build_card()
    columns = card["body"]["elements"][2]["columns"]
    buttons = [column["elements"][0] for column in columns]

    assert card["schema"] == "2.0"
    assert [button["text"]["content"] for button in buttons] == ["确认", "取消"]
    assert [button["behaviors"][0]["value"]["action"] for button in buttons] == [
        "confirm",
        "cancel",
    ]
    assert all(
        button["behaviors"][0]["value"]["task_id"] == "task-a"
        for button in buttons
    )


@pytest.mark.asyncio
async def test_feishu_stop_bypasses_busy_user_lock(tmp_path) -> None:
    channel = FeishuChannel(_config(tmp_path))
    client = _CoreClient()
    channel._clients["ou-user"] = client
    sent = []
    reactions = []
    channel._send_text = lambda *args: sent.append(args) or True
    channel._add_reaction = lambda *_args: "reaction-a"
    channel._remove_reaction = lambda *args: reactions.append(args)
    lock = channel._user_locks.setdefault("ou-user", asyncio.Lock())
    await lock.acquire()
    try:
        await asyncio.wait_for(
            channel._handle_text("ou-user", "/stop", "message-stop"),
            timeout=0.2,
        )
    finally:
        lock.release()

    assert client.cancelled == 1
    assert sent == [("ou-user", "正在停止当前任务。")]
    assert reactions == [("message-stop", "reaction-a")]


def test_feishu_cancelled_card_is_neutral() -> None:
    state = _StreamingCardState()
    state.append_reasoning("处理中")
    state.set_cancelled()

    rendered = json.dumps(state.build_card(), ensure_ascii=False)

    assert "已停止" in rendered
    assert '"template": "grey"' in rendered
    assert "处理出错" not in rendered


def test_feishu_reasoning_is_muted_but_final_answer_is_not() -> None:
    state = _StreamingCardState()
    state.append_reasoning("先检查状态")

    card = state.build_card(final_chunk="结论")
    reasoning, divider, final = card["body"]["elements"]

    assert reasoning["content"] == "<font color='grey'>› 先检查状态</font>"
    assert divider == {"tag": "hr"}
    assert final["content"] == "结论"
    assert "<font" not in final["content"]


def test_feishu_progress_timeline_preserves_event_order() -> None:
    state = _StreamingCardState()
    state.append_reasoning("先分析", iteration=1)
    state.append_draft("我先查询天气", iteration=1)
    state.add_tool_call(
        "call-weather",
        "weather",
        {"location": "上海"},
        iteration=1,
    )
    state.add_tool_result(
        "call-weather",
        "weather",
        {"status": "completed", "output": {"success": True}},
        blocked=False,
        iteration=1,
    )
    state.append_reasoning("根据结果判断", iteration=2)
    state.append_draft("最终答案", iteration=2)
    state.set_final_output("最终答案", iteration=2)

    timeline, divider, final = state.build_card(
        final_chunk="最终答案"
    )["body"]["elements"]
    rendered = timeline["content"]

    assert rendered.index("› 先分析") < rendered.index("› 我先查询天气")
    assert rendered.index("› 我先查询天气") < rendered.index("✓ `weather`")
    assert rendered.index("✓ `weather`") < rendered.index("› 根据结果判断")
    assert "› 最终答案" not in rendered
    assert divider == {"tag": "hr"}
    assert final["content"] == "最终答案"


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

    assert rendered == "操作完成\n\n图片：屏幕截图（见附件）"
    assert "api.artifact.local" not in rendered


def test_feishu_tool_success_uses_plain_check_without_completion_copy() -> None:
    state = _StreamingCardState()
    state.add_tool_call("call-mouse", "mouse", {"action": "click"})
    state.add_tool_result(
        "call-mouse",
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


def test_feishu_short_output_with_many_tables_is_split_as_markdown() -> None:
    tables = [
        (
            f"### 框架 {index}\n"
            "| 项目 | 详情 |\n"
            "|------|------|\n"
            f"| 名称 | Agent {index} |\n"
        )
        for index in range(1, 7)
    ]
    text = "\n".join(tables)

    assert len(text) < 3500
    chunks = _split_text(text)

    assert len(chunks) == 2
    assert "".join(chunks) == text
    assert all(_markdown_table_count(chunk) <= 3 for chunk in chunks)


def test_feishu_card_failure_retries_smaller_markdown_chunks(tmp_path) -> None:
    channel = FeishuChannel(_config(tmp_path))
    attempted = []
    plain_text = []

    def send(_recipient, card):
        content = card["body"]["elements"][0]["content"]
        attempted.append(content)
        if _markdown_table_count(content) > 1:
            return None
        return f"card-{len(attempted)}"

    channel._send_card_returning_id = send
    channel._send_long_text = lambda *args: plain_text.append(args) or True
    text = (
        "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
        "| C | D |\n|---|---|\n| 3 | 4 |\n"
    )

    assert channel._send_card("ou-user", text)
    assert len(attempted) == 3
    assert [_markdown_table_count(item) for item in attempted] == [2, 1, 1]
    assert plain_text == []


@pytest.mark.asyncio
async def test_feishu_principal_feed_notifies_background_task_and_saves_cursor(
    tmp_path,
) -> None:
    feed_event = PrincipalTaskEvent(
        feed_event_id=9,
        principal_id="principal-a",
        event=TaskEvent(
            task_id="task-background",
            event_seq=5,
            occurred_at=time.time() + 10,
            event_type="completed",
            payload=TaskEventPayload(state=TaskState.COMPLETED),
        ),
    )

    class BackgroundClient:
        is_connected = True

        async def principal_task_events(self, *, after_id=0):
            assert after_id == 0
            yield feed_event
            await asyncio.Event().wait()

        async def get_task(self, task_id):
            assert task_id == "task-background"
            return SimpleNamespace(
                final_summary="后台工作已处理完毕。",
                failure_code="",
            )

    channel = FeishuChannel(_config(tmp_path))
    channel._running = True
    channel._clients["ou-user"] = BackgroundClient()
    cards = []
    channel._send_card = lambda *args: cards.append(args) or True

    channel._ensure_principal_watcher("ou-user")
    for _ in range(100):
        if channel._notification_cursors.get("ou-user") == 9:
            break
        await asyncio.sleep(0.01)

    channel._running = False
    watcher = channel._principal_watchers["ou-user"]
    watcher.cancel()
    await asyncio.gather(watcher, return_exceptions=True)

    assert cards == [
        ("ou-user", "后台工作已处理完毕。", "blue", "小诺")
    ]
    assert channel._notification_cursors == {"ou-user": 9}
    persisted = json.loads(
        (tmp_path / "data" / "feishu_notification_cursors.json").read_text()
    )
    assert persisted == {"ou-user": 9}


@pytest.mark.asyncio
async def test_feishu_principal_feed_skips_foreground_task_duplicate(
    tmp_path,
) -> None:
    channel = FeishuChannel(_config(tmp_path))
    channel._foreground_task_ids.add("task-foreground")
    feed_event = PrincipalTaskEvent(
        feed_event_id=4,
        principal_id="principal-a",
        event=TaskEvent(
            task_id="task-foreground",
            event_seq=5,
            occurred_at=1.0,
            event_type="completed",
            payload=TaskEventPayload(state=TaskState.COMPLETED),
        ),
    )

    class UnexpectedLookup:
        async def get_task(self, _task_id):
            raise AssertionError("foreground Task must not be looked up")

    assert await channel._deliver_principal_task_event(
        "ou-user",
        UnexpectedLookup(),
        feed_event,
    )
    assert "task-foreground" not in channel._foreground_task_ids


@pytest.mark.asyncio
async def test_feishu_principal_feed_resolves_background_approval_in_card(
    tmp_path,
) -> None:
    channel = FeishuChannel(_config(tmp_path))
    created_cards = []
    updated_cards = []
    channel._send_card_returning_id = (
        lambda recipient, card: created_cards.append((recipient, card))
        or "card-background"
    )
    channel._update_card = (
        lambda message_id, card: updated_cards.append((message_id, card))
        or True
    )
    event = _approval_event(task_id="task-background")
    feed_event = PrincipalTaskEvent(
        feed_event_id=5,
        principal_id="principal-a",
        event=event,
    )

    class ApprovalClient:
        def __init__(self) -> None:
            self.resolutions = []
            self.fail_once = True

        async def get_task(self, task_id):
            assert task_id == "task-background"
            return SimpleNamespace(
                state=TaskState.WAITING_APPROVAL,
                session_handle="session-background",
            )

        async def resolve_approval(self, approval_id, *, approved):
            self.resolutions.append((approval_id, approved))
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("connection lost after resolution")
            return SimpleNamespace()

    client = ApprovalClient()
    delivery = asyncio.create_task(
        channel._deliver_principal_task_event(
            "ou-user",
            client,
            feed_event,
        )
    )
    while "ou-user" not in channel._pending_confirmations:
        await asyncio.sleep(0)

    assert channel._resolve_confirmation(
        "ou-user",
        "confirmation-a",
        True,
        task_id="task-background",
    )
    assert not await delivery
    assert await channel._deliver_principal_task_event(
        "ou-user",
        client,
        feed_event,
    )

    assert client.resolutions == [
        ("confirmation-a", True),
        ("confirmation-a", True),
    ]
    assert len(created_cards) == 1
    assert "确认" in json.dumps(created_cards[0][1], ensure_ascii=False)
    assert updated_cards[0][0] == "card-background"
    assert "已确认" in json.dumps(updated_cards[0][1], ensure_ascii=False)
    assert "task-background" not in channel._active_task_presentations


def test_feishu_long_fenced_code_keeps_balanced_markdown() -> None:
    text = "```python\n" + ("print('小诺')\n" * 500) + "```\n"

    chunks = _split_text(text)

    assert len(chunks) > 1
    assert all(chunk.count("```") == 2 for chunk in chunks)


@pytest.mark.asyncio
async def test_feishu_ws_card_patch_dispatches_card_payload() -> None:
    class Headers(list):
        def add(self):
            header = SimpleNamespace(key="", value="")
            self.append(header)
            return header

    class Frame:
        def __init__(self) -> None:
            self.headers = Headers(
                [
                    SimpleNamespace(key="type", value="card"),
                    SimpleNamespace(key="message_id", value="message-a"),
                    SimpleNamespace(key="sum", value="1"),
                    SimpleNamespace(key="seq", value="1"),
                ]
            )
            self.payload = b'{"event":"card"}'

        def SerializeToString(self) -> bytes:
            return b"serialized-frame"

    class Handler:
        def __init__(self) -> None:
            self.payloads = []

        def do_without_validation(self, payload):
            self.payloads.append(payload)
            return {"toast": {"type": "info", "content": "ok"}}

    class WebSocketClient:
        def __init__(self) -> None:
            self._event_handler = Handler()
            self.writes = []
            self.original_calls = 0

        async def _handle_data_frame(self, _frame) -> None:
            self.original_calls += 1

        async def _write_message(self, payload: bytes) -> None:
            self.writes.append(payload)

    client = WebSocketClient()
    frame = Frame()

    _patch_ws_card_dispatch(client)
    await client._handle_data_frame(frame)

    assert client._event_handler.payloads == [b'{"event":"card"}']
    assert client.original_calls == 0
    assert client.writes == [b"serialized-frame"]


@pytest.mark.asyncio
async def test_feishu_card_io_does_not_block_core_event_consumption(tmp_path) -> None:
    class BurstClient:
        def __init__(self) -> None:
            self.consumed = asyncio.Event()

        async def execute_task(self, _session, _text, _attachments):
            yield TaskEvent(
                task_id="task-burst",
                event_seq=1,
                occurred_at=1.0,
                event_type="task_created",
                payload=TaskEventPayload(),
            )
            for index in range(2, 1002):
                yield TaskEvent(
                    task_id="task-burst",
                    event_seq=index,
                    occurred_at=float(index),
                    event_type="reasoning_delta",
                    payload=TaskEventPayload(content="x"),
                )
            yield TaskEvent(
                task_id="task-burst",
                event_seq=1002,
                occurred_at=1002.0,
                event_type="final_output",
                payload=TaskEventPayload(content="完成"),
            )
            yield TaskEvent(
                task_id="task-burst",
                event_seq=1003,
                occurred_at=1003.0,
                event_type="completed",
                payload=TaskEventPayload(),
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
        channel._stream_core_task("ou-user", client, "session-a", "hello", ())
    )

    await asyncio.wait_for(client.consumed.wait(), timeout=0.5)
    assert not task.done()
    release_card.set()
    await asyncio.wait_for(task, timeout=2)
