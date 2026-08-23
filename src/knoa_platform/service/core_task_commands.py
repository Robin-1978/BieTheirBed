"""Task definition and execution Core command handlers."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from knoa_platform.agent_runtime.contracts import ArtifactAttachment, RuntimeScope
from knoa_platform.automation import (
    ScheduleService,
    TriggerService,
)
from knoa_platform.service.core_api import (
    ApprovalResolvedMessage,
    CancelTaskRequest,
    ContinueProductTaskRequest,
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
from knoa_platform.service.product_task_lifecycle import ProductTaskLifecycle
from knoa_platform.tasks import TaskService

Send = Callable[[Any], Awaitable[None]]


class TaskCommandHandler:
    def __init__(
        self,
        tasks: TaskService,
        schedules: ScheduleService,
        triggers: TriggerService,
    ) -> None:
        self._tasks = tasks
        self._lifecycle = ProductTaskLifecycle(tasks, schedules, triggers)

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
                agent_id=request.agent_id,
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
            task, execution, _provider = await self._lifecycle.create_definition(
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
                auto_launch=request.auto_launch,
                launch_policy=request.launch_policy,
                notification_policy=request.notification_policy or None,
                agent_id=request.agent_id,
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
            task = await self._lifecycle.update_definition(
                principal,
                request.task_id,
                **{key: value for key, value in changes.items() if value is not None},
            )
            await send(ProductTaskMessage(
                request_id=request.request_id,
                task=ProductTaskSnapshot.from_record(task),
            ))
        elif isinstance(request, SetProductTaskStateRequest):
            task = await self._lifecycle.set_definition_state(
                principal,
                request.task_id,
                request.state,
            )
            await send(ProductTaskMessage(
                request_id=request.request_id,
                task=ProductTaskSnapshot.from_record(task),
            ))
        elif isinstance(request, DeleteProductTaskRequest):
            await self._lifecycle.delete_definition(principal, request.task_id)
            await send(ProductTaskDeletedMessage(
                request_id=request.request_id,
                task_id=request.task_id,
            ))
        elif isinstance(request, ExecuteProductTaskRequest):
            execution = await self._tasks.execute_definition(
                principal,
                request.task_id,
                client_request_id=request.request_id,
                launch_reason=request.launch_reason,
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
        elif isinstance(request, ContinueProductTaskRequest):
            execution = await self._tasks.continue_definition(
                principal,
                request.task_id,
                client_request_id=request.client_request_id,
                input=request.input,
                attachments=tuple(
                    ArtifactAttachment(
                        artifact_id=item.artifact_id,
                        caption=item.caption,
                    )
                    for item in request.attachments
                ),
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
