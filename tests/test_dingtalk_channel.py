from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

from knoa_platform.channels.dingtalk import DingTalkChannel
from knoa_platform.channels.dingtalk_cards import (
    dingtalk_markdown,
    project_dingtalk_card,
)
from knoa_platform.config import AppConfig


def _channel(tmp_path) -> DingTalkChannel:
    return DingTalkChannel(
        AppConfig(
            runtime_root=str(tmp_path),
            dingtalk_enabled=True,
            dingtalk_client_id="client-id",
            dingtalk_client_secret="client-secret",
        )
    )


def test_dingtalk_config_requires_credentials() -> None:
    try:
        AppConfig(dingtalk_enabled=True)
    except ValueError as exc:
        assert "DingTalk" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("missing DingTalk credentials must be rejected")


def test_dingtalk_stream_callback_is_normalized_and_owner_bound(tmp_path) -> None:
    channel = _channel(tmp_path)
    assert (
        channel.ingest_callback(
            {
                "msgId": "message-1",
                "senderStaffId": "staff-1",
                "msgtype": "text",
                "text": {"content": "  hello  "},
            }
        )
        is True
    )
    assert channel._current_receive_id() == "staff-1"
    assert (
        channel.ingest_callback(
            {
                "msgId": "message-2",
                "senderStaffId": "staff-2",
                "msgtype": "text",
                "text": {"content": "intruder"},
            }
        )
        is False
    )


def test_dingtalk_stream_registers_message_and_card_callbacks(
    tmp_path,
    monkeypatch,
) -> None:
    channel = _channel(tmp_path)
    registered: list[tuple[str, object]] = []

    class _Client:
        def __init__(self, _credential):
            pass

        def register_callback_handler(self, topic, handler):
            registered.append((topic, handler))

        def start(self):
            channel._running = False

    fake_sdk = SimpleNamespace(
        Credential=lambda client_id, client_secret: (client_id, client_secret),
        DingTalkStreamClient=_Client,
        ChatbotMessage=SimpleNamespace(TOPIC="/chat/messages"),
        Card_Callback_Router_Topic="/card/callback",
    )
    monkeypatch.setitem(sys.modules, "dingtalk_stream", fake_sdk)
    channel._running = True

    channel._run_stream()

    assert [topic for topic, _handler in registered] == [
        "/chat/messages",
        "/card/callback",
    ]
    assert type(registered[1][1]).__name__ == "_CardCallbackHandler"


def test_dingtalk_rich_text_file_is_ingested_before_its_text(tmp_path) -> None:
    channel = _channel(tmp_path)
    scheduled = []
    events: list[tuple[str, ...]] = []

    channel._submit = scheduled.append

    async def handle_file(principal, message_id, download_code, file_name):
        events.append(("file", principal, message_id, download_code, file_name))

    async def handle_text(principal, text, message_id):
        events.append(("text", principal, message_id, text))

    channel._handle_file = handle_file
    channel._handle_text = handle_text

    assert (
        channel.ingest_callback(
            {
                "msgId": "message-rich-file",
                "senderStaffId": "staff-1",
                "msgtype": "richText",
                "content": {
                    "richText": [
                        {"type": "text", "text": "请看这份设计"},
                        {
                            "type": "file",
                            "downloadCode": "download-code",
                            "fileName": "设计.md",
                        },
                    ]
                },
            }
        )
        is True
    )

    asyncio.run(scheduled[0])
    assert events == [
        ("file", "staff-1", "message-rich-file", "download-code", "设计.md"),
        ("text", "staff-1", "message-rich-file", "请看这份设计"),
    ]


