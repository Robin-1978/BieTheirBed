"""Feishu adapter that speaks only the public Core WebSocket API."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import mimetypes
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pc_assistant import __version__
from pc_assistant.branding import ASSISTANT_NAME
from pc_assistant.config import AppConfig
from pc_assistant.runtime import RuntimePaths
from pc_assistant.service.core_api import (
    ArtifactInputRef,
    ConfirmationRequestedMessage,
)
from pc_assistant.service.core_client import CoreClient, CoreRequestError
from pc_assistant.service.credentials import (
    issue_principal_credential,
    resolve_local_service_token,
)


logger = logging.getLogger(__name__)
_CONFIRM_TIMEOUT_SECONDS = 110.0
_MARKDOWN_IMAGE = re.compile(r"!\[([^\]\n]*)\]\([^\n)]*\)")
_STREAM_PATCH_INTERVAL_SECONDS = 0.6
_CARD_MARKDOWN_CHARS = 3500
_PROGRESS_REASONING_CHARS = 700
_PROGRESS_STEPS_CHARS = 1000
_PROGRESS_DRAFT_CHARS = 900
_PROGRESS_TIMELINE_CHARS = (
    _PROGRESS_REASONING_CHARS
    + _PROGRESS_STEPS_CHARS
    + _PROGRESS_DRAFT_CHARS
)
_TEXT_MESSAGE_CHARS = 4000

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


def _principal_for_log(open_id: str) -> str:
    if not open_id:
        return "unknown"
    return hashlib.sha256(open_id.encode("utf-8")).hexdigest()[:10]


def _service_notice(state: str) -> str:
    return f"{ASSISTANT_NAME} v{__version__} {state}"


def _render_card_markdown(text: str) -> str:
    """Remove image references that Feishu cards cannot render directly.

    Core artifacts are delivered through the Channel's image/file upload path.
    Leaving an ``attachment://`` or internal artifact URL in Markdown makes
    Feishu interpret that URL as a platform image key and reject the whole card.
    """

    def replace_image(match: re.Match[str]) -> str:
        label = match.group(1).strip() or "图片"
        return f"图片：{label}（见附件）"

    return _MARKDOWN_IMAGE.sub(replace_image, text)


def _render_muted_card_markdown(text: str) -> str:
    """Render channel-owned secondary copy in Feishu's muted text color."""
    rendered = _render_card_markdown(text)
    escaped = rendered.replace("<", "&lt;").replace(">", "&gt;")
    return f"<font color='grey'>{escaped}</font>"


def _split_text(text: str, limit: int = _CARD_MARKDOWN_CHARS) -> tuple[str, ...]:
    """Split transport payloads without dropping any model output."""
    if limit < 1:
        raise ValueError("Text chunk limit must be positive")
    if not text:
        return ("",)
    chunks: list[str] = []
    offset = 0
    while offset < len(text):
        end = min(len(text), offset + limit)
        if end < len(text):
            window = text[offset:end]
            candidates = (
                window.rfind("\n\n"),
                window.rfind("\n"),
                window.rfind(" "),
            )
            boundary = max(candidates)
            if boundary >= limit // 2:
                end = offset + boundary + 1
        chunks.append(text[offset:end])
        offset = end
    return tuple(chunks)


