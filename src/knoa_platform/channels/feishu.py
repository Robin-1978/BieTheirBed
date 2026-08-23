"""Feishu adapter that speaks only the public Core WebSocket API."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from typing import Any

from knoa_platform.channels.feishu_cards import (
    _ActiveTaskPresentation,
    _PendingConfirmation,
    _StreamingCardState as _StreamingCardState,
    _markdown_table_count as _markdown_table_count,
    _patch_ws_card_dispatch,
    _principal_for_log as _principal_for_log,
    _render_card_markdown as _render_card_markdown,
    _service_notice as _service_notice,
    _split_text as _split_text,
)
from knoa_platform.channels.feishu_conversation import FeishuConversationMixin
from knoa_platform.channels.feishu_tasks import FeishuTaskMixin
from knoa_platform.channels.feishu_transport import FeishuTransportMixin
from knoa_platform.channels.contracts import ChannelMessage
from knoa_platform.config import AppConfig
from knoa_platform.runtime import RuntimePaths
from knoa_platform.service.core_api import (
    ArtifactInputRef,
)
from knoa_platform.service.core_client import CoreClient
from knoa_platform.tasks import (
    TaskState,
)

logger = logging.getLogger(__name__)
_CONFIRM_TIMEOUT_SECONDS = 110.0
_MARKDOWN_IMAGE = re.compile(r"!\[([^\]\n]*)\]\([^\n)]*\)")
_STREAM_PATCH_INTERVAL_SECONDS = 0.6
_CARD_MARKDOWN_CHARS = 3500
_CARD_TABLES_PER_CHUNK = 3
_PROGRESS_REASONING_CHARS = 700
_PROGRESS_STEPS_CHARS = 1000
_PROGRESS_DRAFT_CHARS = 900
_PROGRESS_TIMELINE_CHARS = (
    _PROGRESS_REASONING_CHARS
    + _PROGRESS_STEPS_CHARS
    + _PROGRESS_DRAFT_CHARS
)
_TEXT_MESSAGE_CHARS = 4000
_MAX_CORE_ARTIFACT_RAW_BYTES = 45 * 1024 * 1024
_LONG_RESULT_CARD_CHARS = 12_000
_LONG_RESULT_PREVIEW_CHARS = 1_800
_PRINCIPAL_WATCH_RETRY_SECONDS = 2.0
_TASK_TERMINAL_EVENT_TYPES = frozenset({"completed", "failed", "cancelled"})
_TASK_STATE_LABELS = {
    TaskState.QUEUED: "排队中",
    TaskState.RUNNING: "进行中",
    TaskState.WAITING_APPROVAL: "等待确认",
    TaskState.PAUSED: "已暂停",
    TaskState.COMPLETED: "已完成",
    TaskState.FAILED: "出错",
    TaskState.CANCELLED: "已停止",
}

for _proxy_name in ("NO_PROXY", "no_proxy"):
    _configured = os.environ.get(_proxy_name, "")
    _hosts = [
        "msg-frontier.feishu.cn",
        "open.feishu.cn",
        "feishu.cn",
        "larkoffice.com",
        "127.0.0.1",
        "localhost",
    ]
    _entries = [entry for entry in _configured.split(",") if entry]
    for _host in _hosts:
        if _host not in _entries:
            _entries.append(_host)
    os.environ[_proxy_name] = ",".join(_entries)


class FeishuChannel(
    FeishuConversationMixin,
    FeishuTaskMixin,
    FeishuTransportMixin,
):
    """Translate Feishu ingress/egress at the CoreClient boundary."""

    name = "feishu"

    @staticmethod
    def message_contract(
        open_id: str,
        message_id: str,
        *,
        text: str = "",
    ) -> ChannelMessage:
        """Return the provider-neutral shape used by channel diagnostics."""
        return ChannelMessage(
            channel="feishu",
            principal_id=open_id,
            message_id=message_id,
            text=text,
        )

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._paths = RuntimePaths.from_root(config.runtime_root)
        self._app_id = config.feishu_app_id.strip()
        self._app_secret = config.feishu_app_secret.get_secret_value().strip()
        self._receive_id = config.feishu_receive_id.strip()
        self._binding_path = self._paths.data / "feishu_open_id"
        self._binding_lock = threading.RLock()
        self._sessions_path = self._paths.data / "feishu_sessions.json"
        self._notification_cursors_path = (
            self._paths.data / "feishu_notification_cursors.json"
        )
        self._notification_intent_cursors_path = (
            self._paths.data / "feishu_notification_intent_cursors.json"
        )
        self._outbox = self._paths.cache / "feishu-outbox"
        self._clients: dict[str, CoreClient] = {}
        self._client_locks: dict[str, asyncio.Lock] = {}
        self._lark_client: Any = None
        self._lark_lock = threading.RLock()
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._ws_thread: threading.Thread | None = None
        self._running = False
        self._sessions: dict[str, str] = {}
        self._session_users: dict[str, str] = {}
        self._notification_cursors: dict[str, int] = {}
        self._notification_intent_cursors: dict[str, int] = {}
        self._principal_watchers: dict[str, asyncio.Task[None]] = {}
        self._principal_watcher_started_at: dict[str, float] = {}
        self._foreground_task_ids: set[str] = set()
        self._active_chat_turn_ids: dict[str, str] = {}
        self._background_approval_decisions: dict[str, bool] = {}
        self._pending_attachments: dict[str, list[ArtifactInputRef]] = {}
        self._active_task_presentations: dict[str, _ActiveTaskPresentation] = {}
        self._active_session_presentations: dict[
            str,
            _ActiveTaskPresentation,
        ] = {}
        self._pending_confirmations: dict[str, _PendingConfirmation] = {}
        self._pending_confirmation_lock = threading.RLock()
        self._seen_messages: dict[str, float] = {}

    async def start(self) -> None:
        if self._running:
            raise RuntimeError("FeishuChannel is already started")
        if not self._app_id or not self._app_secret:
            raise ValueError("Feishu app_id and app_secret are required")
        self._main_loop = asyncio.get_running_loop()
        self._load_sessions()
        self._load_notification_cursors()
        self._load_notification_intent_cursors()
        # Import and initialize the SDK before the WebSocket thread starts.
        # Concurrent first imports from REST and WS paths can deadlock inside
        # Python's module locks in lark-oapi 1.6.x.
        await asyncio.to_thread(self._get_lark_client)
        self._running = True
        for open_id in self._sessions:
            self._ensure_principal_watcher(open_id)
        self._ws_thread = threading.Thread(
            target=self._run_websocket,
            name="knoa-feishu",
            daemon=True,
        )
        self._ws_thread.start()
        logger.info("FeishuChannel started")
        receive_id = self._current_receive_id()
        if receive_id:
            await asyncio.to_thread(
                self._send_text,
                receive_id,
                _service_notice("已启动"),
            )

    async def stop(self) -> None:
        self._running = False
        watchers, self._principal_watchers = (
            tuple(self._principal_watchers.values()),
            {},
        )
        for watcher in watchers:
            watcher.cancel()
        await asyncio.gather(*watchers, return_exceptions=True)
        self._principal_watcher_started_at.clear()
        self._foreground_task_ids.clear()
        self._active_chat_turn_ids.clear()
        self._background_approval_decisions.clear()
        receive_id = self._current_receive_id()
        if receive_id:
            try:
                await asyncio.to_thread(
                    self._send_text,
                    receive_id,
                    _service_notice("已停止"),
                )
            except Exception:
                logger.warning(
                    "Feishu shutdown notification failed",
                    exc_info=True,
                )
        with self._pending_confirmation_lock:
            pending_confirmations = tuple(self._pending_confirmations.values())
            self._pending_confirmations.clear()
            for pending in pending_confirmations:
                pending.resolved = True
        for pending in pending_confirmations:
            self._schedule_confirmation_result(pending, False)
        clients, self._clients = tuple(self._clients.values()), {}
        await asyncio.gather(
            *(client.disconnect() for client in clients),
            return_exceptions=True,
        )
        logger.info("FeishuChannel stopped")

    @property
    def running(self) -> bool:
        return self._running

    def _run_websocket(self) -> None:
        while self._running:
            event_loop: asyncio.AbstractEventLoop | None = None
            try:
                import lark_oapi.ws.client as ws_module
                from lark_oapi.ws import Client as WSClient

                event_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(event_loop)
                ws_module.loop = event_loop
                self._lark_ws_client = WSClient(
                    app_id=self._app_id,
                    app_secret=self._app_secret,
                    event_handler=self._create_event_handler(),
                    auto_reconnect=True,
                )
                _patch_ws_card_dispatch(self._lark_ws_client)
                logging.getLogger("Lark").setLevel(logging.WARNING)
                logger.info("Feishu WebSocket connecting")
                self._lark_ws_client.start()
            except Exception:
                logger.exception("Feishu WebSocket connection failed")
            finally:
                if event_loop is not None:
                    try:
                        pending = asyncio.all_tasks(event_loop)
                        for task in pending:
                            task.cancel()
                        if pending:
                            event_loop.run_until_complete(
                                asyncio.gather(*pending, return_exceptions=True)
                            )
                        event_loop.close()
                    except Exception:
                        logger.debug("Feishu WebSocket loop cleanup failed", exc_info=True)
            if self._running:
                time.sleep(3)

    def _create_event_handler(self) -> Any:
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTriggerResponse,
        )
        from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

        def on_message(event: Any) -> None:
            try:
                sender = event.event.sender
                open_id = sender.sender_id.open_id
                if not self._save_binding(open_id):
                    logger.warning(
                        "Ignored Feishu message from non-owner sender=%s",
                        _principal_for_log(open_id),
                    )
                    return
                message = event.event.message
                message_id = getattr(message, "message_id", "") or ""
                if not self._claim_message(message_id):
                    return
                content = json.loads(message.content or "{}")
                if message.message_type == "text":
                    text = str(content.get("text", "")).strip()
                    if text:
                        self._submit(self._handle_text(open_id, text, message_id))
                elif message.message_type == "image":
                    image_key = str(content.get("image_key", "")).strip()
                    if image_key:
                        self._submit(
                            self._handle_image(open_id, message_id, image_key)
                        )
                elif message.message_type == "file":
                    file_key = str(content.get("file_key", "")).strip()
                    file_name = str(content.get("file_name", "")).strip()
                    if file_key:
                        self._submit(
                            self._handle_file(
                                open_id,
                                message_id,
                                file_key,
                                file_name or "attachment.bin",
                            )
                        )
                elif message.message_type == "audio":
                    file_key = str(content.get("file_key", "")).strip()
                    if file_key:
                        self._submit(
                            self._handle_audio(
                                open_id,
                                message_id,
                                file_key,
                            )
                        )
            except Exception:
                logger.exception("Feishu inbound message handling failed")

        def on_activity(_event: Any) -> None:
            return None

        def on_card_action(event: Any) -> Any:
            try:
                action = event.event.action
                value = action.value or {}
                if isinstance(value, str):
                    value = json.loads(value)
                if not isinstance(value, dict):
                    value = {}
                operator = event.event.operator
                open_id = operator.open_id if operator is not None else ""
                if not self._save_binding(open_id):
                    logger.warning(
                        "Ignored Feishu card action from non-owner sender=%s",
                        _principal_for_log(open_id),
                    )
                    return P2CardActionTriggerResponse(
                        {
                            "toast": {
                                "type": "warning",
                                "content": "无权操作",
                            }
                        }
                    )
                action_name = str(value.get("action", ""))
                resource_id = str(value.get("resource_id", ""))
                approval_id = str(value.get("approval_id", ""))
                if action_name not in {"confirm", "cancel"}:
                    raise ValueError("unknown confirmation action")
                pending = self._resolve_confirmation(
                    open_id,
                    approval_id,
                    action_name == "confirm",
                    resource_id=resource_id,
                )
                if pending is None:
                    return P2CardActionTriggerResponse(
                        {
                            "toast": {
                                "type": "warning",
                                "content": "操作已处理或已过期",
                            }
                        }
                    )
                approved = action_name == "confirm"
                return P2CardActionTriggerResponse(
                    {
                        "toast": {
                            "type": "success" if approved else "info",
                            "content": "已确认" if approved else "已取消",
                        },
                    }
                )
            except Exception:
                logger.exception("Feishu confirmation callback failed")
                return P2CardActionTriggerResponse(
                    {
                        "toast": {
                            "type": "error",
                            "content": "处理失败",
                        }
                    }
                )

        return (
            EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(on_message)
            .register_p2_card_action_trigger(on_card_action)
            .register_p2_im_message_message_read_v1(on_activity)
            .register_p2_im_message_reaction_created_v1(on_activity)
            .register_p2_im_message_reaction_deleted_v1(on_activity)
            .register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(on_activity)
            .build()
        )
