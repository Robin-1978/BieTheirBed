from __future__ import annotations

from knoa_platform.channels.dingtalk import DingTalkChannel
from knoa_platform.channels.dingtalk_cards import dingtalk_markdown, project_dingtalk_card
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
    assert channel.ingest_callback(
        {
            "msgId": "message-1",
            "senderStaffId": "staff-1",
            "msgtype": "text",
            "text": {"content": "  hello  "},
        }
    ) is True
    assert channel._current_receive_id() == "staff-1"
    assert channel.ingest_callback(
        {
            "msgId": "message-2",
            "senderStaffId": "staff-2",
            "msgtype": "text",
            "text": {"content": "intruder"},
        }
    ) is False


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


def test_dingtalk_card_updates_deliver_final_state_as_text(tmp_path) -> None:
    channel = _channel(tmp_path)
    sent: list[str] = []
    channel._send_text = lambda _recipient, text: sent.append(text) or True

    message_id = channel._send_card_returning_id(
        "staff-1",
        {"header": {"title": {"content": "小诺"}}, "body": {"elements": [{"content": "正在处理"}]}},
    )
    assert message_id
    assert channel._update_card(
        message_id,
        {"header": {"title": {"content": "小诺 · 已完成"}}, "body": {"elements": [{"content": "结果已交付"}]}},
    )
    assert "结果已交付" in sent[-1]


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
    assert dingtalk_markdown("```html\n<span>literal</span>\n```\n<span>display</span>") == (
        "```html\n<span>literal</span>\n```\ndisplay"
    )


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
                    {"tag": "markdown", "content": "<font color='grey'>正在思考…</font>"}
                ]
            },
        },
    )
    assert message_id
    assert sent == []
    assert requests[0][1].endswith("/v1.0/card/instances")
    assert requests[1][1].endswith("/v1.0/card/instances/deliver")
    assert requests[1][2]["openSpaceId"] == "dtv1.card//IM_ROBOT.staff-1"
    assert "<font" not in requests[0][2]["cardData"]["cardParamMap"]["markdown"]

    assert channel._update_card(
        message_id,
        {
            "header": {"title": {"content": "小诺"}},
            "body": {"elements": [{"tag": "markdown", "content": "结果已交付"}]},
        },
    )
    assert requests[-1][0] == "PUT"
    assert requests[-1][2]["outTrackId"] == message_id
    assert sent == []