def test_dingtalk_rich_text_supports_multiple_media_and_derived_ids(tmp_path) -> None:
    channel = _channel(tmp_path)
    scheduled = []
    events: list[tuple[str, ...]] = []

    channel._submit = scheduled.append

    async def handle_file(principal, message_id, download_code, file_name):
        events.append(("file", principal, message_id, download_code, file_name))

    async def handle_image(principal, message_id, download_code):
        events.append(("image", principal, message_id, download_code))

    channel._handle_file = handle_file
    channel._handle_image = handle_image

    assert (
        channel.ingest_callback(
            {
                "msgId": "message-rich-media",
                "senderStaffId": "staff-1",
                "msgtype": "richText",
                "content": {
                    "richText": [
                        {
                            "type": "picture",
                            "downloadCode": "image-code",
                            "fileName": "preview.png",
                        },
                        {
                            "downloadCode": "file-code",
                            "fileName": "notes.md",
                        },
                    ]
                },
            }
        )
        is True
    )

    asyncio.run(scheduled[0])
    assert events == [
        ("image", "staff-1", "message-rich-media:0", "image-code"),
        ("file", "staff-1", "message-rich-media:1", "file-code", "notes.md"),
    ]


def test_dingtalk_direct_file_callback_remains_supported(tmp_path) -> None:
    channel = _channel(tmp_path)
    scheduled = []
    events: list[tuple[str, ...]] = []
    channel._submit = scheduled.append

    async def handle_file(principal, message_id, download_code, file_name):
        events.append((principal, message_id, download_code, file_name))

    channel._handle_file = handle_file

    assert (
        channel.ingest_callback(
            {
                "msgId": "message-file",
                "senderStaffId": "staff-1",
                "msgtype": "file",
                "content": {"downloadCode": "direct-code", "fileName": "direct.md"},
            }
        )
        is True
    )

    asyncio.run(scheduled[0])
    assert events == [("staff-1", "message-file", "direct-code", "direct.md")]


def test_dingtalk_message_contract_uses_channel_neutral_shape(tmp_path) -> None:
    message = _channel(tmp_path).message_contract("staff-1", "m-1", text="hello")
    assert message.channel == "dingtalk"
    assert message.principal_id == "staff-1"
    assert message.text == "hello"


def test_dingtalk_approval_card_fallback_explains_text_confirmation(tmp_path) -> None:
    channel = _channel(tmp_path)
    sent: list[str] = []
    channel._send_text = lambda _recipient, text: sent.append(text) or True

    message_id = channel._send_card_returning_id(
        "staff-1",
        {
            "header": {"title": {"content": "小诺 · 等待确认"}},
            "body": {"elements": [{"content": "请确认 deploy 变更"}]},
        },
    )

    assert message_id
    assert sent and "确认" in sent[0] and "confirm/cancel" in sent[0]


def test_dingtalk_approval_card_projects_native_callback_buttons(tmp_path) -> None:
    channel = _channel(tmp_path)
    params = channel._interactive_card_params(
        {
            "header": {"title": {"content": "小诺 · 等待确认"}},
            "body": {
                "elements": [
                    {"tag": "markdown", "content": "需要确认 · 读取桌面文件"},
                    {
                        "tag": "column_set",
                        "columns": [
                            {
                                "elements": [
                                    {
                                        "tag": "button",
                                        "behaviors": [
                                            {
                                                "type": "callback",
                                                "value": {
                                                    "action": "confirm",
                                                    "resource_id": "turn-1",
                                                    "approval_id": "approval-1",
                                                },
                                            }
                                        ],
                                    }
                                ]
                            },
                            {
                                "elements": [
                                    {
                                        "tag": "button",
                                        "behaviors": [
                                            {
                                                "type": "callback",
                                                "value": {
                                                    "action": "cancel",
                                                    "resource_id": "turn-1",
                                                    "approval_id": "approval-1",
                                                },
                                            }
                                        ],
                                    }
                                ]
                            },
                        ],
                    },
                ]
            },
        }
    )

    buttons = json.loads(params["sys_full_json_obj"])["msgButtons"]
    assert [
        (button["text"], button["id"], button["request"]) for button in buttons
    ] == [
        ("确认", "knoa_confirm", True),
    ]
    assert "actionType" not in buttons[0]
    assert "params" not in buttons[0]
    assert channel._interactive_card_action_map(
        {
            "body": {
                "elements": [
                    {
                        "behaviors": [
                            {
                                "type": "callback",
                                "value": {
                                    "action": "confirm",
                                    "approval_id": "approval-1",
                                    "resource_id": "turn-1",
                                },
                            }
                        ]
                    }
                ]
            }
        }
    )["knoa_confirm"] == {
        "action": "confirm",
        "approval_id": "approval-1",
        "resource_id": "turn-1",
    }
    private_data = channel._interactive_card_private_data(
        "staff-1",
        {
            "body": {
                "elements": [
                    {
                        "behaviors": [
                            {
                                "type": "callback",
                                "value": {
                                    "action": "confirm",
                                    "approval_id": "approval-1",
                                    "resource_id": "turn-1",
                                },
                            }
                        ]
                    }
                ]
            }
        },
    )
    assert json.loads(
        private_data["staff-1"]["cardParamMap"]["knoa_action"]
    ) == {
        "action": "confirm",
        "approval_id": "approval-1",
        "resource_id": "turn-1",
    }
    assert params["flowStatus"] == "3"
    assert "回复" in params["staticMsgContent"]
    assert "cancel" in params["staticMsgContent"]


