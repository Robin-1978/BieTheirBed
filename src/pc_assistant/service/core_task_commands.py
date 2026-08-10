"""Task definition and execution Core command handlers."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pc_assistant.agent_runtime.contracts import ArtifactAttachment, RuntimeScope
from pc_assistant.automation import (
    ScheduleKind,
    ScheduleService,
    ScheduleSpec,
    TriggerService,
)
from pc_assistant.service.core_api import (
    ApprovalResolvedMessage,
    CancelTaskRequest,
    CreateProductTaskRequest,
    CreateTaskRequest,
    DeleteProductTaskExecutionRequest,
    DeleteProductTaskRequest,
    ExecuteProductTaskRequest,
    GetProductTaskExecutionRequest,
    GetProductTaskRequest,
    GetTaskRequest,
    HealthMessage,
    HealthRequest,
    ListProductTaskExecutionsRequest,
    ListProductTasksRequest,
    ListTasksRequest,
    PauseTaskRequest,
    ProductTaskDeletedMessage,
    ProductTaskExecutionListMessage,
    ProductTaskExecutionMessage,
    ProductTaskExecutionSnapshot,
    ProductTaskListMessage,
    ProductTaskMessage,
    ProductTaskSnapshot,
    RerunProductTaskExecutionRequest,
    ResolveApprovalRequest,
    ResumeTaskRequest,
    SetProductTaskStateRequest,
    TaskAcceptedMessage,
    TaskCancelResultMessage,
    TaskListMessage,
    TaskPauseResultMessage,
    TaskResumedMessage,
    TaskSnapshot,
    TaskSnapshotMessage,
    UpdateProductTaskRequest,
)
from pc_assistant.tasks import (
    TaskDefinitionState,
    TaskLaunchKind,
    TaskService,
)

Send = Callable[[Any], Awaitable[None]]


class TaskCommandHandler:
    def __init__(
        self,
        tasks: TaskService,
        schedules: ScheduleService,
        triggers: TriggerService,
    ) -> None:
        self._tasks = tasks
        self._schedules = schedules
        self._triggers = triggers

    async def _remove_launch_provider(self, principal: str, task_id: str) -> None:
        binding = await self._tasks.unbind_launch(principal, task_id)
        if binding is None:
            return
        provider_kind, provider_id = binding
        if provider_kind == "schedule":
            await self._schedules.delete(principal, provider_id)
        elif provider_kind == "event":
            await self._triggers.delete(principal, provider_id)

    async def _create_launch_provider(
        self,
        principal: str,
        task: Any,
        client_request_id: str,
    ) -> None:
        policy = task.launch_policy
        if policy.kind is TaskLaunchKind.IMMEDIATE:
            return
        scope = RuntimeScope(
            principal_id=principal,
            session_handle=task.session_handle,
        )
        if policy.kind is TaskLaunchKind.SCHEDULED:
            if policy.schedule_type is None:
                raise ValueError("Scheduled Task requires schedule_type")
            schedule = await self._schedules.create(
                scope,
                client_request_id=client_request_id,
                goal=task.goal,
                spec=ScheduleSpec(
                    kind=ScheduleKind(policy.schedule_type),
                    run_at=policy.run_at,
                    interval_seconds=policy.interval_seconds,
                    cron_expression=policy.cron,
                    timezone=policy.timezone,
                ),
                tools_enabled=task.tools_enabled,
                priority=task.priority,
            )
            await self._tasks.bind_launch(
                principal,
                task.task_id,
                provider_kind="schedule",
                provider_id=schedule.schedule_id,
            )
            return
        trigger = await self._triggers.create(
            scope,
            client_request_id=client_request_id,
            name=task.title,
            goal=task.goal,
            tools_enabled=task.tools_enabled,
            priority=task.priority,
        )
        await self._tasks.bind_launch(
            principal,
            task.task_id,
            provider_kind="event",
            provider_id=trigger.trigger_id,
        )

    async def _replace_launch_provider(self, principal: str, task: Any) -> None:
        await self._remove_launch_provider(principal, task.task_id)
        await self._create_launch_provider(
            principal,
            task,
            f"task-launch:{task.task_id}:r{task.revision}",
        )

    async def dispatch(self, principal: str, request: Any, send: Send) -> bool:
        if isinstance(request, CreateTaskRequest):
            scope = RuntimeScope(
                principal_id=principal,
                session_handle=request.session_handle,
            )
            task = await self._tasks.create(
                scope,
                client_request_id=request.request_id,
                goal=request.input,
                attachments=tuple(
                    ArtifactAttachment(
                        artifact_id=item.artifact_id,
                        caption=item.caption,
                    )
                    for item in request.attachments
                ),
                tools_enabled=request.tools_enabled,
                priority=request.priority,
                parent_task_id=request.parent_task_id,
                origin=request.origin,
            )
            await send(TaskAcceptedMessage(
                request_id=request.request_id,
                task_id=task.task_id,
                state=task.state,
            ))
        elif isinstance(request, GetTaskRequest):
            task = await self._tasks.get(principal, request.task_id)
            trace = await self._tasks.get_trace(principal, request.task_id)
            await send(TaskSnapshotMessage(
                request_id=request.request_id,
                task=TaskSnapshot.from_record(task, trace=trace),
            ))
        elif isinstance(request, ListTasksRequest):
            list_kwargs: dict[str, Any] = {
                "session_handle": request.session_handle,
                "state": request.state,
                "limit": request.limit,
                "cursor": request.cursor,
            }
            if request.origins:
                list_kwargs["origins"] = request.origins
            tasks, next_cursor = await self._tasks.list(principal, **list_kwargs)
            await send(TaskListMessage(
                request_id=request.request_id,
                tasks=tuple(TaskSnapshot.from_record(task) for task in tasks),
                next_cursor=next_cursor,
            ))
        elif isinstance(request, CreateProductTaskRequest):
            task, execution = await self._tasks.create_definition(
                RuntimeScope(
                    principal_id=principal,
                    session_handle=request.session_handle,
                ),
                client_request_id=request.client_request_id,
                title=request.title,
                goal=request.goal,
                attachments=tuple(
                    ArtifactAttachment(
                        artifact_id=item.artifact_id,
                        caption=item.caption,
                    )
                    for item in request.attachments
                ),
                tools_enabled=request.tools_enabled,
                priority=request.priority,
                launch_policy=request.launch_policy,
                notification_policy=request.notification_policy or None,
            )
            await self._create_launch_provider(
                principal,
                task,
                f"task-launch:{task.task_id}",
            )
            await send(ProductTaskMessage(
                request_id=request.request_id,
                task=ProductTaskSnapshot.from_record(task),
                execution=(
                    None
                    if execution is None
                    else ProductTaskExecutionSnapshot.from_record(execution)
                ),
            ))
        elif isinstance(request, GetProductTaskRequest):
            task = await self._tasks.get_definition(principal, request.task_id)
            await send(ProductTaskMessage(
                request_id=request.request_id,
                task=ProductTaskSnapshot.from_record(task),
            ))
        elif isinstance(request, ListProductTasksRequest):
            tasks = await self._tasks.list_definitions(
                principal,
                state=request.state,
                include_archived=request.include_archived,
                limit=request.limit,
            )
            await send(ProductTaskListMessage(
                request_id=request.request_id,
                tasks=tuple(ProductTaskSnapshot.from_record(task) for task in tasks),
            ))
        elif isinstance(request, UpdateProductTaskRequest):
            changes: dict[str, Any] = {
                "title": request.title,
                "goal": request.goal,
                "tools_enabled": request.tools_enabled,
                "priority": request.priority,
                "launch_policy": request.launch_policy,
                "notification_policy": request.notification_policy,
                "expected_revision": request.expected_revision,
            }
            if request.attachments is not None:
                changes["attachments"] = tuple(
                    ArtifactAttachment(
                        artifact_id=item.artifact_id,
                        caption=item.caption,
                    )
                    for item in request.attachments
                )
            task = await self._tasks.update_definition(
                principal,
                request.task_id,
                **{key: value for key, value in changes.items() if value is not None},
            )
            if any(value is not None for value in (
                request.title,
                request.goal,
                request.tools_enabled,
                request.priority,
                request.launch_policy,
            )):
                await self._replace_launch_provider(principal, task)
            await send(ProductTaskMessage(
                request_id=request.request_id,
                task=ProductTaskSnapshot.from_record(task),
            ))
        elif isinstance(request, SetProductTaskStateRequest):
            binding = await self._tasks.launch_binding(principal, request.task_id)
            if binding is not None:
                provider_kind, provider_id = binding
                paused = request.state is not TaskDefinitionState.ACTIVE
                if provider_kind == "schedule":
                    if paused:
                        await self._schedules.pause(principal, provider_id)
                    else:
                        await self._schedules.resume(principal, provider_id)
                elif provider_kind == "event":
                    await self._triggers.set_paused(
                        principal,
                        provider_id,
                        paused=paused,
                    )
            task = await self._tasks.set_definition_state(
                principal,
                request.task_id,
                request.state,
            )
            await send(ProductTaskMessage(
                request_id=request.request_id,
                task=ProductTaskSnapshot.from_record(task),
            ))
        elif isinstance(request, DeleteProductTaskRequest):
            await self._remove_launch_provider(principal, request.task_id)
            await self._tasks.delete_definition(principal, request.task_id)
            await send(ProductTaskDeletedMessage(
                request_id=request.request_id,
                task_id=request.task_id,
            ))
        elif isinstance(request, ExecuteProductTaskRequest):
            execution = await self._tasks.execute_definition(
                principal,
                request.task_id,
                client_request_id=request.request_id,
            )
            await send(ProductTaskExecutionMessage(
                request_id=request.request_id,
                execution=ProductTaskExecutionSnapshot.from_record(execution),
            ))
        elif isinstance(request, GetProductTaskExecutionRequest):
            execution = await self._tasks.get_execution(principal, request.execution_id)
            await send(ProductTaskExecutionMessage(
                request_id=request.request_id,
                execution=ProductTaskExecutionSnapshot.from_record(execution),
            ))
        elif isinstance(request, ListProductTaskExecutionsRequest):
            executions = await self._tasks.list_executions(
                principal,
                request.task_id,
                limit=request.limit,
            )
            await send(ProductTaskExecutionListMessage(
                request_id=request.request_id,
                executions=tuple(
                    ProductTaskExecutionSnapshot.from_record(execution)
                    for execution in executions
                ),
            ))
        elif isinstance(request, DeleteProductTaskExecutionRequest):
            await self._tasks.delete_execution(principal, request.execution_id)
            await send(ProductTaskDeletedMessage(
                request_id=request.request_id,
                execution_id=request.execution_id,
            ))
        elif isinstance(request, RerunProductTaskExecutionRequest):
            execution = await self._tasks.rerun_execution(
                principal,
                request.execution_id,
                client_request_id=request.request_id,
            )
            await send(ProductTaskExecutionMessage(
                request_id=request.request_id,
                execution=ProductTaskExecutionSnapshot.from_record(execution),
            ))
        elif isinstance(request, CancelTaskRequest):
            result = await self._tasks.cancel(
                principal,
                request.task_id,
                reason=request.reason,
            )
            await send(TaskCancelResultMessage(
                request_id=request.request_id,
                result=result,
            ))
        elif isinstance(request, PauseTaskRequest):
            result = await self._tasks.pause(
                principal,
                request.task_id,
                reason=request.reason,
            )
            await send(TaskPauseResultMessage(
                request_id=request.request_id,
                result=result,
            ))
        elif isinstance(request, ResumeTaskRequest):
            task = await self._tasks.resume(
                principal,
                request.task_id,
                reason=request.reason,
                acknowledge_outcome_unknown=request.acknowledge_outcome_unknown,
            )
            await send(TaskResumedMessage(
                request_id=request.request_id,
                task_id=task.task_id,
                state=task.state,
            ))
        elif isinstance(request, ResolveApprovalRequest):
            approval, changed = await self._tasks.resolve_approval(
                principal,
                request.approval_id,
                approved=request.approved,
                resolved_by="core_api",
            )
            await send(ApprovalResolvedMessage(
                request_id=request.request_id,
                approval_id=approval.approval_id,
                resolved=changed,
                state=approval.state,
            ))
        elif isinstance(request, HealthRequest):
            await send(HealthMessage(
                request_id=request.request_id,
                result=await self._tasks.health_check(),
            ))
        else:
            return False
        return True
