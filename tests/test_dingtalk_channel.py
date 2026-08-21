from __future__ import annotations

from knoa_platform.channels.dingtalk import DingTalkChannel
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