def test_dingtalk_card_callback_resolves_only_the_bound_recipient(tmp_path) -> None:
    channel = _channel(tmp_path)
    channel._card_recipients["card-1"] = "staff-1"
    channel._card_actions["card-1"] = {
        "knoa_confirm": {
            "action": "confirm",
            "approval_id": "approval-1",
            "resource_id": "turn-1",
        }
    }
    resolved: list[tuple] = []
    pending = object()

    def resolve(open_id, approval_id, approved, *, resource_id=""):
        resolved.append((open_id, approval_id, approved, resource_id))
        return pending

    channel._resolve_confirmation = resolve
    callback = {
        "data": {
            "userId": "staff-1",
            "outTrackId": "card-1",
            "content": json.dumps(
                {
                    "cardPrivateData": {
                        "actionIds": ["knoa_confirm"],
                    }
                }
            ),
        }
    }

    assert channel.ingest_card_callback(callback)
    assert resolved == [("staff-1", "approval-1", True, "turn-1")]

    callback["data"]["userId"] = "staff-2"
    assert channel.ingest_card_callback(callback) is False
    assert len(resolved) == 1


def test_dingtalk_card_callback_accepts_sdk_normalized_object(tmp_path) -> None:
    channel = _channel(tmp_path)
    channel._card_recipients["card-sdk"] = "staff-1"
    channel._card_actions["card-sdk"] = {
        "knoa_confirm": {
            "action": "confirm",
            "approval_id": "approval-2",
            "resource_id": "turn-2",
        }
    }
    resolved: list[tuple] = []
    channel._resolve_confirmation = (
        lambda open_id, approval_id, approved, *, resource_id="": (
            resolved.append((open_id, approval_id, approved, resource_id)) or object()
        )
    )

    callback = SimpleNamespace(
        user_id="staff-1",
        card_instance_id="card-sdk",
        content={"cardPrivateData": {"actionIds": ["dynamic_v2_component_id"]}},
    )

    assert channel.ingest_card_callback(callback)
    assert resolved == [("staff-1", "approval-2", True, "turn-2")]


def test_dingtalk_v2_dynamic_button_uses_persisted_private_action(tmp_path) -> None:
    channel = _channel(tmp_path)
    channel._card_recipients["card-v2"] = "staff-1"
    channel._card_actions["card-v2"] = {
        "knoa_confirm": {
            "action": "confirm",
            "approval_id": "approval-v2",
            "resource_id": "turn-v2",
        }
    }
    channel._interactive_cards.add("card-v2")
    channel._save_card_state()

    restored = _channel(tmp_path)
    restored._load_card_state()
    resolved: list[tuple[str, str, bool, str]] = []
    restored._resolve_confirmation = (
        lambda open_id, approval_id, approved, *, resource_id="": (
            resolved.append((open_id, approval_id, approved, resource_id)) or object()
        )
    )
    callback = {
        "data": {
            "userId": "staff-1",
            "outTrackId": "card-v2",
            "content": json.dumps(
                {
                    "cardPrivateData": {
                        "actionIds": ["single_button_node_dynamic"],
                    }
                }
            ),
        }
    }

    assert restored.ingest_card_callback(callback)
    assert resolved == [("staff-1", "approval-v2", True, "turn-v2")]
    assert "card-v2" not in restored._card_actions


