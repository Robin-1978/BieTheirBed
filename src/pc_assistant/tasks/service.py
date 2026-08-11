"""Application service for durable Task commands and event subscriptions."""
from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator

from pc_assistant.agent_runtime.contracts import (
    ArtifactAttachment,
    HealthStatus,
    RuntimeScope,
)
from pc_assistant.tasks.approval import DurableApprovalService
from pc_assistant.tasks.errors import TaskTransitionError
from pc_assistant.tasks.event_hub import TaskEventHub
from pc_assistant.tasks.executor import TaskExecutor
from pc_assistant.tasks.models import (
    TERMINAL_TASK_STATES,
    PrincipalTaskEvent,
    TaskApprovalRecord,
    TaskCancelResult,
    TaskDefinitionRecord,
    TaskDefinitionState,
    TaskEvent,
    TaskExecutionRecord,
    TaskExecutionTrace,
    TaskLaunchKind,
    TaskLaunchPolicy,
    TaskLaunchReason,
    TaskOrigin,
    TaskPauseResult,
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
        origin: TaskOrigin = TaskOrigin.USER,
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

    async def create_definition(
        self,
        scope: RuntimeScope,
        *,
        client_request_id: str,
        title: str,
        goal: str,
        attachments: tuple[ArtifactAttachment, ...] = (),
        tools_enabled: bool = True,
        priority: int = 0,
        launch_policy: TaskLaunchPolicy | None = None,
        notification_policy: dict[str, bool] | None = None,
    ) -> tuple[TaskDefinitionRecord, TaskExecutionRecord | None]:
        definition, created = await asyncio.to_thread(
            self._repository.create_task_definition,
            scope,
            client_request_id=client_request_id,
            title=title,
            goal=goal,
            attachments=attachments,
            tools_enabled=tools_enabled,
            priority=priority,
            launch_policy=launch_policy,
            notification_policy=notification_policy,
        )
        executions = await self.list_executions(
            scope.principal_id,
            definition.task_id,
            limit=1,
        )
        if executions:
            return definition, executions[0]
        if not created or definition.launch_policy.kind is not TaskLaunchKind.IMMEDIATE:
            return definition, None
        execution = await self.execute_definition(
            scope.principal_id,
            definition.task_id,
            client_request_id=f"execute:{client_request_id}",
            launch_reason=TaskLaunchReason.CREATED,
        )
        return await self.get_definition(scope.principal_id, definition.task_id), execution

    async def get_definition(
        self,
        principal_id: str,
        task_id: str,
    ) -> TaskDefinitionRecord:
        return await asyncio.to_thread(
            self._repository.get_task_definition,
            principal_id,
            task_id,
        )

    async def list_definitions(
        self,
        principal_id: str,
        *,
        state: TaskDefinitionState | None = None,
        include_archived: bool = False,
        limit: int = 100,
    ) -> tuple[TaskDefinitionRecord, ...]:
        return await asyncio.to_thread(
            self._repository.list_task_definitions,
            principal_id,
            state=state,
            include_archived=include_archived,
            limit=limit,
        )

    async def update_definition(
        self,
        principal_id: str,
        task_id: str,
        **changes,
    ) -> TaskDefinitionRecord:
        return await asyncio.to_thread(
            self._repository.update_task_definition,
            principal_id,
            task_id,
            **changes,
        )

    async def set_definition_state(
        self,
        principal_id: str,
        task_id: str,
        state: TaskDefinitionState,
    ) -> TaskDefinitionRecord:
        return await asyncio.to_thread(
            self._repository.set_task_definition_state,
            principal_id,
            task_id,
            state,
        )

    async def bind_launch(
        self,
        principal_id: str,
        task_id: str,
        *,
        provider_kind: str,
        provider_id: str,
    ) -> None:
        await asyncio.to_thread(
            self._repository.bind_task_launch,
            principal_id,
            task_id,
            provider_kind=provider_kind,
            provider_id=provider_id,
        )

    async def launch_binding(
        self,
        principal_id: str,
        task_id: str,
    ) -> tuple[str, str] | None:
        return await asyncio.to_thread(
            self._repository.launch_binding_for_task,
            principal_id,
            task_id,
        )

    async def unbind_launch(self, principal_id: str, task_id: str) -> tuple[str, str] | None:
        return await asyncio.to_thread(
            self._repository.unbind_launch,
            principal_id,
            task_id,
        )

    async def execute_bound_launch(
        self,
        principal_id: str,
        *,
        provider_kind: str,
        provider_id: str,
        client_request_id: str,
        launch_reason: TaskLaunchReason,
        goal_override: str = "",
    ) -> TaskExecutionRecord:
        definition = await asyncio.to_thread(
            self._repository.task_for_launch,
            principal_id,
            provider_kind=provider_kind,
            provider_id=provider_id,
        )
        if definition.state is not TaskDefinitionState.ACTIVE:
            raise TaskTransitionError("Task is not active")
        return await self.execute_definition(
            principal_id,
            definition.task_id,
            client_request_id=client_request_id,
            launch_reason=launch_reason,
            goal_override=goal_override,
        )

    async def execute_definition(
        self,
        principal_id: str,
        task_id: str,
        *,
        client_request_id: str = "",
        launch_reason: TaskLaunchReason = TaskLaunchReason.MANUAL,
        snapshot: TaskExecutionRecord | None = None,
        goal_override: str = "",
    ) -> TaskExecutionRecord:
        definition = await self.get_definition(principal_id, task_id)
        active = tuple(
            execution
            for execution in await self.list_executions(
                principal_id, task_id, limit=200
            )
            if execution.state not in TERMINAL_TASK_STATES
        )
        if active:
            raise TaskTransitionError("Task already has an active execution")
        scope = RuntimeScope(
            principal_id=definition.principal_id,
            session_handle=definition.session_handle,
        )
        request_id = client_request_id.strip() or (
            f"execution:{definition.task_id}:{secrets.token_urlsafe(12)}"
        )
        goal = definition.goal if snapshot is None else snapshot.goal_snapshot
        if goal_override.strip():
            goal = goal_override.strip()
        attachments = (
            definition.attachments
            if snapshot is None
            else snapshot.attachment_snapshots
        )
        policy = (
            definition.launch_policy if snapshot is None else snapshot.policy_snapshot
        )
        revision = definition.revision if snapshot is None else snapshot.task_revision
        origin = {
            TaskLaunchReason.SCHEDULED: TaskOrigin.SCHEDULED,
            TaskLaunchReason.EVENT: TaskOrigin.EVENT,
        }.get(launch_reason, TaskOrigin.USER)
        execution = await self.create(
            scope,
            client_request_id=request_id,
            goal=goal,
            attachments=attachments,
            tools_enabled=definition.tools_enabled,
            priority=definition.priority,
            origin=origin,
        )
        return await asyncio.to_thread(
            self._repository.link_task_execution,
            principal_id,
            definition.task_id,
            execution.task_id,
            launch_reason=launch_reason,
            goal_snapshot=goal,
            attachments_snapshot=attachments,
            policy_snapshot=policy,
            task_revision=revision,
        )

    async def get_execution(
        self,
        principal_id: str,
        execution_id: str,
        *,
        include_trace: bool = True,
    ) -> TaskExecutionRecord:
        execution = await asyncio.to_thread(
            self._repository.get_task_execution,
            principal_id,
            execution_id,
            include_trace=include_trace,
        )
        approvals = await asyncio.to_thread(
            self._repository.list_approvals,
            principal_id,
            execution.execution_id,
        )
        return execution.model_copy(update={"approvals": approvals})

    async def list_executions(
        self,
        principal_id: str,
        task_id: str,
        *,
        limit: int = 100,
    ) -> tuple[TaskExecutionRecord, ...]:
        executions = await asyncio.to_thread(
            self._repository.list_task_executions,
            principal_id,
            task_id,
            limit=limit,
        )
        hydrated: list[TaskExecutionRecord] = []
        for execution in executions:
            approvals = await asyncio.to_thread(
                self._repository.list_approvals,
                principal_id,
                execution.execution_id,
            )
            hydrated.append(execution.model_copy(update={"approvals": approvals}))
        return tuple(hydrated)

    async def rerun_execution(
        self,
        principal_id: str,
        execution_id: str,
        *,
        client_request_id: str = "",
    ) -> TaskExecutionRecord:
        previous = await self.get_execution(principal_id, execution_id)
        if previous.state not in TERMINAL_TASK_STATES:
            raise TaskTransitionError("Only terminal TaskExecutions can be rerun")
        return await self.execute_definition(
            principal_id,
            previous.task_id,
            client_request_id=client_request_id,
            launch_reason=TaskLaunchReason.RERUN,
            snapshot=previous,
        )

    async def delete_execution(self, principal_id: str, execution_id: str) -> None:
        await asyncio.to_thread(
            self._repository.delete_task_execution,
            principal_id,
            execution_id,
        )

    async def delete_definition(self, principal_id: str, task_id: str) -> None:
        await asyncio.to_thread(
            self._repository.delete_task_definition,
            principal_id,
            task_id,
        )

    async def get(self, principal_id: str, task_id: str) -> TaskRecord:
        return await asyncio.to_thread(
            self._repository.get,
            principal_id,
            task_id,
        )

    async def get_trace(
        self,
        principal_id: str,
        task_id: str,
    ) -> TaskExecutionTrace | None:
        return await asyncio.to_thread(
            self._repository.get_trace,
            principal_id,
            task_id,
        )

    async def compact_expired_traces(self) -> int:
        return await asyncio.to_thread(self._repository.compact_expired_traces)

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
        reconciliation_interval: float = 15.0,
    ) -> AsyncIterator[PrincipalTaskEvent]:
        if not 1.0 <= reconciliation_interval <= 300.0:
            raise ValueError(
                "Principal event reconciliation interval must be 1-300 seconds"
            )
        cursor = after_id
        feed_version = await self._events.feed_version()
        while True:
            page = await asyncio.to_thread(
                self._repository.list_principal_events,
                principal_id,
                after_id=cursor,
                limit=200,
            )
            if not page:
                feed_version = await self._events.wait_for_feed_change(
                    feed_version,
                    timeout=reconciliation_interval,
                )
                continue
            for feed_event in page:
                cursor = feed_event.feed_event_id
                yield feed_event
