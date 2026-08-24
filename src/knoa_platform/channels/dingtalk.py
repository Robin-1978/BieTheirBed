"""DingTalk Stream channel adapter.

The Core conversation/task implementation is deliberately shared with the
Feishu adapter.  DingTalk is only responsible for translating Stream events
and the small set of robot message APIs; this keeps task state, approvals,
attachments and retry semantics identical across channels.

The optional ``dingtalk_stream`` package is imported lazily.  A Node can be
installed without the optional SDK and still start normally when the channel
is disabled.  When enabled, a missing SDK is reported in logs and the channel
keeps retrying instead of taking down Core.
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

import httpx

from knoa_platform.channels.feishu import FeishuChannel
from knoa_platform.channels.dingtalk_cards import project_dingtalk_card
from knoa_platform.channels.feishu_cards import _principal_for_log
from knoa_platform.channels.contracts import ChannelMessage
from knoa_platform.config import AppConfig

logger = logging.getLogger(__name__)

_DINGTALK_API = "https://api.dingtalk.com"
_TEXT_LIMIT = 4000
_MARKDOWN_CARD_TEMPLATE_ID = "589420e2-c1e2-46ef-a5ed-b8728e654da9.schema"


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(k): v for k, v in value.items()}
    if hasattr(value, "to_dict"):
        try:
            converted = value.to_dict()
            if isinstance(converted, Mapping):
                return {str(k): v for k, v in converted.items()}
        except Exception:  # pragma: no cover - third-party object quirks
            pass
    if hasattr(value, "__dict__"):
        return {
            str(k): v
            for k, v in vars(value).items()
            if not str(k).startswith("_")
        }
    return {}


def _nested(payload: Mapping[str, Any], *paths: str) -> str:
    for path in paths:
        value: Any = payload
        for key in path.split("."):
            if isinstance(value, Mapping):
                value = value.get(key)
            else:
                value = getattr(value, key, None)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


class DingTalkChannel(FeishuChannel):
    """DingTalk Stream adapter with Feishu-compatible Core semantics."""

    name = "dingtalk"

    @staticmethod
    def message_contract(
        principal_id: str,
        message_id: str,
        *,
        text: str = "",
    ) -> ChannelMessage:
        return ChannelMessage(
            channel="dingtalk",
            principal_id=principal_id,
            message_id=message_id,
            text=text,
        )

    def __init__(self, config: AppConfig) -> None:
        self._dingtalk_config = config
        # Feishu mixins contain the complete Core/session/task implementation.
        # Feed them a private mapped view so credentials never leak into a
        # second implementation or alter the user's configured Feishu channel.
        mapped = config.model_copy(
            update={
                "feishu_enabled": True,
                "feishu_app_id": config.dingtalk_client_id,
                "feishu_app_secret": config.dingtalk_client_secret,
                "feishu_receive_id": config.dingtalk_receive_id,
            }
        )
        super().__init__(mapped)
        self._config = config
        self._app_id = config.dingtalk_client_id.strip()
        self._app_secret = config.dingtalk_client_secret.get_secret_value().strip()
        self._receive_id = config.dingtalk_receive_id.strip()
        self._binding_path = self._paths.data / "dingtalk_open_id"
        self._sessions_path = self._paths.data / "dingtalk_sessions.json"
        self._notification_cursors_path = (
            self._paths.data / "dingtalk_notification_cursors.json"
        )
        self._notification_intent_cursors_path = (
            self._paths.data / "dingtalk_notification_intent_cursors.json"
        )
        self._outbox = self._paths.cache / "dingtalk-outbox"
        self._stream_client: Any = None
        self._stream_thread: threading.Thread | None = None
        self._stream_stop = threading.Event()
        self._card_recipients: dict[str, str] = {}
        self._interactive_cards: set[str] = set()
        self._conversation_contexts: dict[str, tuple[str, str]] = {}
        self._token_lock = threading.RLock()
        self._access_token = ""
        self._access_token_expires_at = 0.0

    async def start(self) -> None:
        if self._running:
            raise RuntimeError("DingTalkChannel is already started")
        if not self._app_id or not self._app_secret:
            raise ValueError("DingTalk client_id and client_secret are required")
        self._main_loop = asyncio.get_running_loop()
        self._load_sessions()
        self._load_notification_cursors()
        self._load_notification_intent_cursors()
        self._running = True
        self._stream_stop.clear()
        for principal in self._sessions:
            self._ensure_principal_watcher(principal)
        self._stream_thread = threading.Thread(
            target=self._run_stream,
            name="knoa-dingtalk",
            daemon=True,
        )
        self._stream_thread.start()
        logger.info("DingTalkChannel started (Stream mode)")
        if self._receive_id:
            await asyncio.to_thread(self._send_text, self._receive_id, "已启动")

    async def stop(self) -> None:
        self._running = False
        self._stream_stop.set()
        client, self._stream_client = self._stream_client, None
        if client is not None:
            for method_name in ("stop", "close", "shutdown"):
                method = getattr(client, method_name, None)
                if callable(method):
                    try:
                        result = method()
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception:
                        logger.debug("DingTalk Stream client stop failed", exc_info=True)
                    break
        await super().stop()
        logger.info("DingTalkChannel stopped")

    def _run_stream(self) -> None:
        while self._running and not self._stream_stop.is_set():
            try:
                import dingtalk_stream  # type: ignore[import-not-found]

                credential = dingtalk_stream.Credential(self._app_id, self._app_secret)
                client = dingtalk_stream.DingTalkStreamClient(credential)
                handler = _StreamHandler(self)
                chatbot_message = getattr(dingtalk_stream, "ChatbotMessage", None)
                if chatbot_message is None:
                    from dingtalk_stream.chatbot import ChatbotMessage as chatbot_message
                topic = getattr(chatbot_message, "TOPIC", "/v1.0/im/bot/messages/get")
                register = getattr(client, "register_callback_handler", None)
                if not callable(register):
                    raise RuntimeError("dingtalk_stream client lacks callback registration")
                register(topic, handler)
                self._stream_client = client
                logger.info("DingTalk Stream connecting")
                result = client.start()
                if asyncio.iscoroutine(result):
                    asyncio.run(result)
            except ImportError:
                logger.error(
                    "DingTalk channel enabled but dingtalk-stream is not installed"
                )
                self._stream_stop.wait(30)
            except Exception:
                logger.exception("DingTalk Stream connection failed")
                self._stream_stop.wait(3)

    def ingest_callback(self, callback: Any) -> bool:
        """Normalize a Stream callback and schedule Core work.

        This public seam is also used by deployment smoke tests and makes the
        adapter independent of the exact callback object version shipped by
        DingTalk.
        """
        payload = _as_dict(callback)
        if payload.get("data") is not None:
            nested_payload = _as_dict(payload["data"])
            if nested_payload:
                payload = nested_payload
        message_id = _nested(payload, "msgId", "messageId", "msg_id")
        principal = _nested(
            payload,
            "senderStaffId",
            "sender_staff_id",
            "senderId",
            "sender_id",
            "sender.staffId",
            "sender.userId",
            "conversationId",
            "conversation_id",
        )
        if not principal or not self._claim_message(message_id):
            return False
        if not self._save_binding(principal):
            logger.warning(
                "Ignored DingTalk message from non-owner sender=%s",
                _principal_for_log(principal),
            )
            return False
        conversation_type = _nested(
            payload,
            "conversationType",
            "conversation_type",
        )
        conversation_id = _nested(
            payload,
            "conversationId",
            "conversation_id",
        )
        self._conversation_contexts[principal] = (
            conversation_type or "1",
            conversation_id,
        )
        text = _nested(payload, "text.content", "text.content_text", "content.text", "text")
        msg_type = _nested(payload, "msgtype", "messageType", "message_type", "type").lower()
        media_key = _nested(
            payload,
            "content.downloadCode",
            "image_content.download_code",
            "download_code",
            "downloadCode",
            "content.fileKey",
            "fileKey",
        )
        file_name = _nested(payload, "content.fileName", "fileName", "filename", "content.file_name")
        if text:
            self._submit(self._handle_text(principal, text.strip(), message_id))
        elif media_key and msg_type in {"picture", "image"}:
            self._submit(self._handle_image(principal, message_id, media_key))
        elif media_key:
            self._submit(
                self._handle_file(
                    principal,
                    message_id,
                    media_key,
                    file_name or "attachment.bin",
                )
            )
        else:
            return False
        return True

    def _current_receive_id(self) -> str:
        if self._receive_id:
            return self._receive_id
        try:
            return self._binding_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _add_reaction(self, _message_id: str, _emoji_type: str = "Typing") -> str:
        return ""

    def _remove_reaction(self, _message_id: str, _reaction_id: str) -> None:
        return None

    def _access_token_value(self) -> str:
        now = time.time()
        with self._token_lock:
            if self._access_token and now < self._access_token_expires_at - 60:
                return self._access_token
            response = httpx.post(
                f"{_DINGTALK_API}/v1.0/oauth2/accessToken",
                json={"appKey": self._app_id, "appSecret": self._app_secret},
                timeout=10,
            )
            response.raise_for_status()
            body = response.json()
            self._access_token = str(body.get("accessToken") or "")
            expires = float(body.get("expireIn") or 7200)
            self._access_token_expires_at = now + max(60.0, expires)
            if not self._access_token:
                raise RuntimeError("DingTalk access token response was empty")
            return self._access_token

    def _send_text(self, open_id: str, text: str) -> bool:
        text = str(text)
        # Test/deployment integrations can provide a sender without requiring
        # the optional SDK's internal client shape.
        sender = getattr(self._stream_client, "send_text", None)
        if callable(sender):
            result = sender(open_id, text)
            if asyncio.iscoroutine(result):
                asyncio.run(result)
            return True
        chunks = [text[index : index + _TEXT_LIMIT] for index in range(0, len(text), _TEXT_LIMIT)] or [""]
        token = self._access_token_value()
        succeeded = True
        for chunk in chunks:
            succeeded = self._send_robot_message(
                open_id,
                "sampleText",
                {"content": chunk},
                token=token,
            ) and succeeded
        return succeeded

    def _send_robot_message(
        self,
        open_id: str,
        msg_key: str,
        msg_param: dict[str, Any],
        *,
        token: str | None = None,
    ) -> bool:
        """Send a Stream conversation message using the official v1 API."""
        token = token or self._access_token_value()
        robot_code = self._dingtalk_config.dingtalk_robot_code or self._app_id
        headers = {"x-acs-dingtalk-access-token": token}
        body = {
            "robotCode": robot_code,
            "openConversationId": open_id,
            "msgKey": msg_key,
            "msgParam": json.dumps(msg_param, ensure_ascii=False),
        }
        response = httpx.post(
            f"{_DINGTALK_API}/v1.0/robot/groupMessages/send",
            headers=headers,
            json=body,
            timeout=15,
        )
        if not response.is_error:
            return True
        # Some Stream callbacks are one-to-one conversations.  The group
        # endpoint rejects those; retry with the documented batch endpoint.
        fallback = {
            "robotCode": robot_code,
            "userIds": [open_id],
            "msgKey": msg_key,
            "msgParam": json.dumps(msg_param, ensure_ascii=False),
        }
        second = httpx.post(
            f"{_DINGTALK_API}/v1.0/robot/oToMessages/batchSend",
            headers=headers,
            json=fallback,
            timeout=15,
        )
        if second.is_error:
            logger.error(
                "DingTalk message send failed status=%s/%s body=%s",
                response.status_code,
                second.status_code,
                second.text[:300],
            )
            return False
        return True

    def _send_card_returning_id(self, open_id: str, card: dict[str, Any]) -> str | None:
        interactive_id = self._create_interactive_card(open_id, card)
        if interactive_id:
            self._card_recipients[interactive_id] = open_id
            self._interactive_cards.add(interactive_id)
            self._trim_card_state()
            return interactive_id

        message_id = uuid.uuid4().hex
        if not self._send_text(open_id, self._render_dingtalk_card(card)):
            return None
        self._card_recipients[message_id] = open_id
        self._trim_card_state()
        return message_id

    def _update_card(self, message_id: str, card: dict[str, Any]) -> bool:
        recipient = self._card_recipients.get(message_id)
        if not recipient:
            return False
        if message_id in self._interactive_cards and self._update_interactive_card(
            message_id, card
        ):
            return True
        # Preserve the final result when the account has not granted the card
        # APIs or DingTalk rejects an update for a particular conversation.
        # Text fallback is deliberately last-resort instead of the normal path.
        return self._send_text(recipient, self._render_dingtalk_card(card))

    def _trim_card_state(self) -> None:
        while len(self._card_recipients) > 1000:
            oldest = next(iter(self._card_recipients))
            self._card_recipients.pop(oldest, None)
            self._interactive_cards.discard(oldest)

    def _create_interactive_card(
        self,
        open_id: str,
        card: dict[str, Any],
    ) -> str | None:
        if self._stream_client is None:
            return None
        title, markdown = self._interactive_card_content(card)
        card_instance_id = uuid.uuid4().hex
        headers = {
            "x-acs-dingtalk-access-token": self._access_token_value(),
            "Content-Type": "application/json",
        }
        create = httpx.post(
            f"{_DINGTALK_API}/v1.0/card/instances",
            headers=headers,
            json={
                "cardTemplateId": _MARKDOWN_CARD_TEMPLATE_ID,
                "outTrackId": card_instance_id,
                "cardData": {
                    "cardParamMap": {"title": title, "markdown": markdown}
                },
                "callbackType": "STREAM",
                "imGroupOpenSpaceModel": {"supportForward": True},
                "imRobotOpenSpaceModel": {"supportForward": True},
            },
            timeout=15,
        )
        if create.is_error:
            logger.warning(
                "DingTalk interactive card create failed status=%s body=%s; falling back to text",
                create.status_code,
                create.text[:300],
            )
            return None

        conversation_type, conversation_id = self._conversation_contexts.get(
            open_id, ("1", "")
        )
        deliver: dict[str, Any] = {
            "outTrackId": card_instance_id,
            "userIdType": 1,
        }
        if conversation_type == "2" and conversation_id:
            deliver.update(
                {
                    "openSpaceId": f"dtv1.card//IM_GROUP.{conversation_id}",
                    "imGroupOpenDeliverModel": {
                        "robotCode": self._dingtalk_config.dingtalk_robot_code
                        or self._app_id
                    },
                }
            )
        else:
            deliver.update(
                {
                    "openSpaceId": f"dtv1.card//IM_ROBOT.{open_id}",
                    "imRobotOpenDeliverModel": {"spaceType": "IM_ROBOT"},
                }
            )
        response = httpx.post(
            f"{_DINGTALK_API}/v1.0/card/instances/deliver",
            headers=headers,
            json=deliver,
            timeout=15,
        )
        if response.is_error:
            logger.warning(
                "DingTalk interactive card delivery failed status=%s body=%s; falling back to text",
                response.status_code,
                response.text[:300],
            )
            return None
        return card_instance_id

    def _update_interactive_card(
        self,
        card_instance_id: str,
        card: dict[str, Any],
    ) -> bool:
        title, markdown = self._interactive_card_content(card)
        response = httpx.put(
            f"{_DINGTALK_API}/v1.0/card/instances",
            headers={
                "x-acs-dingtalk-access-token": self._access_token_value(),
                "Content-Type": "application/json",
            },
            json={
                "outTrackId": card_instance_id,
                "cardData": {
                    "cardParamMap": {"title": title, "markdown": markdown}
                },
            },
            timeout=15,
        )
        if response.is_error:
            logger.warning(
                "DingTalk interactive card update failed status=%s body=%s; falling back to text",
                response.status_code,
                response.text[:300],
            )
            self._interactive_cards.discard(card_instance_id)
            return False
        return True

    @classmethod
    def _interactive_card_content(cls, card: dict[str, Any]) -> tuple[str, str]:
        projected = project_dingtalk_card(card)
        return projected.title, projected.markdown

    @staticmethod
    def _render_dingtalk_card(card: dict[str, Any]) -> str:
        projected = project_dingtalk_card(card)
        rendered = projected.as_text()
        approval_hint = ""
        if any(marker in rendered for marker in ("确认", "批准", "confirm", "approve")):
            approval_hint = "\n\n回复“确认”/“取消”（或 confirm/cancel）即可处理审批。"
        return f"{rendered}{approval_hint}"

    def _send_image(self, open_id: str, path: Path, name: str = "") -> bool:
        return self._send_media(open_id, path, name or path.name, "sampleImageMsg")

    def _send_file(self, open_id: str, path: Path, name: str = "") -> bool:
        return self._send_media(open_id, path, name or path.name, "sampleFileMsg")

    def _send_media(self, open_id: str, path: Path, name: str, msg_key: str) -> bool:
        data = path.read_bytes()
        media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        token = self._access_token_value()
        response = httpx.post(
            f"{_DINGTALK_API}/v1.0/robot/messageFiles/upload",
            headers={"x-acs-dingtalk-access-token": token},
            data={"robotCode": self._dingtalk_config.dingtalk_robot_code or self._app_id},
            files={"file": (name, data, media_type)},
            timeout=30,
        )
        if response.is_error:
            logger.error("DingTalk media upload failed status=%s", response.status_code)
            return False
        payload = response.json()
        media_id = str(payload.get("mediaId") or payload.get("media_id") or "")
        download_url = str(payload.get("downloadUrl") or payload.get("download_url") or "")
        if not media_id and not download_url:
            logger.error("DingTalk media upload response did not contain media id")
            return False
        key = "photoURL" if msg_key == "sampleImageMsg" and download_url else "mediaId"
        value = download_url if key == "photoURL" else media_id
        return self._send_robot_message(open_id, msg_key, {key: value}, token=token)

    def _download_media(self, download_code: str) -> tuple[bytes, str]:
        token = self._access_token_value()
        response = httpx.post(
            f"{_DINGTALK_API}/v1.0/robot/messageFiles/download",
            headers={"x-acs-dingtalk-access-token": token},
            json={"downloadCode": download_code, "robotCode": self._app_id},
            timeout=20,
        )
        response.raise_for_status()
        body = response.json()
        url = str(body.get("downloadUrl") or body.get("download_url") or "")
        if not url:
            raise RuntimeError("DingTalk media response did not contain downloadUrl")
        media = httpx.get(url, timeout=30)
        media.raise_for_status()
        content_type = media.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
        return media.content, content_type

    def _download_image(self, _message_id: str, image_key: str) -> tuple[bytes, str]:
        data, media_type = self._download_media(image_key)
        if media_type == "application/octet-stream":
            media_type = "image/jpeg"
        return data, media_type

    def _download_file(self, _message_id: str, file_key: str, _file_name: str = "") -> tuple[bytes, str]:
        return self._download_media(file_key)

    def _download_audio(self, _message_id: str, file_key: str) -> tuple[bytes, str, str]:
        data, media_type = self._download_media(file_key)
        normalized = media_type if media_type != "application/octet-stream" else "audio/ogg"
        suffix = {
            "audio/ogg": "ogg",
            "audio/mpeg": "mp3",
            "audio/wav": "wav",
            "audio/mp4": "m4a",
        }.get(normalized, "ogg")
        return data, normalized, f"voice-message.{suffix}"


class _StreamHandler:
    """Version-tolerant dingtalk-stream callback handler."""

    def __init__(self, channel: DingTalkChannel) -> None:
        self._channel = channel

    def pre_start(self) -> None:
        return None

    async def process(self, callback: Any) -> tuple[int, str]:
        self._channel.ingest_callback(callback)
        try:
            from dingtalk_stream.frames import AckMessage

            return AckMessage.STATUS_OK, "OK"
        except ImportError:  # pragma: no cover - optional SDK fallback
            return 200, "OK"

    async def raw_process(self, callback: Any) -> Any:
        code, message = await self.process(callback)
        try:
            from dingtalk_stream.frames import AckMessage, Headers

            ack = AckMessage()
            ack.code = code
            headers = getattr(callback, "headers", None)
            ack.headers.message_id = getattr(headers, "message_id", "")
            ack.headers.content_type = Headers.CONTENT_TYPE_APPLICATION_JSON
            ack.data = {"response": message}
            return ack
        except ImportError:  # pragma: no cover
            return (code, message)


__all__ = ["DingTalkChannel"]