def test_dingtalk_v2_callback_accepts_unique_out_track_id_prefix(tmp_path) -> None:
    channel = _channel(tmp_path)
    full_card_id = "card-v2-full-out-track-id"
    channel._card_recipients[full_card_id] = "staff-1"
    channel._card_actions[full_card_id] = {
        "knoa_confirm": {
            "action": "confirm",
            "approval_id": "approval-v2",
            "resource_id": "turn-v2",
        }
    }
    resolved: list[tuple[str, str, bool, str]] = []
    channel._resolve_confirmation = (
        lambda open_id, approval_id, approved, *, resource_id="": (
            resolved.append((open_id, approval_id, approved, resource_id)) or object()
        )
    )

    assert channel.ingest_card_callback(
        {
            "data": {
                "userId": "staff-1",
                "outTrackId": "card-v2-full-callback-id",
                "content": json.dumps(
                    {
                        "params": {"card_v2_internal": "not-our-action"},
                        "cardPrivateData": {
                            "actionIds": ["single_button_node_dynamic"],
                        }
                    }
                ),
            }
        }
    )
    assert resolved == [("staff-1", "approval-v2", True, "turn-v2")]
    assert full_card_id not in channel._card_actions


def test_dingtalk_v2_callback_reads_card_private_data(tmp_path) -> None:
    channel = _channel(tmp_path)
    channel._card_recipients["card-private"] = "staff-1"
    resolved: list[tuple[str, str, bool, str]] = []
    channel._resolve_confirmation = (
        lambda open_id, approval_id, approved, *, resource_id="": (
            resolved.append((open_id, approval_id, approved, resource_id)) or object()
        )
    )
    action = {
        "action": "confirm",
        "approval_id": "approval-private",
        "resource_id": "turn-private",
    }
    callback = {
        "data": {
            "userId": "staff-1",
            "outTrackId": "card-private",
            "content": json.dumps(
                {
                    "cardPrivateData": {
                        "staff-1": {
                            "actionIds": ["single_button_node_dynamic"],
                            "cardParamMap": {
                                "knoa_action": json.dumps(action),
                            },
                        },
                    }
                }
            ),
        }
    }

    assert channel.ingest_card_callback(callback)
    assert resolved == [
        ("staff-1", "approval-private", True, "turn-private")
    ]


def test_dingtalk_text_confirmation_accepts_advertised_english_commands(
    tmp_path,
) -> None:
    channel = _channel(tmp_path)
    channel._add_reaction = lambda *_args: ""
    channel._remove_reaction = lambda *_args: None
    sent: list[str] = []
    resolved: list[tuple[str, bool]] = []
    channel._send_text = lambda _recipient, text: sent.append(text) or True

    def resolve(_open_id, approval_id, approved, **_kwargs):
        resolved.append((approval_id, approved))
        return object()

    channel._resolve_confirmation = resolve
    channel._pending_confirmations["staff-1"] = SimpleNamespace(
        approval_id="approval-confirm",
        resolved=False,
    )
    asyncio.run(channel._handle_text("staff-1", "confirm", "message-1"))

    channel._pending_confirmations["staff-1"] = SimpleNamespace(
        approval_id="approval-cancel",
        resolved=False,
    )
    asyncio.run(channel._handle_text("staff-1", "cancel", "message-2"))

    assert resolved == [
        ("approval-confirm", True),
        ("approval-cancel", False),
    ]
    assert sent == ["已确认", "已取消"]


