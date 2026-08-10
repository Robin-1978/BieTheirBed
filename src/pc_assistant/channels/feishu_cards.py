"""Feishu adapter that speaks only the public Core WebSocket API."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from pc_assistant import __version__
from pc_assistant.branding import ASSISTANT_NAME
from pc_assistant.conversation import ChatTurnState
from pc_assistant.service.core_api import (
    ChatApprovalSnapshot,
    ChatTurnSnapshot,
)
from pc_assistant.tasks import (
    TaskEvent,
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


def _split_plain_text(text: str, limit: int) -> tuple[str, ...]:
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


def _is_table_separator_line(line: str) -> bool:
    stripped = line.strip().strip("|")
    if "|" not in stripped:
        return False
    cells = stripped.split("|")
    return len(cells) >= 2 and all(
        re.fullmatch(r"\s*:?-{3,}:?\s*", cell) is not None
        for cell in cells
    )


def _markdown_table_count(text: str) -> int:
    return sum(
        1 for line in text.splitlines() if _is_table_separator_line(line)
    )


def _markdown_blocks(text: str) -> tuple[str, ...]:
    """Keep tables, lists and fenced code together when blank lines allow it."""
    blocks: list[str] = []
    current: list[str] = []
    fence = ""
    for line in text.splitlines(keepends=True):
        current.append(line)
        stripped = line.lstrip()
        marker = stripped[:3]
        if marker in {"```", "~~~"}:
            if not fence:
                fence = marker
            elif marker == fence:
                fence = ""
        if not fence and not line.strip():
            blocks.append("".join(current))
            current = []
    if current:
        blocks.append("".join(current))
    return tuple(blocks)


def _split_fenced_block(block: str, limit: int) -> tuple[str, ...] | None:
    lines = block.splitlines(keepends=True)
    nonempty = [index for index, line in enumerate(lines) if line.strip()]
    if len(nonempty) < 2:
        return None
    first, last = nonempty[0], nonempty[-1]
    opening = lines[first]
    marker = opening.lstrip()[:3]
    if marker not in {"```", "~~~"} or not lines[last].lstrip().startswith(marker):
        return None
    prefix = "".join(lines[:first])
    closing = lines[last]
    suffix = "".join(lines[last + 1 :])
    inner = "".join(lines[first + 1 : last])
    budget = limit - len(prefix) - len(opening) - len(closing) - 1
    if budget < 1:
        return None
    pieces = _split_plain_text(inner, budget)
    rendered: list[str] = []
    for index, piece in enumerate(pieces):
        separator = "" if not piece or piece.endswith("\n") else "\n"
        rendered.append(
            (prefix if index == 0 else "")
            + opening
            + piece
            + separator
            + closing
            + (suffix if index == len(pieces) - 1 else "")
        )
    return tuple(rendered)


def _split_markdown_block(block: str, limit: int) -> tuple[str, ...]:
    if len(block) <= limit:
        return (block,)
    fenced = _split_fenced_block(block, limit)
    if fenced is not None:
        return fenced
    return _split_plain_text(block, limit)


def _split_text(
    text: str,
    limit: int = _CARD_MARKDOWN_CHARS,
    *,
    max_tables: int | None = _CARD_TABLES_PER_CHUNK,
) -> tuple[str, ...]:
    """Split Markdown at block boundaries and cap tables per Feishu card."""
    if limit < 1:
        raise ValueError("Text chunk limit must be positive")
    if max_tables is not None and max_tables < 1:
        raise ValueError("Markdown table limit must be positive")
    if not text:
        return ("",)

    chunks: list[str] = []
    current = ""
    current_tables = 0
    for block in _markdown_blocks(text):
        for piece in _split_markdown_block(block, limit):
            piece_tables = _markdown_table_count(piece)
            exceeds_chars = bool(current) and len(current) + len(piece) > limit
            exceeds_tables = (
                bool(current)
                and max_tables is not None
                and current_tables + piece_tables > max_tables
            )
            if exceeds_chars or exceeds_tables:
                chunks.append(current)
                current = ""
                current_tables = 0
            current += piece
            current_tables += piece_tables
    if current:
        chunks.append(current)
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
    approval_id: str = ""
    approval_resource_id: str = ""
    confirmation_reason: str = ""
    confirmation_status: str = ""


@dataclass
class _ThoughtStep:
    source: str
    content: str
    iteration: int = 0


@dataclass
class _PendingConfirmation:
    approval_id: str
    future: asyncio.Future[bool]
    loop: asyncio.AbstractEventLoop
    resource_id: str
    presentation: _ActiveTaskPresentation
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
    """Channel-local projection of standard Core Task events."""

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
        message: TaskEvent,
    ) -> bool:
        payload = message.payload
        step = next(
            (
                candidate
                for candidate in reversed(self.timeline)
                if isinstance(candidate, _ToolStep)
                and candidate.call_id == payload.tool_call_id
                and candidate.status == "running"
            ),
            None,
        )
        if step is None:
            step = _ToolStep(
                call_id=payload.tool_call_id,
                name=payload.tool_name,
                arguments=payload.tool_args,
            )
            self.timeline.append(step)
        step.approval_id = payload.approval_id
        step.approval_resource_id = message.task_id
        step.confirmation_reason = (
            payload.reason or "该操作可能改变系统状态"
        )
        step.confirmation_status = "pending"
        self.phase = "working"
        return True

    def request_chat_confirmation(
        self,
        approval: ChatApprovalSnapshot,
        turn_id: str,
    ) -> bool:
        step = next(
            (
                candidate
                for candidate in reversed(self.timeline)
                if isinstance(candidate, _ToolStep)
                and candidate.call_id == approval.tool_call_id
            ),
            None,
        )
        if step is None:
            step = _ToolStep(
                call_id=approval.tool_call_id,
                name=approval.tool_name,
                arguments=approval.arguments,
            )
            self.timeline.append(step)
        step.approval_id = approval.approval_id
        step.approval_resource_id = turn_id
        step.confirmation_reason = approval.reason or "该操作可能改变系统状态"
        step.confirmation_status = (
            "pending"
            if approval.state == "pending"
            else "confirmed"
            if approval.state == "approved"
            else "cancelled"
        )
        self.phase = "working"
        return approval.state == "pending"

    def load_chat_snapshot(self, snapshot: ChatTurnSnapshot) -> None:
        self.timeline = []
        self.final_output = ""
        self.error = ""
        self.phase = "thinking"
        for entry in snapshot.timeline:
            if entry.kind == "reasoning":
                self.append_reasoning(entry.content, iteration=entry.iteration)
            elif entry.kind == "content":
                self.append_draft(entry.content, iteration=entry.iteration)
            elif entry.kind == "tool_call":
                self.add_tool_call(
                    entry.tool_call_id,
                    entry.tool_name,
                    entry.tool_args,
                    iteration=entry.iteration,
                )
            elif entry.kind == "tool_result":
                self.add_tool_result(
                    entry.tool_call_id,
                    entry.tool_name,
                    entry.tool_result,
                    blocked=entry.blocked,
                    iteration=entry.iteration,
                )
            elif entry.content:
                self.add_notice(entry.content)
        for approval in snapshot.approvals:
            self.request_chat_confirmation(approval, snapshot.turn_id)
        if snapshot.final_output:
            self.set_final_output(snapshot.final_output)
        elif snapshot.state is ChatTurnState.FAILED:
            self.set_error(snapshot.failure_code or "处理失败")
        elif snapshot.state is ChatTurnState.CANCELLED:
            self.set_cancelled()

    def resolve_confirmation(
        self,
        approval_id: str,
        status: str,
    ) -> bool:
        step = next(
            (
                candidate
                for candidate in reversed(self.timeline)
                if isinstance(candidate, _ToolStep)
                and candidate.approval_id == approval_id
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
                                            "resource_id": step.approval_resource_id,
                                            "approval_id": step.approval_id,
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
                                            "resource_id": step.approval_resource_id,
                                            "approval_id": step.approval_id,
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
class _ActiveTaskPresentation:
    session_handle: str
    state: _StreamingCardState
    update_requested: asyncio.Event
    task_id: str = ""

    def bind_task(self, task_id: str) -> None:
        if not self.task_id:
            self.task_id = task_id

    def request_confirmation(
        self,
        message: TaskEvent,
    ) -> bool:
        attached = self.state.request_confirmation(message)
        if attached:
            self.update_requested.set()
        return attached

    def resolve_confirmation(self, approval_id: str, status: str) -> None:
        if self.state.resolve_confirmation(approval_id, status):
            self.update_requested.set()
