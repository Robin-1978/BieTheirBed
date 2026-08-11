"""Shared Task-definition lifecycle orchestration for every product surface."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pc_assistant.agent_runtime.contracts import RuntimeScope
from pc_assistant.automation import (
    ScheduleKind,
    ScheduleService,
    ScheduleSpec,
    ScheduleState,
    TriggerService,
    TriggerState,
)
from pc_assistant.tasks import (
    TERMINAL_TASK_STATES,
    TaskDefinitionRecord,
    TaskDefinitionState,
    TaskExecutionRecord,
    TaskLaunchKind,
    TaskService,
    TaskTransitionError,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LaunchProviderStatus:
    state: str | None = None
    next_fire_at: float | None = None


class ProductTaskLifecycle:
    """Coordinate Task definitions with their internal launch providers."""

    def __init__(
        self,
        tasks: TaskService,
        schedules: ScheduleService,
        triggers: TriggerService | None,
    ) -> None:
        self._tasks = tasks
        self._schedules = schedules
        self._triggers = triggers

    async def _delete_provider(
        self,
        principal_id: str,
        provider_kind: str,
        provider_id: str,
    ) -> None:
        if provider_kind == "schedule":
            await self._schedules.delete(principal_id, provider_id)
        elif provider_kind == "event" and self._triggers is not None:
            await self._triggers.delete(principal_id, provider_id)

    async def _remove_launch_provider(self, principal_id: str, task_id: str) -> None:
        binding = await self._tasks.launch_binding(principal_id, task_id)
        if binding is None:
            return
        try:
            await self._delete_provider(principal_id, *binding)
        except LookupError:
            logger.warning("Task launch provider was already missing")
        await self._tasks.unbind_launch(principal_id, task_id)

    async def _create_launch_provider(
        self,
        principal_id: str,
        task: TaskDefinitionRecord,
        client_request_id: str,
    ) -> LaunchProviderStatus:
        policy = task.launch_policy
        if policy.kind is TaskLaunchKind.IMMEDIATE:
            return LaunchProviderStatus()
        scope = RuntimeScope(
            principal_id=principal_id,
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
            try:
                await self._tasks.bind_launch(
                    principal_id,
                    task.task_id,
                    provider_kind="schedule",
                    provider_id=schedule.schedule_id,
                )
            except Exception:
                try:
                    await self._schedules.delete(principal_id, schedule.schedule_id)
                except Exception:
                    logger.exception("Failed to roll back the Schedule")
                raise
            return LaunchProviderStatus(
                state=schedule.state.value,
                next_fire_at=schedule.next_fire_at,
            )
        if self._triggers is None:
            raise ValueError("Event Task requires TriggerService")
        trigger = await self._triggers.create(
            scope,
            client_request_id=client_request_id,
            name=task.title,
            goal=task.goal,
            tools_enabled=task.tools_enabled,
            priority=task.priority,
        )
        try:
            await self._tasks.bind_launch(
                principal_id,
                task.task_id,
                provider_kind="event",
                provider_id=trigger.trigger_id,
            )
        except Exception:
            try:
                await self._triggers.delete(principal_id, trigger.trigger_id)
            except Exception:
                logger.exception("Failed to roll back the Trigger")
            raise
        return LaunchProviderStatus(state=trigger.state.value)

    async def create_definition(
        self,
        scope: RuntimeScope,
        *,
        client_request_id: str,
        title: str,
        goal: str,
        **options: Any,
    ) -> tuple[TaskDefinitionRecord, TaskExecutionRecord | None, LaunchProviderStatus]:
        task, execution = await self._tasks.create_definition(
            scope,
            client_request_id=client_request_id,
            title=title,
            goal=goal,
            **options,
        )
        try:
            provider = await self._create_launch_provider(
                scope.principal_id,
                task,
                f"task-launch:{task.task_id}",
            )
        except Exception:
            try:
                await self._tasks.delete_definition(scope.principal_id, task.task_id)
            except Exception:
                logger.exception("Failed to roll back the Task")
            raise
        return task, execution, provider

    async def launch_status(
        self,
        principal_id: str,
        task_id: str,
    ) -> LaunchProviderStatus:
        binding = await self._tasks.launch_binding(principal_id, task_id)
        if binding is None:
            return LaunchProviderStatus()
        provider_kind, provider_id = binding
        if provider_kind == "schedule":
            try:
                schedule = await self._schedules.get(principal_id, provider_id)
            except LookupError:
                return LaunchProviderStatus(state="missing")
            return LaunchProviderStatus(
                state=schedule.state.value,
                next_fire_at=schedule.next_fire_at,
            )
        if provider_kind == "event" and self._triggers is not None:
            try:
                trigger = await self._triggers.get(principal_id, provider_id)
            except LookupError:
                return LaunchProviderStatus(state="missing")
            return LaunchProviderStatus(state=trigger.state.value)
        return LaunchProviderStatus()

    async def set_definition_state(
        self,
        principal_id: str,
        task_id: str,
        state: TaskDefinitionState,
    ) -> TaskDefinitionRecord:
        binding = await self._tasks.launch_binding(principal_id, task_id)
        prior_state: str | None = None
        if binding is not None:
            provider_kind, provider_id = binding
            paused = state is not TaskDefinitionState.ACTIVE
            if provider_kind == "schedule":
                provider = await self._schedules.get(principal_id, provider_id)
                prior_state = provider.state.value
                if paused:
                    await self._schedules.pause(principal_id, provider_id)
                else:
                    await self._schedules.resume(principal_id, provider_id)
            elif provider_kind == "event" and self._triggers is not None:
                provider = await self._triggers.get(principal_id, provider_id)
                prior_state = provider.state.value
                await self._triggers.set_paused(
                    principal_id,
                    provider_id,
                    paused=paused,
                )
        try:
            return await self._tasks.set_definition_state(
                principal_id,
                task_id,
                state,
            )
        except Exception:
            if binding is not None and prior_state is not None:
                provider_kind, provider_id = binding
                try:
                    if provider_kind == "schedule":
                        if prior_state == ScheduleState.PAUSED.value:
                            await self._schedules.pause(principal_id, provider_id)
                        elif prior_state == ScheduleState.ACTIVE.value:
                            await self._schedules.resume(principal_id, provider_id)
                    elif provider_kind == "event" and self._triggers is not None:
                        await self._triggers.set_paused(
                            principal_id,
                            provider_id,
                            paused=prior_state == TriggerState.PAUSED.value,
                        )
                except Exception:
                    logger.exception("Failed to restore the launch provider state")
            raise

    async def update_definition(
        self,
        principal_id: str,
        task_id: str,
        **changes: Any,
    ) -> TaskDefinitionRecord:
        previous = await self._tasks.get_definition(principal_id, task_id)
        task = await self._tasks.update_definition(principal_id, task_id, **changes)
        if {
            "title",
            "goal",
            "tools_enabled",
            "priority",
            "launch_policy",
        } & changes.keys():
            try:
                await self._remove_launch_provider(principal_id, task.task_id)
                await self._create_launch_provider(
                    principal_id,
                    task,
                    f"task-launch:{task.task_id}:r{task.revision}",
                )
                if task.state is not TaskDefinitionState.ACTIVE:
                    await self.set_definition_state(
                        principal_id,
                        task.task_id,
                        task.state,
                    )
            except Exception:
                logger.exception("Failed to replace the Task launch provider")
                try:
                    await self._remove_launch_provider(principal_id, task.task_id)
                except Exception:
                    logger.exception("Failed to discard the replacement launch provider")
                try:
                    restored = await self._tasks.update_definition(
                        principal_id,
                        task.task_id,
                        title=previous.title,
                        goal=previous.goal,
                        attachments=previous.attachments,
                        tools_enabled=previous.tools_enabled,
                        priority=previous.priority,
                        launch_policy=previous.launch_policy,
                        notification_policy=previous.notification_policy,
                    )
                    await self._create_launch_provider(
                        principal_id,
                        restored,
                        f"task-launch:{task.task_id}:rollback:r{restored.revision}",
                    )
                    if previous.state is not TaskDefinitionState.ACTIVE:
                        await self.set_definition_state(
                            principal_id,
                            restored.task_id,
                            previous.state,
                        )
                except Exception:
                    logger.exception("Failed to restore the previous Task definition")
                raise
        return task

    async def delete_definition(self, principal_id: str, task_id: str) -> None:
        task = await self._tasks.get_definition(principal_id, task_id)
        active = tuple(
            execution
            for execution in await self._tasks.list_executions(
                principal_id,
                task_id,
                limit=200,
            )
            if execution.state not in TERMINAL_TASK_STATES
        )
        if active:
            raise TaskTransitionError(
                "Task has active executions; stop them before deleting"
            )
        await self._remove_launch_provider(principal_id, task_id)
        try:
            await self._tasks.delete_definition(principal_id, task_id)
        except Exception:
            try:
                await self._create_launch_provider(
                    principal_id,
                    task,
                    f"task-launch:{task.task_id}:delete-rollback:r{task.revision}",
                )
                if task.state is not TaskDefinitionState.ACTIVE:
                    await self.set_definition_state(
                        principal_id,
                        task.task_id,
                        task.state,
                    )
            except Exception:
                logger.exception("Failed to restore the Task launch provider")
            raise