def test_dingtalk_text_confirmation_without_pending_action_is_not_a_new_turn(
    tmp_path,
) -> None:
    channel = _channel(tmp_path)
    channel._add_reaction = lambda *_args: ""
    channel._remove_reaction = lambda *_args: None
    sent: list[str] = []
    channel._send_text = lambda _recipient, text: sent.append(text) or True

    async def unexpected_run(*_args, **_kwargs):
        raise AssertionError("confirmation command must not start an Agent turn")

    channel._run_text = unexpected_run

    asyncio.run(channel._handle_text("staff-1", "/confirm", "message-1"))

    assert sent == ["当前没有待确认的操作。"]


def test_dingtalk_card_fallback_coalesces_progress_into_one_final_message(
    tmp_path,
) -> None:
    channel = _channel(tmp_path)
    sent: list[str] = []
    channel._send_text = lambda _recipient, text: sent.append(text) or True

    message_id = channel._send_card_returning_id(
        "staff-1",
        {
            "header": {"title": {"content": "小诺 · 处理中"}},
            "body": {"elements": [{"content": "正在处理"}]},
        },
    )
    assert message_id
    assert sent == []
    assert channel._update_card(
        message_id,
        {
            "header": {"title": {"content": "小诺 · 处理中"}},
            "body": {"elements": [{"content": "仍在处理"}]},
        },
    )
    assert sent == []
    assert channel._update_card(
        message_id,
        {
            "header": {"title": {"content": "小诺 · 已完成"}},
            "body": {"elements": [{"content": "结果已交付"}]},
        },
    )
    assert len(sent) == 1
    assert "结果已交付" in sent[0]
    assert channel._update_card(
        message_id,
        {
            "header": {"title": {"content": "小诺"}},
            "body": {"elements": [{"content": "结果已交付"}]},
        },
    )
    assert len(sent) == 1


def test_dingtalk_card_fallback_delivers_approval_and_terminal_once(tmp_path) -> None:
    channel = _channel(tmp_path)
    sent: list[str] = []
    channel._send_text = lambda _recipient, text: sent.append(text) or True

    message_id = channel._send_card_returning_id(
        "staff-1",
        {
            "header": {"title": {"content": "小诺 · 处理中"}},
            "body": {"elements": [{"content": "正在查找文件"}]},
        },
    )
    approval = {
        "header": {"title": {"content": "小诺 · 等待确认"}},
        "body": {"elements": [{"content": "需要确认 · 读取桌面文件"}]},
    }
    terminal = {
        "header": {"title": {"content": "小诺 · 已完成"}},
        "body": {"elements": [{"content": "文件已发送"}]},
    }

    assert message_id
    assert sent == []
    assert channel._update_card(message_id, approval)
    assert channel._update_card(message_id, approval)
    assert len(sent) == 1
    assert "confirm/cancel" in sent[0]
    assert channel._update_card(message_id, terminal)
    assert channel._update_card(message_id, terminal)
    assert len(sent) == 2
    assert "文件已发送" in sent[1]


def test_dingtalk_card_fallback_delivers_each_distinct_approval(tmp_path) -> None:
    channel = _channel(tmp_path)
    sent: list[str] = []
    channel._send_text = lambda _recipient, text: sent.append(text) or True
    message_id = channel._send_card_returning_id(
        "staff-1",
        {
            "header": {"title": {"content": "小诺 · 处理中"}},
            "body": {"elements": [{"content": "正在处理"}]},
        },
    )

    def approval(approval_id: str) -> dict:
        return {
            "header": {"title": {"content": "小诺 · 等待确认"}},
            "body": {
                "elements": [
                    {"content": f"需要确认 · {approval_id}"},
                    {
                        "behaviors": [
                            {
                                "type": "callback",
                                "value": {
                                    "action": "confirm",
                                    "approval_id": approval_id,
                                    "resource_id": "turn-1",
                                },
                            }
                        ]
                    },
                ]
            },
        }

    assert message_id
    assert channel._update_card(message_id, approval("approval-1"))
    assert channel._update_card(message_id, approval("approval-1"))
    assert channel._update_card(message_id, approval("approval-2"))
    assert len(sent) == 2
    assert "approval-1" in sent[0]
    assert "approval-2" in sent[1]


