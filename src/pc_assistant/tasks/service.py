"""Application service for durable Task commands and event subscriptions."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from pc_assistant.agent_runtime.contracts import (
    ArtifactAttachment,
    HealthStatus,
    RuntimeScope,
)
from pc_assistant.tasks.approval import DurableApprovalService
from pc_assistant.tasks.event_hub import TaskEventHub
from pc_assistant.tasks.executor import TaskExecutor
from pc_assistant.tasks.models import (
    TERMINAL_TASK_STATES,
    PrincipalTaskEvent,
    TaskApprovalRecord,
    TaskCancelResult,
    TaskEvent,
    TaskPauseResult,
    TaskOrigin,
    TaskRecord,
    TaskState,
)
from pc_assistant.tasks.repository import TaskRepository


class TaskService:
    """Own Task lifecycle while connections remain disposable subscribers."""

    def __init__(
        self,
        repository: TaskRepository,
        executor: TaskExecutor,
        approvals: DurableApprovalService,
        events: TaskEventHub,
    ) -> None:
        self._repository = repository
        self._executor = executor
        self._approvals = approvals
        self._events = events

    async def start(self) -> None:
        await self._executor.start()

    async def stop(self) -> None:
        await self._executor.stop()
        await self._approvals.close()

    async def health_check(self) -> HealthStatus:
        return await self._executor.health_check()

    async def create(
        self,
        scope: RuntimeScope,
        *,
        client_request_id: str,
        goal: str,
        attachments: tuple[ArtifactAttachment, ...] = (),
        tools_enabled: bool = True,
        priority: int = 0,
        parent_task_id: str = "",
        origin: TaskOrigin = TaskOrigin.CHAT,
    ) -> TaskRecord:
        task, created = await asyncio.to_thread(
            self._repository.create,
            scope,
            client_request_id=client_request_id,
            goal=goal,
            attachments=attachments,
            tools_enabled=tools_enabled,
            priority=priority,
            parent_task_id=parent_task_id,
            origin=origin,
        )
        if created:
            first = await asyncio.to_thread(
                self._repository.list_events,
                scope.principal_id,
                task.task_id,
                after_seq=0,
                limit=1,
            )
            if first:
                await self._events.publish(first[0])
            self._executor.wake()
        return task

    async def get(self, principal_id: str, task_id: str) -> TaskRecord:
        return await asyncio.to_thread(
            self._repository.get,
            principal_id,
            task_id,
        )

    async def list(
        self,
        principal_id: str,
        *,
        session_handle: str = "",
        state: TaskState | None = None,
        origins: tuple[TaskOrigin, ...] = (),
        limit: int = 50,
        cursor: str = "",
    ) -> tuple[tuple[TaskRecord, ...], str]:
        return await asyncio.to_thread(
            self._repository.list_tasks,
            principal_id,
            session_handle=session_handle,
            state=state,
            origins=origins,
            limit=limit,
            cursor=cursor,
        )

    async def cancel(
        self,
        principal_id: str,
        task_id: str,
        *,
        reason: str = "",
    ) -> TaskCancelResult:
        result, event = await asyncio.to_thread(
            self._repository.request_cancel,
            principal_id,
            task_id,
            reason=reason,
        )
        self._executor.signal_cancel(task_id)
        await self._approvals.cancel_task(task_id)
        if event is not None:
            await self._events.publish(event)
        return result

    async def pause(
        self,
        principal_id: str,
        task_id: str,
        *,
        reason: str = "",
    ) -> TaskPauseResult:
        result, event = await asyncio.to_thread(
            self._repository.request_pause,
            principal_id,
            task_id,
            reason=reason,
        )
        self._executor.signal_cancel(task_id)
        if result.state is TaskState.PAUSED:
            await self._approvals.cancel_task(task_id)
        if event is not None:
            await self._events.publish(event)
        return result

    async def resume(
        self,
        principal_id: str,
        task_id: str,
        *,
        reason: str = "",
        acknowledge_outcome_unknown: bool = False,
    ) -> TaskRecord:
        task, event = await asyncio.to_thread(
            self._repository.resume,
            principal_id,
            task_id,
            reason=reason,
            acknowledge_outcome_unknown=acknowledge_outcome_unknown,
        )
        await self._events.publish(event)
        self._executor.wake()
        return task

    async def resolve_approval(
        self,
        principal_id: str,
        approval_id: str,
        *,
        approved: bool,
        resolved_by: str = "",
    ) -> tuple[TaskApprovalRecord, bool]:
        approval, changed, resume_state = await self._approvals.resolve(
            principal_id,
            approval_id,
            approved=approved,
            resolved_by=resolved_by,
        )
        if changed and resume_state is TaskState.QUEUED:
            self._executor.wake()
        return approval, changed

    async def events(
        self,
        principal_id: str,
        task_id: str,
        *,
        after_seq: int = 0,
    ) -> AsyncIterator[TaskEvent]:
        task = await self.get(principal_id, task_id)
        subscription = await self._events.subscribe(task.task_id)
        last_seq = after_seq
        try:
            while True:
                page = await asyncio.to_thread(
                    self._repository.list_events,
                    principal_id,
                    task.task_id,
                    after_seq=last_seq,
                    limit=200,
                )
                if not page:
                    break
                for event in page:
                    last_seq = event.event_seq
                    yield event
                    if event.event_type in {"completed", "failed", "cancelled"}:
                        return
                if len(page) < 200:
                    break

            current = await self.get(principal_id, task.task_id)
            if current.state in TERMINAL_TASK_STATES:
                return
            while True:
                event = await subscription.receive()
                if event.event_seq <= last_seq:
                    continue
                if event.event_seq > last_seq + 1:
                    missing = await asyncio.to_thread(
                        self._repository.list_events,
                        principal_id,
                        task.task_id,
                        after_seq=last_seq,
                        limit=min(1000, event.event_seq - last_seq),
                    )
                    for persisted in missing:
                        if persisted.event_seq > event.event_seq:
                            break
                        last_seq = persisted.event_seq
                        yield persisted
                        if persisted.event_type in {
                            "completed",
                            "failed",
                            "cancelled",
                        }:
                            return
                    continue
                last_seq = event.event_seq
                yield event
                if event.event_type in {"completed", "failed", "cancelled"}:
                    return
        finally:
            await subscription.close()

    async def principal_events(
        self,
        principal_id: str,
        *,
        after_id: int = 0,
        poll_interval: float = 0.5,
    ) -> AsyncIterator[PrincipalTaskEvent]:
        if not 0.1 <= poll_interval <= 10.0:
            raise ValueError("Principal event poll interval must be 0.1-10 seconds")
        cursor = after_id
        while True:
            page = await asyncio.to_thread(
                self._repository.list_principal_events,
                principal_id,
                after_id=cursor,
                limit=200,
            )
            if not page:
                await asyncio.sleep(poll_interval)
                continue
            for feed_event in page:
                cursor = feed_event.feed_event_id
                yield feed_event
