"""Feishu adapter that speaks only the public Core WebSocket API."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
import uuid
from typing import Any

from knoa_platform.branding import ASSISTANT_NAME
from knoa_platform.channels.feishu_cards import (
    _ActiveTaskPresentation,
    _PendingConfirmation,
    _StreamingCardState,
    _principal_for_log,
    _render_card_markdown,
    _split_text,
)
from knoa_platform.conversation import ChatTurnState
from knoa_platform.service.core_api import (
    ArtifactInputRef,
    ChatApprovalSnapshot,
)
from knoa_platform.service.core_client import CoreClient, CoreRequestError
from knoa_platform.tasks import (
    TERMINAL_TASK_STATES,
    TaskEvent,
    TaskOrigin,
    TaskState,
)

logger = logging.getLogger(__name__)
_CONFIRM_TIMEOUT_SECONDS = 110.0
_CONFIRM_WORDS = frozenset(
    {
        "确认",
        "批准",
        "yes",
        "y",
        "ok",
        "confirm",
        "/confirm",
        "approve",
        "/approve",
    }
)
_REJECT_WORDS = frozenset(
    {
        "取消",
        "拒绝",
        "no",
        "n",
        "cancel",
        "/cancel",
        "deny",
        "/deny",
        "reject",
        "/reject",
    }
)
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

class FeishuConversationMixin:

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
            pending.approval_id,
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
        approval_id: str,
        approved: bool,
        *,
        resource_id: str = "",
    ) -> _PendingConfirmation | None:
        with self._pending_confirmation_lock:
            pending = self._pending_confirmations.get(open_id)
            if (
                pending is None
                or pending.resolved
                or pending.approval_id != approval_id
                or (resource_id and pending.resource_id != resource_id)
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
            with self._pending_confirmation_lock:
                confirmation = self._pending_confirmations.get(open_id)
            if confirmation is not None and not confirmation.resolved:
                if normalized in _CONFIRM_WORDS:
                    self._resolve_confirmation(
                        open_id,
                        confirmation.approval_id,
                        True,
                    )
                    await asyncio.to_thread(
                        self._send_text,
                        open_id,
                        "已确认",
                    )
                    return
                if normalized in _REJECT_WORDS:
                    self._resolve_confirmation(
                        open_id,
                        confirmation.approval_id,
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

            if normalized in {"/stop", "/cancel"}:
                await self._cancel_active_task(open_id)
                return

            try:
                await self._run_text(open_id, text)
            except Exception as exc:
                logger.exception(
                    "Feishu Core Task failed principal=%s",
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

    async def _cancel_active_task(self, open_id: str) -> None:
        client = self._clients.get(open_id)
        if client is None or not client.is_connected:
            await asyncio.to_thread(
                self._send_text,
                open_id,
                "当前没有正在运行的任务。",
            )
            return
        try:
            active_turn_id = self._active_chat_turn_ids.get(open_id)
            if active_turn_id:
                turn = await client.cancel_chat_turn(active_turn_id)
                message = (
                    "正在停止当前对话。"
                    if turn.state not in {ChatTurnState.CANCELLED, ChatTurnState.COMPLETED, ChatTurnState.FAILED}
                    else "当前对话已经结束。"
                )
                await asyncio.to_thread(self._send_text, open_id, message)
                return
            result = await client.cancel_active_task()
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
        if result is None:
            message = "当前没有正在运行的任务。"
        elif result.result.accepted and result.result.state not in TERMINAL_TASK_STATES:
            message = "正在停止当前任务。"
        elif result.result.state is not None:
            message = "当前任务已经结束。"
        else:
            message = "当前任务已经结束。"
        await asyncio.to_thread(self._send_text, open_id, message)

    async def _run_text(self, open_id: str, text: str) -> None:
        client = await self._client_for(open_id)
        stripped = text.strip()
        command, _separator, argument = stripped.partition(" ")
        command = command.lower()
        argument = argument.strip()

        if command in {"/agent", "/new"}:
            current_agent = await self._current_agent_id(open_id, client)
            if command == "/agent" and not argument:
                await asyncio.to_thread(
                    self._send_text,
                    open_id,
                    self._agent_help(current_agent),
                )
                return
            requested_agent = argument or current_agent
            selectable = self._selectable_agents()
            if requested_agent not in selectable:
                available = "、".join(f"`{agent_id}`" for agent_id in selectable)
                await asyncio.to_thread(
                    self._send_text,
                    open_id,
                    f"Agent `{requested_agent}` 不可用。可选：{available or '暂无'}。",
                )
                return
            session = await client.create_session(agent_id=requested_agent)
            self._bind_session(open_id, session)
            display_name = selectable[requested_agent]
            message = (
                f"已切换到 **{display_name}**（`{requested_agent}`），并开始新对话。"
                if command == "/agent"
                else f"已使用 **{display_name}**（`{requested_agent}`）开始新对话。"
            )
            await asyncio.to_thread(self._send_text, open_id, message)
            return

        if command == "/tasks" and not argument:
            listing = await client.list_tasks(
                origins=(
                    TaskOrigin.USER,
                    TaskOrigin.AGENT,
                    TaskOrigin.SCHEDULED,
                    TaskOrigin.EVENT,
                ),
                limit=10,
            )
            if not listing.tasks:
                await asyncio.to_thread(self._send_text, open_id, "暂无任务。")
                return
            lines = []
            for task in listing.tasks:
                goal = " ".join(task.goal.split())
                if len(goal) > 100:
                    goal = goal[:99] + "…"
                lines.append(
                    f"- **{_TASK_STATE_LABELS[task.state]}** `{task.task_id}`\n"
                    f"  {goal}"
                )
            if listing.next_cursor:
                lines.append("\n仅显示最近 10 个任务。")
            await asyncio.to_thread(
                self._send_card,
                open_id,
                "\n".join(lines),
                "blue",
                "任务",
            )
            return

        if command == "/task":
            if not argument:
                await asyncio.to_thread(
                    self._send_text,
                    open_id,
                    "用法：/task <任务 ID>",
                )
                return
            try:
                task = await client.get_task(argument)
            except CoreRequestError as exc:
                if exc.code == "task_not_found":
                    await asyncio.to_thread(
                        self._send_text,
                        open_id,
                        "未找到该任务。",
                    )
                    return
                raise
            lines = [
                f"状态：**{_TASK_STATE_LABELS[task.state]}**",
                f"任务：`{task.task_id}`",
                "",
                task.goal,
            ]
            outcome = task.final_summary or task.failure_code
            if outcome:
                lines.extend(["", "---", "", outcome])
            await asyncio.to_thread(
                self._send_card,
                open_id,
                "\n".join(lines),
                "blue" if task.state is not TaskState.FAILED else "red",
                "任务",
            )
            return

        if command in {"/stop", "/cancel"} and argument:
            try:
                result = await client.cancel_task(argument)
            except CoreRequestError as exc:
                if exc.code == "task_not_found":
                    await asyncio.to_thread(
                        self._send_text,
                        open_id,
                        "未找到该任务。",
                    )
                    return
                raise
            message = (
                "正在停止任务。"
                if result.result.accepted
                and result.result.state not in TERMINAL_TASK_STATES
                else "任务已经结束。"
            )
            await asyncio.to_thread(self._send_text, open_id, message)
            return

        if command == "/pause" and argument:
            try:
                result = await client.pause_task(argument, reason="飞书手动暂停")
            except CoreRequestError as exc:
                if exc.code == "task_not_found":
                    await asyncio.to_thread(self._send_text, open_id, "未找到该任务。")
                    return
                raise
            message = "已暂停。" if result.result.state is TaskState.PAUSED else "暂停请求已提交。"
            await asyncio.to_thread(self._send_text, open_id, message)
            return

        if command == "/resume" and argument:
            try:
                result = await client.resume_task(argument, reason="飞书手动恢复")
            except CoreRequestError as exc:
                if exc.code == "task_not_found":
                    await asyncio.to_thread(self._send_text, open_id, "未找到该任务。")
                    return
                raise
            await asyncio.to_thread(
                self._send_text,
                open_id,
                "已恢复。" if result.state in {TaskState.QUEUED, TaskState.RUNNING} else "恢复请求已提交。",
            )
            return

        if command == "/retry" and argument:
            try:
                previous = await client.get_task(argument)
                if previous.state not in TERMINAL_TASK_STATES:
                    await asyncio.to_thread(self._send_text, open_id, "任务尚未结束，无需重试。")
                    return
                session = await client.create_session(activate=False)
                accepted = await client.create_task(
                    session,
                    previous.goal,
                    previous.attachments,
                    tools_enabled=previous.tools_enabled,
                    priority=previous.priority,
                    parent_task_id=previous.task_id,
                    origin=previous.origin,
                )
            except CoreRequestError as exc:
                if exc.code == "task_not_found":
                    await asyncio.to_thread(self._send_text, open_id, "未找到该任务。")
                    return
                raise
            await asyncio.to_thread(
                self._send_text,
                open_id,
                f"已重新执行：`{accepted.task_id}`",
            )
            return

        session = await self._session_for(open_id)
        if command == "/status" and not argument:
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
            await self._stream_chat_turn(
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
            await self._stream_chat_turn(
                open_id,
                client,
                session,
                text,
                attachments,
            )

    async def _stream_chat_turn(
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
        presentation = _ActiveTaskPresentation(
            session_handle=session,
            state=state,
            update_requested=update_requested,
        )
        self._active_session_presentations[session] = presentation
        stop_updates = False
        last_patch = 0.0
        terminal = ""
        artifacts: list[str] = []
        handled_approvals: set[str] = set()

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
            accepted = await client.create_chat_turn(
                session,
                text,
                attachments,
                client_request_id=str(uuid.uuid4()),
            )
            turn_id = accepted.turn_id
            presentation.bind_task(turn_id)
            self._active_chat_turn_ids[open_id] = turn_id
            start_card()
            async for snapshot in client.chat_turn_updates(turn_id):
                state.load_chat_snapshot(snapshot)
                artifacts = [artifact.artifact_id for artifact in snapshot.artifacts]
                update_requested.set()
                for approval in snapshot.approvals:
                    if (
                        approval.state != "pending"
                        or approval.approval_id in handled_approvals
                    ):
                        continue
                    handled_approvals.add(approval.approval_id)
                    approved = await self._confirm_chat_approval(
                        open_id,
                        presentation,
                        turn_id,
                        approval,
                    )
                    await client.resolve_chat_approval(
                        approval.approval_id,
                        approved=approved,
                    )
                    presentation.resolve_confirmation(
                        approval.approval_id,
                        "confirmed" if approved else "cancelled",
                    )
                if snapshot.state is ChatTurnState.COMPLETED:
                    terminal = "completed"
                elif snapshot.state is ChatTurnState.CANCELLED:
                    terminal = "cancelled"
                elif snapshot.state is ChatTurnState.FAILED:
                    terminal = "failed"
        finally:
            stop_updates = True
            update_requested.set()
            await updater
            self._active_chat_turn_ids.pop(open_id, None)
            if self._active_session_presentations.get(session) is presentation:
                self._active_session_presentations.pop(session, None)

        card_message_id = (
            await card_task if card_task is not None else None
        )

        if terminal == "completed":
            if state.phase != "done":
                raise RuntimeError("ChatTurn completed without final output")
            final_output = state.final_output if state.final_output else "已完成"
            if len(final_output) > _LONG_RESULT_CARD_CHARS and artifacts:
                preview = _split_text(
                    _render_card_markdown(final_output),
                    _LONG_RESULT_PREVIEW_CHARS,
                    max_tables=1,
                )[0].rstrip()
                rendered = preview + "\n\n完整内容见附件。"
            else:
                rendered = _render_card_markdown(final_output)
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
                state.set_error("ChatTurn ended without a terminal state")
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

    async def _handle_file(
        self,
        open_id: str,
        message_id: str,
        file_key: str,
        file_name: str,
    ) -> None:
        reaction_id = await asyncio.to_thread(
            self._add_reaction,
            message_id,
            "Typing",
        )
        try:
            self._save_binding(open_id)
            data, media_type = await asyncio.to_thread(
                self._download_file,
                message_id,
                file_key,
                file_name,
            )
            if len(data) > _MAX_CORE_ARTIFACT_RAW_BYTES:
                raise ValueError("File exceeds Core ingress limit")
            session = await self._session_for(open_id)
            encoded = base64.b64encode(data).decode("ascii")
            client = await self._client_for(open_id)
            artifact = await client.upload_artifact(
                session,
                f"data:{media_type};base64,{encoded}",
                media_type=media_type,
                name=file_name,
                caption=file_name,
            )
            pending = self._pending_attachments.setdefault(open_id, [])
            pending.append(
                ArtifactInputRef(
                    artifact_id=artifact.artifact_id,
                    caption=file_name,
                )
            )
            self._pending_attachments[open_id] = pending[-4:]
            await asyncio.to_thread(
                self._send_text,
                open_id,
                f"文件已收到：{file_name}\n请继续发送任务。",
            )
        except Exception:
            logger.exception(
                "Feishu file ingress failed principal=%s",
                _principal_for_log(open_id),
            )
            await asyncio.to_thread(self._send_text, open_id, "文件接收失败")
        finally:
            await asyncio.to_thread(
                self._remove_reaction,
                message_id,
                reaction_id,
            )

    async def _handle_audio(
        self,
        open_id: str,
        message_id: str,
        file_key: str,
    ) -> None:
        reaction_id = await asyncio.to_thread(
            self._add_reaction,
            message_id,
            "Typing",
        )
        artifact: Any = None
        session = ""
        try:
            self._save_binding(open_id)
            data, media_type, file_name = await asyncio.to_thread(
                self._download_audio,
                message_id,
                file_key,
            )
            if len(data) > _MAX_CORE_ARTIFACT_RAW_BYTES:
                raise ValueError("Audio exceeds Core ingress limit")
            session = await self._session_for(open_id)
            encoded = base64.b64encode(data).decode("ascii")
            client = await self._client_for(open_id)
            artifact = await client.upload_artifact(
                session,
                f"data:{media_type};base64,{encoded}",
                media_type=media_type,
                name=file_name,
                caption="Feishu voice message",
            )
            pending = self._pending_attachments.setdefault(open_id, [])
            pending.append(
                ArtifactInputRef(
                    artifact_id=artifact.artifact_id,
                    caption="Feishu voice message",
                )
            )
            self._pending_attachments[open_id] = pending[-4:]
        except Exception:
            logger.exception(
                "Feishu audio ingress failed principal=%s",
                _principal_for_log(open_id),
            )
            await asyncio.to_thread(self._send_text, open_id, "语音接收失败")
            await asyncio.to_thread(
                self._remove_reaction,
                message_id,
                reaction_id,
            )
            return

        try:
            client = await self._client_for(open_id)
            result = await client.transcribe_artifact(
                session,
                artifact.artifact_id,
            )
            await self._run_text(open_id, result.transcript)
        except CoreRequestError as exc:
            message = (
                "语音已收到；尚未配置转写能力。"
                if exc.code == "capability_denied"
                else "语音已收到，但转写失败。请发送文字说明。"
            )
            await asyncio.to_thread(self._send_text, open_id, message)
        except Exception:
            logger.exception(
                "Feishu audio transcription failed principal=%s",
                _principal_for_log(open_id),
            )
            await asyncio.to_thread(
                self._send_text,
                open_id,
                "语音已收到，但转写失败。请发送文字说明。",
            )
        finally:
            await asyncio.to_thread(
                self._remove_reaction,
                message_id,
                reaction_id,
            )

    async def _confirm_tool(
        self,
        open_id: str,
        message: TaskEvent,
    ) -> bool:
        presentation = self._active_task_presentations.get(message.task_id)
        if (
            presentation is None
            or (
                presentation.task_id
                and presentation.task_id != message.task_id
            )
            or not presentation.request_confirmation(message)
        ):
            return False
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        pending = _PendingConfirmation(
            approval_id=message.payload.approval_id,
            future=future,
            loop=loop,
            resource_id=message.task_id,
            presentation=presentation,
        )
        with self._pending_confirmation_lock:
            current = self._pending_confirmations.get(open_id)
            if current is not None and not current.resolved:
                presentation.resolve_confirmation(
                    message.payload.approval_id,
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
                message.payload.approval_id,
                "expired",
            )
            return False
        finally:
            with self._pending_confirmation_lock:
                if self._pending_confirmations.get(open_id) is pending:
                    self._pending_confirmations.pop(open_id, None)

    async def _confirm_chat_approval(
        self,
        open_id: str,
        presentation: _ActiveTaskPresentation,
        turn_id: str,
        approval: ChatApprovalSnapshot,
    ) -> bool:
        if not presentation.state.request_chat_confirmation(approval, turn_id):
            return approval.state == "approved"
        presentation.update_requested.set()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        pending = _PendingConfirmation(
            approval_id=approval.approval_id,
            future=future,
            loop=loop,
            resource_id=turn_id,
            presentation=presentation,
        )
        with self._pending_confirmation_lock:
            current = self._pending_confirmations.get(open_id)
            if current is not None and not current.resolved:
                presentation.resolve_confirmation(approval.approval_id, "cancelled")
                return False
            self._pending_confirmations[open_id] = pending
        try:
            return await asyncio.wait_for(future, timeout=_CONFIRM_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            with self._pending_confirmation_lock:
                pending.resolved = True
            presentation.resolve_confirmation(approval.approval_id, "expired")
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

    def _selectable_agents(self) -> dict[str, str]:
        return {
            agent_id: agent.display_name
            for agent_id, agent in self._config.node_agents.items()
            if agent.enabled and agent.visibility == "user"
        }

    async def _current_agent_id(self, open_id: str, client: CoreClient) -> str:
        session = self._sessions.get(open_id)
        if not session:
            return self._config.default_agent
        try:
            snapshot = await client.get_conversation_session(session)
        except CoreRequestError as exc:
            if exc.code != "session_not_found":
                raise
            return self._config.default_agent
        return snapshot.agent_id

    def _agent_help(self, current_agent: str) -> str:
        selectable = self._selectable_agents()
        current_name = selectable.get(current_agent, current_agent)
        lines = [
            f"当前 Agent：**{current_name}**（`{current_agent}`）",
            "",
            "可选 Agent：",
        ]
        lines.extend(
            f"- **{display_name}**：`{agent_id}`"
            for agent_id, display_name in selectable.items()
        )
        lines.extend(
            [
                "",
                "使用 `/agent <Agent ID>` 切换并开始新对话；"
                "使用 `/new` 继续用当前 Agent 开始新对话。",
            ]
        )
        return "\n".join(lines)

    def _bind_session(self, open_id: str, session: str) -> None:
        previous = self._sessions.get(open_id)
        if previous:
            self._session_users.pop(previous, None)
        self._sessions[open_id] = session
        self._session_users[session] = open_id
        self._save_sessions()
        self._ensure_principal_watcher(open_id)

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