def test_dingtalk_card_permission_denial_is_cached(tmp_path, monkeypatch) -> None:
    channel = _channel(tmp_path)
    channel._stream_client = object()
    channel._access_token_value = lambda: "token"
    requests = []

    class _Response:
        is_error = True
        status_code = 403
        text = "missing Card.Instance.Write"

    def post(url, **_kwargs):
        requests.append(url)
        return _Response()

    monkeypatch.setattr("knoa_platform.channels.dingtalk.httpx.post", post)
    card = {
        "header": {"title": {"content": "小诺 · 处理中"}},
        "body": {"elements": [{"content": "正在处理"}]},
    }

    assert channel._send_card_returning_id("staff-1", card)
    assert channel._send_card_returning_id("staff-1", card)
    assert len(requests) == 1


def test_dingtalk_direct_text_uses_one_to_one_endpoint(tmp_path, monkeypatch) -> None:
    channel = _channel(tmp_path)
    channel._stream_client = object()
    channel._conversation_contexts["staff-1"] = ("1", "conversation-1")
    channel._access_token_value = lambda: "token"
    requests: list[tuple[str, dict]] = []

    class _Response:
        is_error = False
        status_code = 200
        text = "{}"

    def post(url, *, json, **_kwargs):
        requests.append((url, json))
        return _Response()

    monkeypatch.setattr("knoa_platform.channels.dingtalk.httpx.post", post)

    assert channel._send_text("staff-1", "**你好** <font color='grey'>世界</font>")
    assert len(requests) == 1
    assert requests[0][0].endswith("/v1.0/robot/oToMessages/batchSend")
    assert requests[0][1]["userIds"] == ["staff-1"]
    assert requests[0][1]["msgKey"] == "sampleMarkdown"
    assert json.loads(requests[0][1]["msgParam"]) == {
        "title": "小诺",
        "text": "**你好** > 世界",
    }


def test_dingtalk_group_text_uses_callback_conversation_id(
    tmp_path, monkeypatch
) -> None:
    channel = _channel(tmp_path)
    channel._stream_client = object()
    channel._conversation_contexts["staff-1"] = ("2", "conversation-group")
    channel._access_token_value = lambda: "token"
    requests: list[tuple[str, dict]] = []

    class _Response:
        is_error = False
        status_code = 200
        text = "{}"

    def post(url, *, json, **_kwargs):
        requests.append((url, json))
        return _Response()

    monkeypatch.setattr("knoa_platform.channels.dingtalk.httpx.post", post)

    assert channel._send_text("staff-1", "## 大家好")
    assert len(requests) == 1
    assert requests[0][0].endswith("/v1.0/robot/groupMessages/send")
    assert requests[0][1]["openConversationId"] == "conversation-group"
    assert requests[0][1]["msgKey"] == "sampleMarkdown"


def test_dingtalk_markdown_falls_back_to_plain_text(tmp_path, monkeypatch) -> None:
    channel = _channel(tmp_path)
    channel._stream_client = object()
    channel._access_token_value = lambda: "token"
    requests: list[dict] = []

    class _Response:
        status_code = 200
        text = "{}"

        def __init__(self, is_error: bool) -> None:
            self.is_error = is_error

    def post(_url, *, json, **_kwargs):
        requests.append(json)
        return _Response(is_error=len(requests) == 1)

    monkeypatch.setattr("knoa_platform.channels.dingtalk.httpx.post", post)

    assert channel._send_text("staff-1", "**最终结果**")
    assert [request["msgKey"] for request in requests] == [
        "sampleMarkdown",
        "sampleText",
    ]
    assert json.loads(requests[1]["msgParam"]) == {"content": "**最终结果**"}