def _tail(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return "…\n" + text[-max(1, limit - 2) :]


def _brief_json(value: Any, limit: int = 240) -> str:
    rendered = json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
    if len(rendered) <= limit:
        return rendered
    return rendered[: max(1, limit - 1)] + "…"


def _summarize_tool_result(result: Any) -> str:
    if not isinstance(result, dict):
        return _tail(str(result), 220) if result is not None else "完成"
    status = str(result.get("status", ""))
    if status and status != "completed":
        detail = result.get("message") or result.get("code") or status
        return f"{status}: {_tail(str(detail), 180)}"
    output = result.get("output")
    if isinstance(output, dict):
        if output.get("success") is False:
            detail = output.get("message") or output.get("error") or "执行失败"
            return _tail(str(detail), 180)
        if output.get("artifact"):
            artifact = output["artifact"]
            if isinstance(artifact, dict):
                return f"已生成 {artifact.get('name') or '附件'}"
        return "完成"
    return _tail(str(output), 220) if output not in (None, "") else "完成"


def _tool_result_failed(result: Any, *, blocked: bool) -> bool:
    if blocked:
        return True
    if not isinstance(result, dict):
        return False
    if str(result.get("status", "")).lower() in {
        "failed",
        "rejected",
        "not_executed",
        "error",
    }:
        return True
    output = result.get("output")
    return isinstance(output, dict) and output.get("success") is False


@dataclass
class _ToolStep:
    call_id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    status: str = "running"
    detail: str = ""
    iteration: int = 0
    confirmation_id: str = ""
    confirmation_run_id: str = ""
    confirmation_reason: str = ""
    confirmation_status: str = ""


@dataclass
class _ThoughtStep:
    source: str
    content: str
    iteration: int = 0


@dataclass
class _PendingConfirmation:
    confirmation_id: str
    future: asyncio.Future[bool]
    loop: asyncio.AbstractEventLoop
    message: ConfirmationRequestedMessage
    presentation: _ActiveRunPresentation
    resolved: bool = False


def _patch_ws_card_dispatch(ws_client: Any) -> None:
    """Route CARD frames dropped by lark_oapi through its callback handler."""
    if getattr(ws_client, "_pc_assistant_card_dispatch_patched", False):
        return
    try:
        import http
        import types

        from lark_oapi import JSON as LarkJSON
        from lark_oapi.ws.const import (
            HEADER_BIZ_RT,
            HEADER_MESSAGE_ID,
            HEADER_SEQ,
            HEADER_SUM,
            HEADER_TYPE,
        )
        from lark_oapi.ws.enum import MessageType
        from lark_oapi.ws.model import Response

        original = ws_client._handle_data_frame

        def header_value(headers: Any, key: str, default: str = "") -> str:
            for header in headers:
                if header.key == key:
                    return header.value
            return default

        async def patched(self: Any, frame: Any) -> None:
            headers = frame.headers
            frame_type = header_value(headers, HEADER_TYPE)
            if not frame_type or MessageType(frame_type) != MessageType.CARD:
                await original(frame)
                return

            payload = frame.payload
            message_id = header_value(headers, HEADER_MESSAGE_ID)
            total = int(header_value(headers, HEADER_SUM, "1") or "1")
            sequence = int(header_value(headers, HEADER_SEQ, "1") or "1")
            if total > 1:
                if hasattr(self, "_combine"):
                    payload = self._combine(
                        message_id,
                        total,
                        sequence,
                        payload,
                    )
                elif getattr(self, "_cache", None) is not None:
                    payload = self._cache.merge(
                        message_id,
                        total,
                        sequence,
                        payload,
                    )
                if payload is None:
                    return

            response = Response(code=http.HTTPStatus.OK)
            try:
                started = int(round(time.time() * 1000))
                handler = self._event_handler
                dispatch = getattr(handler, "do_without_validation", None)
                if dispatch is None:
                    dispatch = getattr(handler, "_do_without_validation")
                result = dispatch(payload)
                header = headers.add()
                header.key = HEADER_BIZ_RT
                header.value = str(int(round(time.time() * 1000)) - started)
                if result is not None:
                    response.data = base64.b64encode(
                        LarkJSON.marshal(result).encode("utf-8")
                    )
            except Exception:
                logger.exception("Feishu CARD callback dispatch failed")
                response = Response(code=http.HTTPStatus.INTERNAL_SERVER_ERROR)

            frame.payload = LarkJSON.marshal(response).encode("utf-8")
            await self._write_message(frame.SerializeToString())

        ws_client._handle_data_frame = types.MethodType(patched, ws_client)
        ws_client._pc_assistant_card_dispatch_patched = True
        logger.info("Feishu CARD callback dispatch patch applied")
    except Exception:
        logger.exception("Feishu CARD callback dispatch patch failed")


class _StreamingCardState:
    """Channel-local projection of standard Core run events."""

    def __init__(self) -> None:
        self.timeline: list[_ThoughtStep | _ToolStep] = []
        self.final_output = ""
        self.error = ""
        self.phase = "thinking"

    def _append_thought(
        self,
        source: str,
        content: str,
        iteration: int,
    ) -> None:
        if not content:
            return
        if self.timeline:
            current = self.timeline[-1]
            if (
                isinstance(current, _ThoughtStep)
                and current.source == source
                and current.iteration == iteration
            ):
                current.content += content
                return
        self.timeline.append(
            _ThoughtStep(
                source=source,
                content=content,
                iteration=iteration,
            )
        )

    def append_reasoning(self, content: str, *, iteration: int = 0) -> None:
        self._append_thought("reasoning", content, iteration)

    def append_draft(self, content: str, *, iteration: int = 0) -> None:
        self._append_thought("content", content, iteration)

    def set_final_output(self, content: str, *, iteration: int = 0) -> None:
        self.final_output = content
        self.phase = "done"
        if not content or not self.timeline:
            return
        current = self.timeline[-1]
        if not (
            isinstance(current, _ThoughtStep)
            and current.source == "content"
            and current.iteration == iteration
        ):
            return
        if current.content == content:
            self.timeline.pop()
        elif current.content.endswith(content):
            current.content = current.content[: -len(content)].rstrip()
            if not current.content:
                self.timeline.pop()

    def add_tool_call(
        self,
        call_id: str,
        name: str,
        arguments: dict[str, Any],
        *,
        iteration: int = 0,
    ) -> None:
        self.phase = "working"
        existing = next(
            (
                candidate
                for candidate in reversed(self.timeline)
                if isinstance(candidate, _ToolStep)
                and candidate.call_id == call_id
                and candidate.status == "running"
            ),
            None,
        )
        if existing is not None:
            existing.name = name
            existing.arguments = arguments
            existing.iteration = iteration
            return
        self.timeline.append(
            _ToolStep(
                call_id=call_id,
                name=name,
                arguments=arguments,
                iteration=iteration,
            )
        )

    def add_tool_result(
        self,
        call_id: str,
        name: str,
        result: Any,
        *,
        blocked: bool,
        iteration: int = 0,
    ) -> None:
        step = next(
            (
                candidate
                for candidate in reversed(self.timeline)
                if isinstance(candidate, _ToolStep)
                and (
                    candidate.call_id == call_id
                    or (not call_id and candidate.name == name)
                )
                and candidate.status == "running"
            ),
            None,
        )
        if step is None:
            step = _ToolStep(
                call_id=call_id,
                name=name,
                iteration=iteration,
            )
            self.timeline.append(step)
        failed = _tool_result_failed(result, blocked=blocked)
        step.status = "failed" if failed else "completed"
        summary = _summarize_tool_result(result)
        step.detail = summary if failed or summary != "完成" else ""

    def request_confirmation(
        self,
        message: ConfirmationRequestedMessage,
    ) -> bool:
        step = next(
            (
                candidate
                for candidate in reversed(self.timeline)
                if isinstance(candidate, _ToolStep)
                and candidate.call_id == message.tool_call_id
                and candidate.status == "running"
            ),
            None,
        )
        if step is None:
            step = _ToolStep(
                call_id=message.tool_call_id,
                name=message.tool_name,
                arguments=message.arguments,
            )
            self.timeline.append(step)
        step.confirmation_id = message.confirmation_id
        step.confirmation_run_id = message.run_id
        step.confirmation_reason = (
            message.reason or "该操作可能改变系统状态"
        )
        step.confirmation_status = "pending"
        self.phase = "working"
        return True

    def resolve_confirmation(
        self,
        confirmation_id: str,
        status: str,
    ) -> bool:
        step = next(
            (
                candidate
                for candidate in reversed(self.timeline)
                if isinstance(candidate, _ToolStep)
                and candidate.confirmation_id == confirmation_id
            ),
            None,
        )
        if step is None:
            return False
        step.confirmation_status = status
        return True

    def _pending_confirmation(self) -> _ToolStep | None:
        return next(
            (
                candidate
                for candidate in reversed(self.timeline)
                if isinstance(candidate, _ToolStep)
                and candidate.confirmation_status == "pending"
            ),
            None,
        )

    @staticmethod
    def _render_tool(step: _ToolStep) -> str:
        if step.status == "running":
            if step.confirmation_status in {"cancelled", "expired"}:
                suffix = "已取消" if step.confirmation_status == "cancelled" else "已过期"
                return f"× `{step.name}` · {suffix}"
            line = f"… `{step.name}`"
            if step.confirmation_status == "confirmed":
                line += " · 已确认"
            if step.arguments:
                line += f"\n`{_brief_json(step.arguments)}`"
        elif step.status == "failed":
            line = f"× `{step.name}`"
            if step.detail:
                line += f" — {step.detail}"
        else:
            line = f"✓ `{step.name}`"
            if step.detail:
                line += f" — {step.detail}"
        return line

    def _render_timeline(self) -> str:
        parts: list[str] = []
        for entry in self.timeline:
            if isinstance(entry, _ThoughtStep):
                limit = (
                    _PROGRESS_DRAFT_CHARS
                    if entry.source == "content"
                    else _PROGRESS_REASONING_CHARS
                )
                text = _tail(entry.content.strip(), limit)
                if not text:
                    continue
                prefix = "" if entry.source == "notice" else "› "
                parts.append(_render_muted_card_markdown(prefix + text))
            else:
                parts.append(self._render_tool(entry))

        selected: list[str] = []
        total = 0
        truncated = False
        for part in reversed(parts):
            added = len(part) + (2 if selected else 0)
            if selected and total + added > _PROGRESS_TIMELINE_CHARS:
                truncated = True
                break
            selected.append(part)
            total += added
        selected.reverse()
        if truncated:
            selected.insert(0, _render_muted_card_markdown("…"))
        return "\n\n".join(selected)

    @staticmethod
    def _confirmation_elements(step: _ToolStep) -> list[dict[str, Any]]:
        return [
            {
                "tag": "markdown",
                "content": _render_muted_card_markdown(
                    f"需要确认 · {step.confirmation_reason}"
                ),
            },
            {
                "tag": "column_set",
                "horizontal_spacing": "8px",
                "columns": [
                    {
                        "tag": "column",
                        "width": "auto",
                        "elements": [
                            {
                                "tag": "button",
                                "name": "pc_assistant_confirm",
                                "type": "primary",
                                "text": {
                                    "tag": "plain_text",
                                    "content": "确认",
                                },
                                "behaviors": [
                                    {
                                        "type": "callback",
                                        "value": {
                                            "action": "confirm",
                                            "run_id": step.confirmation_run_id,
                                            "confirmation_id": step.confirmation_id,
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "tag": "column",
                        "width": "auto",
                        "elements": [
                            {
                                "tag": "button",
                                "name": "pc_assistant_cancel",
                                "type": "default",
                                "text": {
                                    "tag": "plain_text",
                                    "content": "取消",
                                },
                                "behaviors": [
                                    {
                                        "type": "callback",
                                        "value": {
                                            "action": "cancel",
                                            "run_id": step.confirmation_run_id,
                                            "confirmation_id": step.confirmation_id,
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                ],
            },
        ]

    def add_notice(self, content: str) -> None:
        if content:
            self._append_thought("notice", f"· {_tail(content, 220)}", 0)

    def set_error(self, content: str) -> None:
        self.error = content or "处理失败"
        self.phase = "error"

    def set_cancelled(self) -> None:
        self.error = ""
        self.phase = "cancelled"

    def build_card(
        self,
        *,
        final_chunk: str | None = None,
    ) -> dict[str, Any]:
        elements: list[dict[str, Any]] = []
        timeline = self._render_timeline()
        pending_confirmation = self._pending_confirmation()
        if timeline:
            elements.append(
                {
                    "tag": "markdown",
                    "content": timeline,
                }
            )
        if pending_confirmation is not None:
            elements.extend(
                self._confirmation_elements(pending_confirmation)
            )
        if final_chunk is not None:
            if elements:
                elements.append({"tag": "hr"})
            elements.append(
                {
                    "tag": "markdown",
                    "content": final_chunk or "已完成",
                }
            )
        elif self.error:
            if elements:
                elements.append({"tag": "hr"})
            elements.append(
                {"tag": "markdown", "content": f"× {self.error}"}
            )
        elif self.phase == "cancelled":
            if elements:
                elements.append({"tag": "hr"})
            elements.append({"tag": "markdown", "content": "已停止"})
        elif not timeline:
            status = "正在调用工具…" if self.phase == "working" else "正在思考…"
            elements.append(
                {
                    "tag": "markdown",
                    "content": _render_muted_card_markdown(status),
                }
            )

        if self.phase == "error":
            template, title = "red", "处理出错"
        elif self.phase == "cancelled":
            template, title = "grey", "已停止"
        elif final_chunk is not None:
            template, title = "blue", ASSISTANT_NAME
        elif pending_confirmation is not None:
            template, title = "orange", f"{ASSISTANT_NAME} · 等待确认"
        else:
            template, title = "turquoise", f"{ASSISTANT_NAME} · 处理中"
        return {
            "schema": "2.0",
            "header": {
                "template": template,
                "title": {"tag": "plain_text", "content": title},
            },
            "body": {"elements": elements},
        }


@dataclass
class _ActiveRunPresentation:
    session_handle: str
    state: _StreamingCardState
    update_requested: asyncio.Event
    run_id: str = ""

    def bind_run(self, run_id: str) -> None:
        if not self.run_id:
            self.run_id = run_id

    def request_confirmation(
        self,
        message: ConfirmationRequestedMessage,
    ) -> bool:
        attached = self.state.request_confirmation(message)
        if attached:
            self.update_requested.set()
        return attached

    def resolve_confirmation(self, confirmation_id: str, status: str) -> None:
        if self.state.resolve_confirmation(confirmation_id, status):
            self.update_requested.set()


class FeishuChannel:
    """Translate Feishu ingress/egress at the CoreClient boundary."""

    name = "feishu"

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._paths = RuntimePaths.from_root(config.runtime_root)
        self._app_id = config.feishu_app_id.strip()
        self._app_secret = config.feishu_app_secret.get_secret_value().strip()
        self._receive_id = config.feishu_receive_id.strip()
        self._binding_path = self._paths.data / "feishu_open_id"
        self._sessions_path = self._paths.data / "feishu_sessions.json"
        self._outbox = self._paths.cache / "feishu-outbox"
        self._clients: dict[str, CoreClient] = {}
        self._lark_client: Any = None
        self._lark_lock = threading.RLock()
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._ws_thread: threading.Thread | None = None
        self._running = False
        self._sessions: dict[str, str] = {}
        self._session_users: dict[str, str] = {}
        self._user_locks: dict[str, asyncio.Lock] = {}
        self._pending_attachments: dict[str, list[ArtifactInputRef]] = {}
        self._active_run_presentations: dict[str, _ActiveRunPresentation] = {}
        self._active_session_presentations: dict[
            str,
            _ActiveRunPresentation,
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
        # Import and initialize the SDK before the WebSocket thread starts.
        # Concurrent first imports from REST and WS paths can deadlock inside
        # Python's module locks in lark-oapi 1.6.x.
        await asyncio.to_thread(self._get_lark_client)
        self._running = True
        self._ws_thread = threading.Thread(
            target=self._run_websocket,
            name="pc-assistant-feishu",
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
                action_name = str(value.get("action", ""))
                run_id = str(value.get("run_id", ""))
                confirmation_id = str(value.get("confirmation_id", ""))
                if action_name not in {"confirm", "cancel"}:
                    raise ValueError("unknown confirmation action")
                pending = self._resolve_confirmation(
                    open_id,
                    confirmation_id,
                    action_name == "confirm",
                    run_id=run_id,
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

    def _submit(self, coroutine: Any) -> None:
        loop = self._main_loop
        if loop is None or not self._running:
            coroutine.close()
            return
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)

        def report_failure(done: Any) -> None:
            try:
                done.result()
            except Exception:
                logger.exception("Feishu scheduled operation failed")

        future.add_done_callback(report_failure)

    @staticmethod
    def _set_confirmation_result(
        pending: _PendingConfirmation,
        approved: bool,
        status: str,
    ) -> None:
        pending.presentation.resolve_confirmation(
            pending.confirmation_id,
            status,
        )
        if not pending.future.done():
            pending.future.set_result(approved)

    def _schedule_confirmation_result(
        self,
        pending: _PendingConfirmation,
        approved: bool,
        *,
        status: str | None = None,
    ) -> None:
        pending.loop.call_soon_threadsafe(
            self._set_confirmation_result,
            pending,
            approved,
            status or ("confirmed" if approved else "cancelled"),
        )

    def _resolve_confirmation(
        self,
        open_id: str,
        confirmation_id: str,
        approved: bool,
        *,
        run_id: str = "",
    ) -> _PendingConfirmation | None:
        with self._pending_confirmation_lock:
            pending = self._pending_confirmations.get(open_id)
            if (
                pending is None
                or pending.resolved
                or pending.confirmation_id != confirmation_id
                or (run_id and pending.message.run_id != run_id)
            ):
                return None
            pending.resolved = True
        self._schedule_confirmation_result(pending, approved)
        return pending

    async def _handle_text(
        self,
        open_id: str,
        text: str,
        message_id: str = "",
    ) -> None:
        reaction_id = await asyncio.to_thread(
            self._add_reaction,
            message_id,
            "Typing",
        )
        try:
            self._save_binding(open_id)
            normalized = text.strip().lower()
            if normalized in {"/stop", "/cancel"}:
                await self._cancel_active_run(open_id)
                return
            with self._pending_confirmation_lock:
                confirmation = self._pending_confirmations.get(open_id)
            if confirmation is not None and not confirmation.resolved:
                if normalized in {"确认", "批准", "yes", "y", "ok"}:
                    self._resolve_confirmation(
                        open_id,
                        confirmation.confirmation_id,
                        True,
                    )
                    await asyncio.to_thread(
                        self._send_text,
                        open_id,
                        "已确认",
                    )
                    return
                if normalized in {"取消", "拒绝", "no", "n"}:
                    self._resolve_confirmation(
                        open_id,
                        confirmation.confirmation_id,
                        False,
                    )
                    await asyncio.to_thread(
                        self._send_text,
                        open_id,
                        "已取消",
                    )
                    return
                await asyncio.to_thread(
                    self._send_text,
                    open_id,
                    "当前有操作等待确认，请在卡片中选择“确认”或“取消”。",
                )
                return

            lock = self._user_locks.setdefault(open_id, asyncio.Lock())
            async with lock:
                try:
                    await self._run_text(open_id, text)
                except Exception as exc:
                    logger.exception(
                        "Feishu Core run failed principal=%s",
                        _principal_for_log(open_id),
                    )
                    await asyncio.to_thread(
                    self._send_text,
                    open_id,
                    f"处理失败：{type(exc).__name__}",
                )
        finally:
            await asyncio.to_thread(
                self._remove_reaction,
                message_id,
                reaction_id,
            )

    async def _cancel_active_run(self, open_id: str) -> None:
        client = self._clients.get(open_id)
        if client is None or not client.is_connected:
            await asyncio.to_thread(
                self._send_text,
                open_id,
                "当前没有正在运行的任务。",
            )
            return
        try:
            result = await client.cancel_active()
        except Exception:
            logger.exception(
                "Feishu Core cancellation failed principal=%s",
                _principal_for_log(open_id),
            )
            await asyncio.to_thread(
                self._send_text,
                open_id,
                "停止失败，请稍后重试。",
            )
            return
        if result is None or result.result.status == "not_found":
            message = "当前没有正在运行的任务。"
        elif result.result.accepted:
            message = "正在停止当前任务。"
        else:
            message = "当前任务已经结束。"
        await asyncio.to_thread(self._send_text, open_id, message)

    async def _run_text(self, open_id: str, text: str) -> None:
        client = await self._client_for(open_id)
        if text.strip().lower() == "/new":
            session = await client.create_session()
            self._bind_session(open_id, session)
            await asyncio.to_thread(self._send_text, open_id, "已创建新会话")
            return

        session = await self._session_for(open_id)
        if text.strip().lower() == "/status":
            status = await client.status(session)
            details = status.details
            prompt_tokens = int(details.get("prompt_tokens") or 0)
            completion_tokens = int(details.get("completion_tokens") or 0)
            total_tokens = int(details.get("total_tokens") or 0)
            cached_tokens = int(details.get("cached_tokens") or 0)
            lines = [
                f"模型：`{details.get('model') or '未知'}`"
                f"（{details.get('provider') or '未知'}）",
                f"连接：{'正常' if status.connected else '断开'}",
                "",
                f"输入：{prompt_tokens:,} tokens",
                f"输出：{completion_tokens:,} tokens",
                f"合计：{total_tokens:,} tokens",
            ]
            if cached_tokens:
                lines.append(f"缓存命中：{cached_tokens:,} tokens")
            lines.extend(
                [
                    "",
                    f"对话轮次：{int(details.get('turns') or 0):,}",
                    f"模型调用：{int(details.get('model_calls') or 0):,}",
                    f"工具调用：{int(details.get('tool_calls') or 0):,}",
                    f"消息：{int(details.get('messages') or 0):,}",
                    f"会话：{int(details.get('sessions') or 0):,}",
                    f"可用工具：{int(details.get('available_tools') or 0):,}",
                ]
            )
            await asyncio.to_thread(
                self._send_card,
                open_id,
                "\n".join(lines),
                "blue",
                "状态",
            )
            return
        if text.strip().lower() == "/tools":
            tools = await client.list_tools(session)
            await asyncio.to_thread(
                self._send_text,
                open_id,
                "可用工具：\n" + "、".join(tools.tools),
            )
            return

        attachments = tuple(self._pending_attachments.pop(open_id, []))
        try:
            await self._stream_core_run(
                open_id,
                client,
                session,
                text,
                attachments,
            )
        except CoreRequestError as exc:
            if exc.code != "session_not_found":
                raise
            session = await client.create_session()
            self._bind_session(open_id, session)
            await self._stream_core_run(
                open_id,
                client,
                session,
                text,
                attachments,
            )

    async def _stream_core_run(
        self,
        open_id: str,
        client: CoreClient,
        session: str,
        text: str,
        attachments: tuple[ArtifactInputRef, ...],
    ) -> None:
        state = _StreamingCardState()
        card_task: asyncio.Task[str | None] | None = None
        update_requested = asyncio.Event()
        presentation = _ActiveRunPresentation(
            session_handle=session,
            state=state,
            update_requested=update_requested,
        )
        self._active_session_presentations[session] = presentation
        stop_updates = False
        last_patch = 0.0
        terminal = ""
        artifacts: list[str] = []
        artifact_ids: set[str] = set()

        async def create_initial_card(card: dict[str, Any]) -> str | None:
            try:
                return await asyncio.to_thread(
                    self._send_card_returning_id,
                    open_id,
                    card,
                )
            except Exception:
                logger.exception("Feishu initial streaming card failed")
                return None

        def start_card() -> None:
            nonlocal card_task
            if card_task is None:
                initial = state.build_card()
                card_task = asyncio.create_task(
                    create_initial_card(initial)
                )

        async def update_worker() -> None:
            nonlocal last_patch
            while True:
                await update_requested.wait()
                update_requested.clear()
                if stop_updates:
                    return
                delay = _STREAM_PATCH_INTERVAL_SECONDS - (
                    time.monotonic() - last_patch
                )
                if delay > 0:
                    await asyncio.sleep(delay)
                if stop_updates:
                    return
                task = card_task
                if task is None:
                    continue
                message_id = await task
                if message_id is None:
                    continue
                snapshot = state.build_card()
                await asyncio.to_thread(
                    self._update_card,
                    message_id,
                    snapshot,
                )
                last_patch = time.monotonic()

        updater = asyncio.create_task(update_worker())
        try:
            async for event in client.run(session, text, attachments):
                presentation.bind_run(event.run_id)
                self._active_run_presentations[event.run_id] = presentation
                start_card()
                payload = event.payload
                if event.event_type == "reasoning_delta":
                    state.append_reasoning(
                        payload.content,
                        iteration=payload.iteration,
                    )
                    update_requested.set()
                elif event.event_type == "content_delta":
                    state.append_draft(
                        payload.content,
                        iteration=payload.iteration,
                    )
                    update_requested.set()
                elif event.event_type == "final_output":
                    state.set_final_output(
                        payload.content,
                        iteration=payload.iteration,
                    )
                elif event.event_type == "tool_call":
                    state.add_tool_call(
                        payload.tool_call_id,
                        payload.tool_name,
                        payload.tool_args,
                        iteration=payload.iteration,
                    )
                    update_requested.set()
                elif event.event_type == "tool_result":
                    state.add_tool_result(
                        payload.tool_call_id,
                        payload.tool_name,
                        payload.tool_result,
                        blocked=payload.blocked,
                        iteration=payload.iteration,
                    )
                    update_requested.set()
                elif event.event_type == "artifact" and payload.artifact:
                    artifact_id = payload.artifact.artifact_id
                    if artifact_id not in artifact_ids:
                        artifact_ids.add(artifact_id)
                        artifacts.append(artifact_id)
                elif event.event_type == "context_compacted":
                    state.add_notice("较早对话已整理为简短工作摘要。")
                    update_requested.set()
                elif event.event_type in {"plan", "warning"}:
                    state.add_notice(payload.content)
                    update_requested.set()
                elif event.event_type == "failed":
                    terminal = "failed"
                    state.set_error(payload.content or event.event_type)
                elif event.event_type == "cancelled":
                    terminal = "cancelled"
                    state.set_cancelled()
                elif event.event_type == "completed":
                    terminal = "completed"
        finally:
            stop_updates = True
            update_requested.set()
            await updater
            if presentation.run_id:
                self._active_run_presentations.pop(
                    presentation.run_id,
                    None,
                )
            if self._active_session_presentations.get(session) is presentation:
                self._active_session_presentations.pop(session, None)

        card_message_id = (
            await card_task if card_task is not None else None
        )

        if terminal == "completed":
            if state.phase != "done":
                raise RuntimeError("Core completed without final_output event")
            rendered = _render_card_markdown(
                state.final_output if state.final_output else "已完成"
            )
            chunks = _split_text(rendered)
            final_card = state.build_card(final_chunk=chunks[0])
            if card_message_id is not None:
                updated = await asyncio.to_thread(
                    self._update_card,
                    card_message_id,
                    final_card,
                )
            else:
                updated = False
            if not updated:
                await asyncio.to_thread(
                    self._send_card,
                    open_id,
                    rendered,
                )
            else:
                total = len(chunks)
                for index, chunk in enumerate(chunks[1:], start=2):
                    await asyncio.to_thread(
                        self._send_card,
                        open_id,
                        chunk,
                        "blue",
                        f"{ASSISTANT_NAME}（续 {index}/{total}）",
                    )
        else:
            if not terminal:
                state.set_error("Core run ended without a terminal event")
            final_card = state.build_card()
            if card_message_id is None or not await asyncio.to_thread(
                self._update_card,
                card_message_id,
                final_card,
            ):
                await asyncio.to_thread(
                    self._send_card,
                    open_id,
                    "已停止" if terminal == "cancelled" else f"× {state.error or terminal}",
                    "grey" if terminal == "cancelled" else "red",
                    "已停止" if terminal == "cancelled" else "处理出错",
                )

        delivery_failures: list[str] = []
        for artifact_id in artifacts:
            try:
                await self._deliver_artifact(open_id, session, artifact_id)
            except Exception:
                logger.exception("Feishu artifact delivery failed: %s", artifact_id)
                delivery_failures.append(artifact_id)
        if delivery_failures:
            await asyncio.to_thread(
                self._send_text,
                open_id,
                "附件交付失败：" + "、".join(delivery_failures),
            )

    async def _handle_image(
        self,
        open_id: str,
        message_id: str,
        image_key: str,
    ) -> None:
        reaction_id = await asyncio.to_thread(
            self._add_reaction,
            message_id,
            "Typing",
        )
        try:
            self._save_binding(open_id)
            data, media_type = await asyncio.to_thread(
                self._download_image,
                message_id,
                image_key,
            )
            session = await self._session_for(open_id)
            encoded = base64.b64encode(data).decode("ascii")
            client = await self._client_for(open_id)
            artifact = await client.upload_artifact(
                session,
                f"data:{media_type};base64,{encoded}",
                media_type=media_type,
                caption="Feishu image",
            )
            pending = self._pending_attachments.setdefault(open_id, [])
            pending.append(
                ArtifactInputRef(
                    artifact_id=artifact.artifact_id,
                    caption="Feishu image",
                )
            )
            self._pending_attachments[open_id] = pending[-4:]
            await asyncio.to_thread(
                self._send_text,
                open_id,
                "图片已收到，请继续发送问题。",
            )
        except Exception:
            logger.exception(
                "Feishu image ingress failed principal=%s",
                _principal_for_log(open_id),
            )
            await asyncio.to_thread(self._send_text, open_id, "图片接收失败")
        finally:
            await asyncio.to_thread(
                self._remove_reaction,
                message_id,
                reaction_id,
            )

    async def _confirm_tool(
        self,
        open_id: str,
        message: ConfirmationRequestedMessage,
    ) -> bool:
        if self._session_users.get(message.session_handle) != open_id:
            return False
        presentation = self._active_run_presentations.get(message.run_id)
        if presentation is None:
            presentation = self._active_session_presentations.get(
                message.session_handle
            )
        if (
            presentation is None
            or (
                presentation.run_id
                and presentation.run_id != message.run_id
            )
            or not presentation.request_confirmation(message)
        ):
            return False
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        pending = _PendingConfirmation(
            confirmation_id=message.confirmation_id,
            future=future,
            loop=loop,
            message=message,
            presentation=presentation,
        )
        with self._pending_confirmation_lock:
            current = self._pending_confirmations.get(open_id)
            if current is not None and not current.resolved:
                presentation.resolve_confirmation(
                    message.confirmation_id,
                    "cancelled",
                )
                return False
            self._pending_confirmations[open_id] = pending
        try:
            return await asyncio.wait_for(future, timeout=_CONFIRM_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            with self._pending_confirmation_lock:
                pending.resolved = True
            presentation.resolve_confirmation(
                message.confirmation_id,
                "expired",
            )
            return False
        finally:
            with self._pending_confirmation_lock:
                if self._pending_confirmations.get(open_id) is pending:
                    self._pending_confirmations.pop(open_id, None)

    async def _session_for(self, open_id: str) -> str:
        session = self._sessions.get(open_id)
        if session:
            self._session_users[session] = open_id
            return session
        session = await (await self._client_for(open_id)).create_session()
        self._bind_session(open_id, session)
        return session

    def _bind_session(self, open_id: str, session: str) -> None:
        previous = self._sessions.get(open_id)
        if previous:
            self._session_users.pop(previous, None)
        self._sessions[open_id] = session
        self._session_users[session] = open_id
        self._save_sessions()

    def _load_sessions(self) -> None:
        try:
            data = json.loads(self._sessions_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._sessions = {
                    str(key): str(value)
                    for key, value in data.items()
                    if str(key) and str(value)
                }
                self._session_users = {
                    session: open_id for open_id, session in self._sessions.items()
                }
        except FileNotFoundError:
            return
        except Exception:
            logger.warning("Ignoring invalid Feishu session mapping", exc_info=True)

    def _save_sessions(self) -> None:
        self._sessions_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._sessions_path.parent.chmod(0o700)
        temporary = self._sessions_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(self._sessions, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(self._sessions_path)
        self._sessions_path.chmod(0o600)

    def _claim_message(self, message_id: str) -> bool:
        if not message_id:
            return True
        now = time.time()
        if message_id in self._seen_messages:
            return False
        self._seen_messages[message_id] = now
        if len(self._seen_messages) > 1000:
            self._seen_messages = {
                key: seen_at
                for key, seen_at in self._seen_messages.items()
                if now - seen_at < 600
            }
        return True

    def _save_binding(self, open_id: str) -> None:
        if not open_id:
            return
        self._receive_id = open_id
        self._binding_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._binding_path.write_text(open_id, encoding="utf-8")
        self._binding_path.chmod(0o600)

    def _current_receive_id(self) -> str:
        if self._receive_id:
            return self._receive_id
        try:
            return self._binding_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _get_lark_client(self) -> Any:
        if self._lark_client is None:
            with self._lark_lock:
                if self._lark_client is None:
                    from lark_oapi import Client

                    self._lark_client = (
                        Client.builder()
                        .app_id(self._app_id)
                        .app_secret(self._app_secret)
                        .build()
                    )
        return self._lark_client

    def _add_reaction(self, message_id: str, emoji_type: str = "Typing") -> str:
        if not message_id:
            return ""
        try:
            from lark_oapi.api.im.v1 import CreateMessageReactionRequest
            from lark_oapi.api.im.v1.model.create_message_reaction_request_body import (
                CreateMessageReactionRequestBody,
            )
            from lark_oapi.api.im.v1.model.emoji import Emoji

            request = (
                CreateMessageReactionRequest.builder()
                .message_id(message_id)
                .request_body(
                    CreateMessageReactionRequestBody.builder()
                    .reaction_type(
                        Emoji.builder().emoji_type(emoji_type).build()
                    )
                    .build()
                )
                .build()
            )
            with self._lark_lock:
                response = self._get_lark_client().im.v1.message_reaction.create(
                    request
                )
            if response.success() and response.data:
                return response.data.reaction_id or ""
            logger.debug(
                "Feishu reaction create failed code=%s msg=%s",
                response.code,
                response.msg,
            )
        except Exception:
            logger.debug("Feishu reaction create failed", exc_info=True)
        return ""

    def _remove_reaction(self, message_id: str, reaction_id: str) -> None:
        if not message_id or not reaction_id:
            return
        try:
            from lark_oapi.api.im.v1 import DeleteMessageReactionRequest

            request = (
                DeleteMessageReactionRequest.builder()
                .message_id(message_id)
                .reaction_id(reaction_id)
                .build()
            )
            with self._lark_lock:
                response = self._get_lark_client().im.v1.message_reaction.delete(
                    request
                )
            if not response.success():
                logger.debug(
                    "Feishu reaction delete failed code=%s msg=%s",
                    response.code,
                    response.msg,
                )
        except Exception:
            logger.debug("Feishu reaction delete failed", exc_info=True)

    def _send_text(self, open_id: str, text: str) -> bool:
        from lark_oapi.api.im.v1 import CreateMessageRequest
        from lark_oapi.api.im.v1.model.create_message_request_body import (
            CreateMessageRequestBody,
        )

        request = (
            CreateMessageRequest.builder()
            .receive_id_type("open_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(open_id)
                .msg_type("text")
                .content(json.dumps({"text": text}, ensure_ascii=False))
                .build()
            )
            .build()
        )
        with self._lark_lock:
            response = self._get_lark_client().im.v1.message.create(request)
        if response.code == 0:
            logger.info(
                "Feishu text sent principal=%s chars=%d",
                _principal_for_log(open_id),
                len(text),
            )
            return True
        logger.error("Feishu text send failed code=%s msg=%s", response.code, response.msg)
        return False

    def _send_long_text(self, open_id: str, text: str) -> bool:
        succeeded = True
        for chunk in _split_text(text, _TEXT_MESSAGE_CHARS):
            if not self._send_text(open_id, chunk):
                succeeded = False
        return succeeded

    @staticmethod
    def _text_card(
        text: str,
        template: str,
        title: str,
    ) -> dict[str, Any]:
        return {
            "schema": "2.0",
            "header": {
                "template": template,
                "title": {"tag": "plain_text", "content": title},
            },
            "body": {
                "elements": [{"tag": "markdown", "content": text}],
            },
        }

    def _send_card_returning_id(
        self,
        open_id: str,
        card: dict[str, Any],
    ) -> str | None:
        from lark_oapi.api.im.v1 import CreateMessageRequest
        from lark_oapi.api.im.v1.model.create_message_request_body import (
            CreateMessageRequestBody,
        )

        request = (
            CreateMessageRequest.builder()
            .receive_id_type("open_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(open_id)
                .msg_type("interactive")
                .content(json.dumps(card, ensure_ascii=False))
                .build()
            )
            .build()
        )
        with self._lark_lock:
            response = self._get_lark_client().im.v1.message.create(request)
        if response.code == 0 and response.data:
            message_id = getattr(response.data, "message_id", "") or ""
            logger.info(
                "Feishu card sent principal=%s msg_id=%s",
                _principal_for_log(open_id),
                message_id,
            )
            return message_id or None
        logger.error(
            "Feishu card send failed code=%s msg=%s",
            response.code,
            response.msg,
        )
        return None

    def _update_card(self, message_id: str, card: dict[str, Any]) -> bool:
        try:
            from lark_oapi.api.im.v1 import PatchMessageRequest
            from lark_oapi.api.im.v1.model.patch_message_request_body import (
                PatchMessageRequestBody,
            )

            request = (
                PatchMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    PatchMessageRequestBody.builder()
                    .content(json.dumps(card, ensure_ascii=False))
                    .build()
                )
                .build()
            )
            with self._lark_lock:
                response = self._get_lark_client().im.v1.message.patch(request)
            if response.code == 0:
                return True
            logger.warning(
                "Feishu card update failed code=%s msg=%s",
                response.code,
                response.msg,
            )
        except Exception:
            logger.warning("Feishu card update failed", exc_info=True)
        return False

    def _send_card(
        self,
        open_id: str,
        text: str,
        template: str = "blue",
        title: str = ASSISTANT_NAME,
    ) -> bool:
        rendered = _render_card_markdown(text)
        chunks = _split_text(rendered)
        succeeded = True
        total = len(chunks)
        for index, chunk in enumerate(chunks, start=1):
            chunk_title = title if total == 1 else f"{title}（{index}/{total}）"
            card = self._text_card(chunk, template, chunk_title)
            try:
                message_id = self._send_card_returning_id(open_id, card)
            except Exception:
                logger.exception("Feishu card send failed")
                message_id = None
            if message_id is None:
                succeeded = False
                self._send_long_text(open_id, f"{chunk_title}\n\n{chunk}")
        return succeeded

    def _download_image(self, message_id: str, image_key: str) -> tuple[bytes, str]:
        from lark_oapi.api.im.v1 import GetMessageResourceRequest

        request = (
            GetMessageResourceRequest.builder()
            .message_id(message_id)
            .file_key(image_key)
            .type("image")
            .build()
        )
        with self._lark_lock:
            response = self._get_lark_client().im.v1.message_resource.get(request)
        if not response.success() or not response.file:
            raise RuntimeError(
                f"Feishu image download failed: {response.code} {response.msg}"
            )
        data = response.file.read()
        media_type = "image/jpeg"
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            media_type = "image/png"
        elif data.startswith((b"GIF87a", b"GIF89a")):
            media_type = "image/gif"
        elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            media_type = "image/webp"
        return data, media_type

    async def _deliver_artifact(
        self,
        open_id: str,
        session: str,
        artifact_id: str,
    ) -> None:
        downloaded = await (await self._client_for(open_id)).download_artifact(
            session,
            artifact_id,
        )
        _header, encoded = downloaded.data_url.split(",", 1)
        data = base64.b64decode(encoded, validate=True)
        suffix = Path(downloaded.artifact.name).suffix
        if not suffix:
            suffix = mimetypes.guess_extension(downloaded.artifact.media_type) or ".bin"
        self._outbox.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = self._outbox / f"{uuid.uuid4().hex}{suffix}"
        target.write_bytes(data)
        target.chmod(0o600)
        try:
            sender = self._send_image if downloaded.artifact.kind == "image" else self._send_file
            await asyncio.to_thread(
                sender,
                open_id,
                target,
                downloaded.artifact.name,
            )
        finally:
            target.unlink(missing_ok=True)

    def _send_image(self, open_id: str, path: Path, _name: str = "") -> bool:
        from lark_oapi.api.im.v1 import CreateImageRequest, CreateMessageRequest
        from lark_oapi.api.im.v1.model.create_image_request_body import (
            CreateImageRequestBody,
        )
        from lark_oapi.api.im.v1.model.create_message_request_body import (
            CreateMessageRequestBody,
        )

        with path.open("rb") as stream:
            upload = (
                CreateImageRequest.builder()
                .request_body(
                    CreateImageRequestBody.builder()
                    .image_type("message")
                    .image(stream)
                    .build()
                )
                .build()
            )
            with self._lark_lock:
                response = self._get_lark_client().im.v1.image.create(upload)
        if not response.success() or not response.data:
            return False
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("open_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(open_id)
                .msg_type("image")
                .content(json.dumps({"image_key": response.data.image_key}))
                .build()
            )
            .build()
        )
        with self._lark_lock:
            sent = self._get_lark_client().im.v1.message.create(request)
        return sent.code == 0

    def _send_file(self, open_id: str, path: Path, name: str = "") -> bool:
        from lark_oapi.api.im.v1 import CreateFileRequest, CreateMessageRequest
        from lark_oapi.api.im.v1.model.create_file_request_body import (
            CreateFileRequestBody,
        )
        from lark_oapi.api.im.v1.model.create_message_request_body import (
            CreateMessageRequestBody,
        )

        with path.open("rb") as stream:
            upload = (
                CreateFileRequest.builder()
                .request_body(
                    CreateFileRequestBody.builder()
                    .file_type("stream")
                    .file_name(name or path.name)
                    .file(stream)
                    .build()
                )
                .build()
            )
            with self._lark_lock:
                response = self._get_lark_client().im.v1.file.create(upload)
        if not response.success() or not response.data:
            return False
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("open_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(open_id)
                .msg_type("file")
                .content(json.dumps({"file_key": response.data.file_key}))
                .build()
            )
            .build()
        )
        with self._lark_lock:
            sent = self._get_lark_client().im.v1.message.create(request)
        return sent.code == 0

    async def _client_for(self, open_id: str) -> CoreClient:
        current = self._clients.get(open_id)
        if current is not None and current.is_connected:
            return current
        if current is not None:
            await current.disconnect()
        signing_key = resolve_local_service_token(self._paths)
        principal = f"personal:feishu:{_principal_for_log(open_id)}"
        credential = issue_principal_credential(signing_key, principal)

        async def confirm(message: ConfirmationRequestedMessage) -> bool:
            return await self._confirm_tool(open_id, message)

        client = await CoreClient.connect(
            f"ws://{self._config.service_host}:{self._config.service_port}",
            credential,
            confirmation_handler=confirm,
            max_buffered_run_events=4096,
        )
        self._clients[open_id] = client
        return client
