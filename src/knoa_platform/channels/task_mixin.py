"""Shared background task notification handling for IM channel adapters."""
from __future__ import annotations

import asyncio
import logging
import re
import time

from knoa_platform.branding import ASSISTANT_NAME
from knoa_platform.notification_intent import (
    NotificationIntentRecord,
    notification_intent_for_event,
)
from knoa_platform.channels.feishu_cards import (
    _ActiveTaskPresentation,
    _StreamingCardState,
    _principal_for_log,
)
from knoa_platform.service.core_client import CoreClient, CoreRequestError
from knoa_platform.tasks import (
    PrincipalTaskEvent,
    TaskEvent,
    TaskOrigin,
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
_BACKGROUND_TASK_PREVIEW_CHARS = 1_200
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


def _compact_background_result(text: str) -> str:
    """Keep the first-screen conclusion readable; App retains the full result."""

    normalized = text.strip()
    if len(normalized) <= _BACKGROUND_TASK_PREVIEW_CHARS:
        return normalized
    head_limit = 720
    tail_limit = 360
    head = normalized[:head_limit].rstrip()
    tail_start = max(
        normalized.rfind("\n## 决策"),
        normalized.rfind("\n## 结论"),
        normalized.rfind("\n## Decision"),
        normalized.rfind("\n## Conclusion"),
    )
    tail = (
        normalized[tail_start:].strip()
        if tail_start >= head_limit
        else normalized[-tail_limit:].strip()
    )
    if len(tail) > tail_limit:
        tail = tail[:tail_limit].rstrip()
    return (
        f"{head}\n\n…\n\n{tail}\n\n"
        "完整结论和执行过程请在 Knoa Execution 中查看。"
    )

class ChannelTaskMixin:

    async def _task_notification_title(
        self,
        client: CoreClient,
        execution_id: str,
    ) -> str:
        try:
            execution = await client.get_product_task_execution(execution_id)
            task = await client.get_product_task(execution.task_id)
        except Exception:  # noqa: BLE001 - a title must never block notification delivery
            return ""
        return str(getattr(task, "title", "") or "").strip()[:80]

    async def _task_notification_policy(
        self,
        client: CoreClient,
        execution_id: str,
    ) -> dict[str, bool] | None:
        """Return the owning product Task policy, or None for ad-hoc Tasks."""

        try:
            execution = await client.get_product_task_execution(execution_id)
            task = await client.get_product_task(execution.task_id)
        except CoreRequestError as exc:
            if exc.code != "task_not_found":
                raise
            task = await client.get_task(execution_id)
            if task.origin is TaskOrigin.USER:
                return None
            return {
                "waiting_approval": True,
                "completed": True,
                "failed": True,
                "cancelled": True,
            }
        return task.notification_policy

    def _ensure_principal_watcher(self, open_id: str) -> None:
        if not self._running:
            return
        current = self._principal_watchers.get(open_id)
        if current is not None and not current.done():
            return
        self._principal_watcher_started_at[open_id] = time.time()
        watcher = asyncio.create_task(
            self._watch_principal_tasks(open_id),
            name=f"{self.name}-principal-feed-{_principal_for_log(open_id)}",
        )
        self._principal_watchers[open_id] = watcher

    async def _watch_principal_tasks(self, open_id: str) -> None:
        bootstrap = open_id not in self._notification_cursors
        started_at = self._principal_watcher_started_at[open_id]
        while self._running:
            try:
                client = await self._client_for(open_id)
                if callable(getattr(client, "list_notification_intents", None)):
                    await self._watch_notification_intents(
                        open_id,
                        client,
                        started_at=started_at,
                    )
                    continue
                cursor = self._notification_cursors.get(open_id, 0)
                async for feed_event in client.principal_task_events(
                    after_id=cursor
                ):
                    if not self._running:
                        return
                    cursor = feed_event.feed_event_id
                    if bootstrap and feed_event.event.occurred_at < started_at:
                        self._notification_cursors[open_id] = cursor
                        continue
                    bootstrap = False
                    while self._running:
                        if await self._deliver_principal_task_event(
                            open_id,
                            client,
                            feed_event,
                        ):
                            break
                        await asyncio.sleep(_PRINCIPAL_WATCH_RETRY_SECONDS)
                    if not self._running:
                        return
                    self._notification_cursors[open_id] = cursor
                    if notification_intent_for_event(feed_event.event.event_type) is not None:
                        self._save_notification_cursors()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "%s principal Task feed disconnected principal=%s",
                    self.name,
                    _principal_for_log(open_id),
                    exc_info=True,
                )
                if self._running:
                    await asyncio.sleep(_PRINCIPAL_WATCH_RETRY_SECONDS)

    async def _watch_notification_intents(
        self,
        open_id: str,
        client: CoreClient,
        *,
        started_at: float,
    ) -> None:
        bootstrap = open_id not in self._notification_intent_cursors
        while self._running:
            cursor = self._notification_intent_cursors.get(open_id, 0)
            intents = await client.list_notification_intents(
                after_sequence=cursor,
                limit=100,
            )
            if not intents:
                await asyncio.sleep(_PRINCIPAL_WATCH_RETRY_SECONDS)
                continue
            for intent in intents:
                if not self._running:
                    return
                cursor = intent.source_sequence
                if bootstrap and intent.created_at < started_at:
                    self._notification_intent_cursors[open_id] = cursor
                    continue
                bootstrap = False
                while self._running:
                    if await self._deliver_notification_intent(open_id, client, intent):
                        break
                    await asyncio.sleep(_PRINCIPAL_WATCH_RETRY_SECONDS)
                self._notification_intent_cursors[open_id] = cursor
                self._save_notification_intent_cursors()

    async def _event_for_notification_intent(
        self,
        client: CoreClient,
        intent: NotificationIntentRecord,
    ) -> TaskEvent | None:
        expected = {
            "approval_required": "approval_requested",
            "interaction_required": "interaction_requested",
        }.get(intent.category)
        if expected is None:
            return None
        action_key = "approval_id" if expected == "approval_requested" else "interaction_id"
        expected_action = str(intent.deep_link.get(action_key) or "")
        stream = client.task_events(intent.execution_id, handle_approvals=False)
        try:
            async for event in stream:
                if event.event_type != expected:
                    continue
                actual = getattr(event.payload, action_key)
                if not expected_action or actual == expected_action:
                    return event
                if event.event_seq >= intent.source_sequence:
                    return None
        finally:
            await stream.aclose()
        return None

    async def _deliver_notification_intent(
        self,
        open_id: str,
        client: CoreClient,
        intent: NotificationIntentRecord,
    ) -> bool:
        execution_id = intent.execution_id
        if intent.category in {"approval_required", "interaction_required"}:
            if execution_id in self._foreground_task_ids:
                return True
            event = await self._event_for_notification_intent(client, intent)
            if event is None:
                return True
            if intent.category == "approval_required":
                return await self._deliver_background_approval(open_id, client, event)
            return await self._deliver_interaction_request(open_id, client, event)
        if intent.category not in {"completed", "failed", "cancelled"}:
            return True
        if execution_id in self._foreground_task_ids:
            self._foreground_task_ids.discard(execution_id)
            return True
        try:
            snapshot = await client.get_task(execution_id)
        except Exception:
            logger.warning(
                "Channel notification Task lookup failed task_id=%s",
                execution_id,
                exc_info=True,
            )
            return False
        title_value = str(intent.parameters.get("title") or "").strip()[:80]
        if intent.category == "completed":
            text = _compact_background_result(snapshot.final_summary or "已完成")
            template = "blue"
            title = f"已完成 · {title_value}" if title_value else ASSISTANT_NAME
        elif intent.category == "cancelled":
            text, template = "已停止", "grey"
            title = f"已停止 · {title_value}" if title_value else "已停止"
        else:
            reason = snapshot.final_summary or snapshot.failure_code or "任务未完成"
            text, template = f"× {_compact_background_result(reason)}", "red"
            title = f"处理出错 · {title_value}" if title_value else "处理出错"
        try:
            return await asyncio.to_thread(
                self._send_card,
                open_id,
                text,
                template,
                title,
            )
        except Exception:
            logger.warning(
                "Channel notification delivery failed task_id=%s",
                execution_id,
                exc_info=True,
            )
            return False

    async def _deliver_principal_task_event(
        self,
        open_id: str,
        client: CoreClient,
        feed_event: PrincipalTaskEvent,
    ) -> bool:
        event = feed_event.event
        if (
            event.event_type in {"approval_requested", "interaction_requested"}
            and event.task_id in self._foreground_task_ids
        ):
            return True
        if (
            event.event_type in _TASK_TERMINAL_EVENT_TYPES
            and event.task_id in self._foreground_task_ids
        ):
            self._foreground_task_ids.discard(event.task_id)
            return True
        intent = notification_intent_for_event(event.event_type)
        if intent is not None:
            try:
                policy = await self._task_notification_policy(
                    client,
                    event.task_id,
                )
            except Exception:
                logger.warning(
                    "Feishu Task notification policy lookup failed task_id=%s",
                    event.task_id,
                    exc_info=True,
                )
                return False
            if policy is None:
                return True
            if not policy.get(intent.policy_key, False):
                return True
        if event.event_type == "approval_requested":
            return await self._deliver_background_approval(
                open_id,
                client,
                event,
            )
        if event.event_type == "interaction_requested":
            return await self._deliver_interaction_request(open_id, client, event)
        if event.event_type not in _TASK_TERMINAL_EVENT_TYPES:
            return True
        try:
            snapshot = await client.get_task(event.task_id)
        except Exception:
            logger.warning(
                "Feishu background Task lookup failed task_id=%s",
                event.task_id,
                exc_info=True,
            )
            return False

        task_title = await self._task_notification_title(client, event.task_id)

        if event.event_type == "completed":
            text = _compact_background_result(
                snapshot.final_summary or event.payload.content or "已完成"
            )
            template = "blue"
            title = f"已完成 · {task_title}" if task_title else ASSISTANT_NAME
        elif event.event_type == "cancelled":
            text = "已停止"
            template = "grey"
            title = f"已停止 · {task_title}" if task_title else "已停止"
        else:
            reason = (
                snapshot.final_summary
                or event.payload.content
                or snapshot.failure_code
                or "任务未完成"
            )
            text = f"× {_compact_background_result(reason)}"
            template = "red"
            title = f"处理出错 · {task_title}" if task_title else "处理出错"
        try:
            return await asyncio.to_thread(
                self._send_card,
                open_id,
                text,
                template,
                title,
            )
        except Exception:
            logger.warning(
                "Feishu background Task notification failed task_id=%s",
                event.task_id,
                exc_info=True,
            )
            return False

    async def _deliver_background_approval(
        self,
        open_id: str,
        client: CoreClient,
        event: TaskEvent,
    ) -> bool:
        approval_id = event.payload.approval_id
        decided = self._background_approval_decisions.get(approval_id)
        if decided is not None:
            try:
                await client.resolve_approval(approval_id, approved=decided)
            except Exception:
                logger.warning(
                    "Feishu background approval retry failed approval_id=%s",
                    approval_id,
                    exc_info=True,
                )
                return False
            self._background_approval_decisions.pop(approval_id, None)
            return True
        with self._pending_confirmation_lock:
            current = self._pending_confirmations.get(open_id)
            if current is not None and not current.resolved:
                return False
        try:
            snapshot = await client.get_task(event.task_id)
        except Exception:
            logger.warning(
                "Feishu background approval Task lookup failed task_id=%s",
                event.task_id,
                exc_info=True,
            )
            return False
        if snapshot.state is not TaskState.WAITING_APPROVAL:
            return True

        state = _StreamingCardState()
        presentation = _ActiveTaskPresentation(
            session_handle=snapshot.session_handle,
            state=state,
            update_requested=asyncio.Event(),
            task_id=event.task_id,
        )
        self._active_task_presentations[event.task_id] = presentation
        confirmation = asyncio.create_task(self._confirm_tool(open_id, event))
        await asyncio.sleep(0)
        try:
            message_id = await asyncio.to_thread(
                self._send_card_returning_id,
                open_id,
                state.build_card(),
            )
            if message_id is None:
                confirmation.cancel()
                await asyncio.gather(confirmation, return_exceptions=True)
                return False
            approved = await confirmation
            self._background_approval_decisions[approval_id] = approved
            await asyncio.to_thread(
                self._update_card,
                message_id,
                state.build_card(),
            )
            await client.resolve_approval(
                approval_id,
                approved=approved,
            )
            self._background_approval_decisions.pop(approval_id, None)
            return True
        except asyncio.CancelledError:
            confirmation.cancel()
            await asyncio.gather(confirmation, return_exceptions=True)
            raise
        except Exception:
            logger.warning(
                "Feishu background approval delivery failed task_id=%s",
                event.task_id,
                exc_info=True,
            )
            return False
        finally:
            if self._active_task_presentations.get(event.task_id) is presentation:
                self._active_task_presentations.pop(event.task_id, None)


__all__ = ["ChannelTaskMixin", "_compact_background_result"]