def test_dingtalk_image_uses_sdk_upload_and_image_message(
    tmp_path, monkeypatch
) -> None:
    channel = _channel(tmp_path)
    image = tmp_path / "截图.jpg"
    image.write_bytes(b"jpeg-data")
    uploads: list[tuple[bytes, dict]] = []
    requests: list[dict] = []

    class _StreamClient:
        def upload_to_dingtalk(self, data, **kwargs):
            uploads.append((data, kwargs))
            return "@image-media-id"

    class _Response:
        is_error = False

    def post(_url, *, json, **_kwargs):
        requests.append(json)
        return _Response()

    channel._stream_client = _StreamClient()
    channel._access_token_value = lambda: "token"
    monkeypatch.setattr("knoa_platform.channels.dingtalk.httpx.post", post)

    assert channel._send_image("staff-1", image)
    assert uploads == [
        (
            b"jpeg-data",
            {
                "filetype": "image",
                "filename": "截图.jpg",
                "mimetype": "image/jpeg",
            },
        )
    ]
    assert requests[0]["msgKey"] == "sampleImageMsg"
    assert json.loads(requests[0]["msgParam"]) == {"photoURL": "@image-media-id"}


def test_dingtalk_file_uses_sdk_upload_and_file_message(tmp_path, monkeypatch) -> None:
    channel = _channel(tmp_path)
    attachment = tmp_path / "report.pdf"
    attachment.write_bytes(b"pdf-data")
    uploads: list[tuple[bytes, dict]] = []
    requests: list[dict] = []

    class _StreamClient:
        def upload_to_dingtalk(self, data, **kwargs):
            uploads.append((data, kwargs))
            return "@file-media-id"

    class _Response:
        is_error = False

    def post(_url, *, json, **_kwargs):
        requests.append(json)
        return _Response()

    channel._stream_client = _StreamClient()
    channel._access_token_value = lambda: "token"
    monkeypatch.setattr("knoa_platform.channels.dingtalk.httpx.post", post)

    assert channel._send_file("staff-1", attachment, "交付报告.pdf")
    assert uploads == [
        (
            b"pdf-data",
            {
                "filetype": "file",
                "filename": "交付报告.pdf",
                "mimetype": "application/pdf",
            },
        )
    ]
    assert requests[0]["msgKey"] == "sampleFile"
    assert json.loads(requests[0]["msgParam"]) == {
        "mediaId": "@file-media-id",
        "fileName": "交付报告.pdf",
        "fileType": "pdf",
    }


def test_dingtalk_media_download_uses_configured_robot_code(
    tmp_path, monkeypatch
) -> None:
    channel = DingTalkChannel(
        AppConfig(
            runtime_root=str(tmp_path),
            dingtalk_enabled=True,
            dingtalk_client_id="client-id",
            dingtalk_client_secret="client-secret",
            dingtalk_robot_code="robot-code",
        )
    )
    channel._access_token_value = lambda: "token"
    requests: list[tuple[str, dict | None]] = []

    class _Response:
        def __init__(self, *, body=None, content=b"", headers=None):
            self._body = body or {}
            self.content = content
            self.headers = headers or {}

        def raise_for_status(self):
            return None

        def json(self):
            return self._body

    def post(url, *, json, **_kwargs):
        requests.append((url, json))
        return _Response(body={"downloadUrl": "https://download.example/attachment"})

    def get(url, **_kwargs):
        requests.append((url, None))
        return _Response(content=b"attachment", headers={"content-type": "image/png"})

    monkeypatch.setattr("knoa_platform.channels.dingtalk.httpx.post", post)
    monkeypatch.setattr("knoa_platform.channels.dingtalk.httpx.get", get)

    assert channel._download_media("download-code") == (b"attachment", "image/png")
    assert requests[0][1] == {
        "downloadCode": "download-code",
        "robotCode": "robot-code",
    }


