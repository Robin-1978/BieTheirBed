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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import httpx

from knoa_platform.branding import ASSISTANT_NAME
from knoa_platform.channels.contracts import ChannelMessage
from knoa_platform.channels.dingtalk_cards import dingtalk_markdown
from knoa_platform.channels.dingtalk_cards import project_dingtalk_card
from knoa_platform.channels.feishu import FeishuChannel
from knoa_platform.channels.feishu_cards import _principal_for_log
from knoa_platform.config import AppConfig

logger = logging.getLogger(__name__)

_DINGTALK_API = "https://api.dingtalk.com"
_TEXT_LIMIT = 4000
_MARKDOWN_BUTTON_CARD_TEMPLATE_ID = "1366a1eb-bc54-4859-ac88-517c56a9acb1.schema"


@dataclass(frozen=True)
class _InboundMedia:
    download_code: str
    file_name: str
    is_image: bool = False


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


def _rich_text_content(payload: Mapping[str, Any]) -> tuple[str, tuple[_InboundMedia, ...]]:
    """Extract text and downloadable media from DingTalk rich-text messages."""
    raw: Any = payload.get("richText") or payload.get("rich_text")
    content = _as_dict(payload.get("content"))
    if raw is None:
        raw = content.get("richText") or content.get("rich_text")
    if not isinstance(raw, (list, tuple)):
        return "", ()

    text_parts: list[str] = []
    media: list[_InboundMedia] = []
    for raw_item in raw:
        item = _as_dict(raw_item)
        if not item:
            continue
        raw_text = item.get("text")
        if isinstance(raw_text, Mapping):
            text = _nested(raw_text, "content", "text")
        elif raw_text is not None:
            text = str(raw_text).strip()
        else:
            raw_content = item.get("content")
            text = str(raw_content).strip() if isinstance(raw_content, str) else ""
        if text:
            text_parts.append(text)
        download_code = _nested(
            item,
            "downloadCode",
            "download_code",
            "fileKey",
            "file_key",
            "content.downloadCode",
            "content.download_code",
            "content.fileKey",
        )
        if not download_code:
            continue
        file_name = _nested(
            item,
            "fileName",
            "file_name",
            "filename",
            "content.fileName",
            "content.file_name",
        )
        item_type = _nested(item, "type", "msgtype", "messageType").lower()
        guessed_type, _ = mimetypes.guess_type(file_name)
        is_image = item_type in {"image", "picture", "pic"} or bool(
            guessed_type and guessed_type.startswith("image/")
        )
        media.append(
            _InboundMedia(
                download_code=download_code,
                file_name=file_name or ("image" if is_image else "attachment.bin"),
                is_image=is_image,
            )
        )
    return "\n".join(text_parts).strip(), tuple(media)


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
        self._card_actions: dict[str, dict[str, dict[str, str]]] = {}
        self._interactive_cards: set[str] = set()
        self._fallback_cards: set[str] = set()
        self._fallback_approval_delivered: set[str] = set()
        self._fallback_card_delivered: set[str] = set()
        self._interactive_cards_supported: bool | None = None
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
                card_callback_topic = getattr(
                    dingtalk_stream,
                    "Card_Callback_Router_Topic",
                    "/v1.0/card/instances/callback",
                )
                register(card_callback_topic, _CardCallbackHandler(self))
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
        rich_text, media = _rich_text_content(payload)
        if rich_text:
            text = "\n".join(part for part in (text.strip(), rich_text) if part)
        if media_key:
            is_image = msg_type in {"picture", "image"}
            media = (
                _InboundMedia(
                    download_code=media_key,
                    file_name=file_name or ("image" if is_image else "attachment.bin"),
                    is_image=is_image,
                ),
                *media,
            )
        if media:
            unique_media: dict[str, _InboundMedia] = {}
            for item in media:
                unique_media.setdefault(item.download_code, item)
            media = tuple(unique_media.values())
        if not text and not media:
            content = _as_dict(payload.get("content"))
            logger.warning(
                "Ignored unsupported DingTalk message msgtype=%s payload_keys=%s content_keys=%s",
                msg_type or "unknown",
                sorted(str(key) for key in payload),
                sorted(str(key) for key in content),
            )
            return False
        self._submit(
            self._handle_inbound_message(
                principal,
                message_id,
                text.strip(),
                media,
            )
        )
        return True

    def ingest_card_callback(self, callback: Any) -> bool:
        """Resolve a pending approval from a DingTalk interactive-card button."""
        payload = _as_dict(callback)
        if payload.get("data") is not None:
            nested_payload = _as_dict(payload["data"])
            if nested_payload:
                payload = nested_payload
        content: Any = payload.get("content")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except json.JSONDecodeError:
                content = {}
        content = _as_dict(content)
        private_data: Any = content.get("cardPrivateData") or content.get(
            "card_private_data"
        )
        if isinstance(private_data, str):
            try:
                private_data = json.loads(private_data)
            except json.JSONDecodeError:
                private_data = {}
        private_data = _as_dict(private_data)
        action_ids: Any = (
            private_data.get("actionIds")
            or private_data.get("action_ids")
            or content.get("actionIds")
            or content.get("action_ids")
            or payload.get("actionIds")
            or payload.get("action_ids")
        )
        if isinstance(action_ids, str):
            try:
                decoded_action_ids = json.loads(action_ids)
            except json.JSONDecodeError:
                decoded_action_ids = [action_ids]
            action_ids = decoded_action_ids
        if not isinstance(action_ids, (list, tuple)):
            action_ids = []
        params: Any = (
            private_data.get("params")
            or content.get("params")
            or payload.get("params")
        )
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except json.JSONDecodeError:
                params = {}
        params = _as_dict(params)
        card_instance_id = _nested(
            payload,
            "outTrackId",
            "cardInstanceId",
            "card_instance_id",
        )
        logger.info(
            "DingTalk card callback received card=%s action_ids=%s",
            card_instance_id[:12],
            [str(value)[:32] for value in action_ids[:3]],
        )
        # The SDK's built-in Markdown button template reports the pressed
        # request button through cardPrivateData.actionIds.  Bind that ID back
        # to the action captured when this exact card instance was rendered.
        if not params:
            actions = self._card_actions.get(card_instance_id, {})
            for action_id in action_ids:
                matched = actions.get(str(action_id))
                if matched is not None:
                    params = matched
                    break
        action = str(params.get("action") or "").strip().lower()
        if action not in {"confirm", "cancel"}:
            logger.warning("Ignored DingTalk card callback with unknown action=%s", action)
            return False

        expected_recipient = self._card_recipients.get(card_instance_id, "")
        open_id = _nested(payload, "userId", "user_id") or expected_recipient
        if (
            not expected_recipient
            or not open_id
            or (expected_recipient and open_id != expected_recipient)
            or not self._save_binding(open_id)
        ):
            logger.warning(
                "Ignored DingTalk card callback from non-owner sender=%s",
                _principal_for_log(open_id),
            )
            return False
        pending = self._resolve_confirmation(
            open_id,
            str(params.get("approval_id") or ""),
            action == "confirm",
            resource_id=str(params.get("resource_id") or ""),
        )
        if pending is None:
            logger.info(
                "Ignored expired DingTalk card confirmation card=%s",
                card_instance_id[:12],
            )
            return False
        logger.info(
            "DingTalk card confirmation resolved approved=%s card=%s",
            action == "confirm",
            card_instance_id[:12],
        )
        return True

    async def _handle_inbound_message(
        self,
        principal: str,
        message_id: str,
        text: str,
        media: tuple[_InboundMedia, ...],
    ) -> None:
        """Persist all media before submitting accompanying rich-text content."""
        for index, item in enumerate(media):
            derived_message_id = f"{message_id}:{index}" if len(media) > 1 else message_id
            if item.is_image:
                await self._handle_image(
                    principal,
                    derived_message_id,
                    item.download_code,
                )
            else:
                await self._handle_file(
                    principal,
                    derived_message_id,
                    item.download_code,
                    item.file_name,
                )
        if text:
            await self._handle_text(principal, text, message_id)

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
        text = dingtalk_markdown(str(text))
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
            delivered = self._send_robot_message(
                open_id,
                "sampleMarkdown",
                {"title": ASSISTANT_NAME, "text": chunk or " "},
                token=token,
            )
            if not delivered:
                # Keep a final plain-text escape hatch for tenants where the
                # Markdown robot message type has not been enabled.
                delivered = self._send_robot_message(
                    open_id,
                    "sampleText",
                    {"content": chunk},
                    token=token,
                )
            succeeded = delivered and succeeded
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
        conversation_type, conversation_id = self._conversation_contexts.get(
            open_id, ("1", "")
        )
        if conversation_type == "2" and conversation_id:
            endpoint = "groupMessages/send"
            body = {
                "robotCode": robot_code,
                "openConversationId": conversation_id,
                "msgKey": msg_key,
                "msgParam": json.dumps(msg_param, ensure_ascii=False),
            }
        else:
            endpoint = "oToMessages/batchSend"
            body = {
                "robotCode": robot_code,
                "userIds": [open_id],
                "msgKey": msg_key,
                "msgParam": json.dumps(msg_param, ensure_ascii=False),
            }
        response = httpx.post(
            f"{_DINGTALK_API}/v1.0/robot/{endpoint}",
            headers=headers,
            json=body,
            timeout=15,
        )
        if response.is_error:
            logger.error(
                "DingTalk message send failed endpoint=%s status=%s body=%s",
                endpoint,
                response.status_code,
                response.text[:300],
            )
            return False
        return True

    def _send_card_returning_id(self, open_id: str, card: dict[str, Any]) -> str | None:
        interactive_id = self._create_interactive_card(open_id, card)
        if interactive_id:
            self._card_recipients[interactive_id] = open_id
            self._card_actions[interactive_id] = self._interactive_card_action_map(card)
            self._interactive_cards.add(interactive_id)
            self._trim_card_state()
            return interactive_id

        message_id = uuid.uuid4().hex
        self._card_recipients[message_id] = open_id
        self._fallback_cards.add(message_id)
        if self._fallback_card_is_immediate(card):
            if not self._send_text(open_id, self._render_dingtalk_card(card)):
                self._card_recipients.pop(message_id, None)
                self._fallback_cards.discard(message_id)
                return None
            if self._fallback_card_is_waiting_approval(card):
                self._fallback_approval_delivered.add(message_id)
            else:
                self._fallback_card_delivered.add(message_id)
        self._trim_card_state()
        return message_id

    def _update_card(self, message_id: str, card: dict[str, Any]) -> bool:
        recipient = self._card_recipients.get(message_id)
        if not recipient:
            return False
        if message_id in self._interactive_cards and self._update_interactive_card(
            message_id, card
        ):
            actions = self._interactive_card_action_map(card)
            if actions:
                self._card_actions[message_id] = actions
            else:
                self._card_actions.pop(message_id, None)
            return True
        if message_id in self._interactive_cards:
            self._interactive_cards.discard(message_id)
            self._fallback_cards.add(message_id)
        if message_id not in self._fallback_cards:
            return False
        # A plain DingTalk message cannot be edited. Suppress streaming patches
        # while still delivering one approval prompt and one terminal snapshot.
        # This avoids one message per reasoning/model delta when native cards
        # are unavailable without making confirmation impossible.
        if self._fallback_card_is_waiting_approval(card):
            if message_id in self._fallback_approval_delivered:
                return True
            delivered = self._send_text(recipient, self._render_dingtalk_card(card))
            if delivered:
                self._fallback_approval_delivered.add(message_id)
            return delivered
        if not self._fallback_card_is_terminal(card):
            return True
        if message_id in self._fallback_card_delivered:
            return True
        delivered = self._send_text(recipient, self._render_dingtalk_card(card))
        if delivered:
            self._fallback_card_delivered.add(message_id)
        return delivered

    @staticmethod
    def _fallback_card_is_terminal(card: dict[str, Any]) -> bool:
        title = project_dingtalk_card(card).title
        return "处理中" not in title and "等待确认" not in title

    @staticmethod
    def _fallback_card_is_waiting_approval(card: dict[str, Any]) -> bool:
        return "等待确认" in project_dingtalk_card(card).title

    @classmethod
    def _fallback_card_is_immediate(cls, card: dict[str, Any]) -> bool:
        title = project_dingtalk_card(card).title
        return "等待确认" in title or cls._fallback_card_is_terminal(card)

    def _trim_card_state(self) -> None:
        while len(self._card_recipients) > 1000:
            oldest = next(iter(self._card_recipients))
            self._card_recipients.pop(oldest, None)
            self._card_actions.pop(oldest, None)
            self._interactive_cards.discard(oldest)
            self._fallback_cards.discard(oldest)
            self._fallback_approval_delivered.discard(oldest)
            self._fallback_card_delivered.discard(oldest)

    def _create_interactive_card(
        self,
        open_id: str,
        card: dict[str, Any],
    ) -> str | None:
        if self._stream_client is None or self._interactive_cards_supported is False:
            return None
        card_params = self._interactive_card_params(card)
        card_instance_id = uuid.uuid4().hex
        headers = {
            "x-acs-dingtalk-access-token": self._access_token_value(),
            "Content-Type": "application/json",
        }
        create = httpx.post(
            f"{_DINGTALK_API}/v1.0/card/instances",
            headers=headers,
            json={
                "cardTemplateId": _MARKDOWN_BUTTON_CARD_TEMPLATE_ID,
                "outTrackId": card_instance_id,
                "cardData": {"cardParamMap": card_params},
                "callbackType": "STREAM",
                "imGroupOpenSpaceModel": {"supportForward": True},
                "imRobotOpenSpaceModel": {"supportForward": True},
            },
            timeout=15,
        )
        if create.is_error:
            if (
                create.status_code == 403
                and "Card.Instance.Write" in create.text
            ):
                self._interactive_cards_supported = False
            logger.warning(
                "DingTalk interactive card create failed status=%s body=%s; falling back to text",
                create.status_code,
                create.text[:300],
            )
            return None
        self._interactive_cards_supported = True

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
        card_params = self._interactive_card_params(card)
        response = httpx.put(
            f"{_DINGTALK_API}/v1.0/card/instances",
            headers={
                "x-acs-dingtalk-access-token": self._access_token_value(),
                "Content-Type": "application/json",
            },
            json={
                "outTrackId": card_instance_id,
                "cardData": {"cardParamMap": card_params},
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

    @classmethod
    def _interactive_card_params(cls, card: dict[str, Any]) -> dict[str, str]:
        title, markdown = cls._interactive_card_content(card)
        buttons = cls._interactive_card_buttons(card)
        tips = ""
        if buttons:
            tips = "点击按钮，或回复“确认”/“取消”（confirm/cancel）"
            markdown = f"{markdown}\n\n> {tips}"
        return {
            "title": title,
            "markdown": markdown,
            "tips": tips,
            "sys_full_json_obj": json.dumps(
                {"msgButtons": buttons},
                ensure_ascii=False,
            ),
        }

    @staticmethod
    def _interactive_card_action_map(
        card: dict[str, Any],
    ) -> dict[str, dict[str, str]]:
        actions: dict[str, dict[str, str]] = {}

        def visit(value: Any) -> None:
            if isinstance(value, Mapping):
                if str(value.get("type") or "") == "callback":
                    callback = _as_dict(value.get("value"))
                    action = str(callback.get("action") or "").strip().lower()
                    approval_id = str(callback.get("approval_id") or "").strip()
                    resource_id = str(callback.get("resource_id") or "").strip()
                    if action in {"confirm", "cancel"} and approval_id and resource_id:
                        actions[f"knoa_{action}"] = {
                            "action": action,
                            "approval_id": approval_id,
                            "resource_id": resource_id,
                        }
                for nested in value.values():
                    visit(nested)
            elif isinstance(value, (list, tuple)):
                for nested in value:
                    visit(nested)

        visit(card)
        return actions

    @classmethod
    def _interactive_card_buttons(cls, card: dict[str, Any]) -> list[dict[str, Any]]:
        actions = cls._interactive_card_action_map(card)
        buttons: list[dict[str, Any]] = []
        for action, text, color in (
            ("confirm", "确认", "blue"),
            ("cancel", "取消", "gray"),
        ):
            action_id = f"knoa_{action}"
            if action_id in actions:
                buttons.append(
                    {
                        "text": text,
                        "color": color,
                        "id": action_id,
                        "request": True,
                    }
                )
        return buttons

    @staticmethod
    def _render_dingtalk_card(card: dict[str, Any]) -> str:
        projected = project_dingtalk_card(card)
        rendered = projected.as_text()
        approval_hint = ""
        if any(marker in rendered for marker in ("确认", "批准", "confirm", "approve")):
            approval_hint = "\n\n回复“确认”/“取消”（或 confirm/cancel）即可处理审批。"
        return f"{rendered}{approval_hint}"

    def _send_image(self, open_id: str, path: Path, name: str = "") -> bool:
        return self._send_media(open_id, path, name or path.name, is_image=True)

    def _send_file(self, open_id: str, path: Path, name: str = "") -> bool:
        return self._send_media(open_id, path, name or path.name, is_image=False)

    def _send_media(
        self,
        open_id: str,
        path: Path,
        name: str,
        *,
        is_image: bool,
    ) -> bool:
        """Upload with DingTalk's SDK, then send the matching robot message."""
        try:
            data = path.read_bytes()
        except OSError:
            logger.exception("DingTalk media file could not be read path=%s", path)
            return False
        media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        uploader = getattr(self._stream_client, "upload_to_dingtalk", None)
        if not callable(uploader):
            logger.error("DingTalk media upload unavailable: Stream client is not connected")
            return False
        try:
            media_id = str(
                uploader(
                    data,
                    filetype="image" if is_image else "file",
                    filename=name,
                    mimetype=media_type,
                )
                or ""
            )
        except Exception:
            logger.exception("DingTalk SDK media upload failed name=%s", name)
            return False
        if not media_id:
            logger.error("DingTalk SDK media upload returned no media id name=%s", name)
            return False

        if is_image:
            msg_key = "sampleImageMsg"
            msg_param = {"photoURL": media_id}
        else:
            suffix = Path(name).suffix.lstrip(".").lower() or "file"
            msg_key = "sampleFile"
            msg_param = {
                "mediaId": media_id,
                "fileName": name,
                "fileType": suffix,
            }
        return self._send_robot_message(open_id, msg_key, msg_param)

    def _download_media(self, download_code: str) -> tuple[bytes, str]:
        token = self._access_token_value()
        response = httpx.post(
            f"{_DINGTALK_API}/v1.0/robot/messageFiles/download",
            headers={"x-acs-dingtalk-access-token": token},
            json={
                "downloadCode": download_code,
                "robotCode": self._dingtalk_config.dingtalk_robot_code or self._app_id,
            },
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


class _CardCallbackHandler(_StreamHandler):
    """DingTalk Stream callback for interactive confirmation buttons."""

    async def process(self, callback: Any) -> tuple[int, str]:
        self._channel.ingest_card_callback(callback)
        try:
            from dingtalk_stream.frames import AckMessage

            return AckMessage.STATUS_OK, "OK"
        except ImportError:  # pragma: no cover - optional SDK fallback
            return 200, "OK"


__all__ = ["DingTalkChannel"]