def test_dingtalk_card_projection_removes_feishu_html() -> None:
    projected = project_dingtalk_card(
        {
            "header": {"title": {"content": "小诺 · 处理中"}},
            "body": {
                "elements": [
                    {
                        "tag": "markdown",
                        "content": (
                            "<font color='grey'>正在思考… 1 &lt; 2</font>"
                            "\n\n<strong>结果</strong>"
                        ),
                    },
                    {"tag": "hr"},
                    {"tag": "markdown", "content": "已完成"},
                ]
            },
        }
    )

    assert projected.title == "小诺 · 处理中"
    assert "<font" not in projected.markdown
    assert "<strong>" not in projected.markdown
    assert "> 正在思考… 1 < 2" in projected.markdown
    assert "**结果**" in projected.markdown
    assert "---" in projected.markdown


def test_dingtalk_markdown_preserves_html_inside_code_fence() -> None:
    assert dingtalk_markdown(
        "```html\n<span>literal</span>\n```\n<span>display</span>"
    ) == ("```html\n<span>literal</span>\n```\ndisplay")


def test_dingtalk_interactive_card_is_created_delivered_and_updated(
    tmp_path,
    monkeypatch,
) -> None:
    channel = _channel(tmp_path)
    channel._stream_client = object()
    channel._conversation_contexts["staff-1"] = ("1", "conversation-1")
    channel._access_token_value = lambda: "token"
    sent: list[str] = []
    channel._send_text = lambda _recipient, text: sent.append(text) or True
    requests: list[tuple[str, str, dict]] = []

    class _Response:
        is_error = False
        status_code = 200
        text = "{}"

    def post(url, *, json, **_kwargs):
        requests.append(("POST", url, json))
        return _Response()

    def put(url, *, json, **_kwargs):
        requests.append(("PUT", url, json))
        return _Response()

    monkeypatch.setattr("knoa_platform.channels.dingtalk.httpx.post", post)
    monkeypatch.setattr("knoa_platform.channels.dingtalk.httpx.put", put)

    message_id = channel._send_card_returning_id(
        "staff-1",
        {
            "header": {"title": {"content": "小诺 · 处理中"}},
            "body": {
                "elements": [
                    {
                        "tag": "markdown",
                        "content": "<font color='grey'>正在思考…</font>",
                    }
                ]
            },
        },
    )
    assert message_id
    assert sent == []
    assert requests[0][1].endswith("/v1.0/card/instances")
    assert requests[0][2]["cardTemplateId"] == (
        "382e4302-551d-4880-bf29-a30acfab2e71.schema"
    )
    assert requests[1][1].endswith("/v1.0/card/instances/deliver")
    assert requests[1][2]["openSpaceId"] == "dtv1.card//IM_ROBOT.staff-1"
    assert "privateData" not in requests[0][2]
    assert requests[0][2]["userIdType"] == 1
    assert "<font" not in requests[0][2]["cardData"]["cardParamMap"]["staticMsgContent"]
    assert requests[0][2]["cardData"]["cardParamMap"]["flowStatus"] == "2"

    assert channel._update_card(
        message_id,
        {
            "header": {"title": {"content": "小诺"}},
            "body": {"elements": [{"tag": "markdown", "content": "结果已交付"}]},
        },
    )
    assert requests[-1][0] == "PUT"
    assert requests[-1][2]["outTrackId"] == message_id
    assert "privateData" not in requests[-1][2]
    assert requests[-1][2]["userIdType"] == 1
    assert sent == []


def test_dingtalk_cancelled_card_stops_ai_card_spinner(tmp_path) -> None:
    channel = _channel(tmp_path)
    params = channel._interactive_card_params(
        {
            "header": {
                "template": "grey",
                "title": {"content": "已停止"},
            },
            "body": {
                "elements": [
                    {"tag": "markdown", "content": "已停止"},
                ]
            },
        }
    )

    assert params["flowStatus"] == "3"
    assert params["msgTitle"] == "已停止"
