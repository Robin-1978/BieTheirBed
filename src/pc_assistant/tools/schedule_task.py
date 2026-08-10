"""Agent-facing creation of a Task with a scheduled launch policy."""
from __future__ import annotations

import asyncio
import secrets
import time
from typing import Any, Protocol

from pc_assistant.agent_runtime.contracts import RuntimeScope
from pc_assistant.automation import ScheduleKind, ScheduleRecord, ScheduleSpec
from pc_assistant.tasks import (
    TaskDefinitionRecord,
    TaskExecutionRecord,
    TaskLaunchKind,
    TaskLaunchPolicy,
)
from pc_assistant.tools.base import ToolBase, ToolCapability, ToolEffect, ToolRisk


class DetachedSessionPort(Protocol):
    def create(self, principal_id: str, *, activate: bool = True) -> RuntimeScope: ...


class ScheduleTaskPort(Protocol):
    async def create(
        self,
        scope: RuntimeScope,
        *,
        client_request_id: str,
        goal: str,
        spec: ScheduleSpec,
    ) -> ScheduleRecord: ...


class ProductTaskPort(Protocol):
    async def create_definition(
        self,
        scope: RuntimeScope,
        *,
        client_request_id: str,
        title: str,
        goal: str,
        launch_policy: TaskLaunchPolicy,
    ) -> tuple[TaskDefinitionRecord, TaskExecutionRecord | None]: ...

    async def bind_launch(
        self,
        principal_id: str,
        task_id: str,
        *,
        provider_kind: str,
        provider_id: str,
    ) -> None: ...


class ScheduleTaskTool(ToolBase):
    name = "schedule_task"
    description = (
        "Create one Task whose launch policy is one-time, interval, or Cron. "
        "Returns the same public task_id used by the task query/control tool."
    )
    effect = ToolEffect.INTERNAL_WRITE
    capabilities = frozenset({ToolCapability.TASK_MANAGEMENT})
    risk = ToolRisk.LOW

    def __init__(
        self,
        sessions: DetachedSessionPort,
        tasks: ProductTaskPort,
        schedules: ScheduleTaskPort,
    ) -> None:
        self._sessions = sessions
        self._tasks = tasks
        self._schedules = schedules

    async def execute(self, **kwargs: Any) -> Any:
        del kwargs
        return {"error": "Scheduled Task creation requires an authenticated scope"}

    async def execute_scoped(self, scope: RuntimeScope, **kwargs: Any) -> Any:
        goal = str(kwargs.get("goal", "")).strip()
        run_at = kwargs.get("run_at")
        interval_seconds = kwargs.get("interval_seconds")
        cron = str(kwargs.get("cron", "")).strip()
        timezone = str(kwargs.get("timezone", "Asia/Shanghai")).strip()
        if cron:
            spec = ScheduleSpec(
                kind=ScheduleKind.CRON,
                cron_expression=cron,
                timezone=timezone,
            )
        elif interval_seconds is not None:
            interval = float(interval_seconds)
            spec = ScheduleSpec(
                kind=ScheduleKind.INTERVAL,
                run_at=float(run_at) if run_at is not None else time.time() + interval,
                interval_seconds=interval,
                timezone=timezone,
            )
        elif run_at is not None:
            spec = ScheduleSpec(
                kind=ScheduleKind.ONE_TIME,
                run_at=float(run_at),
                timezone=timezone,
            )
        else:
            return {"error": "Provide run_at, interval_seconds, or cron"}
        detached = await asyncio.to_thread(
            self._sessions.create,
            scope.principal_id,
            activate=False,
        )
        schedule = await self._schedules.create(
            detached,
            client_request_id=f"agent-schedule:{secrets.token_urlsafe(16)}",
            goal=goal,
            spec=spec,
        )
        policy = TaskLaunchPolicy(
            kind=TaskLaunchKind.SCHEDULED,
            schedule_type=spec.kind.value,
            run_at=spec.run_at,
            interval_seconds=spec.interval_seconds,
            cron=spec.cron_expression,
            timezone=spec.timezone,
        )
        task, _execution = await self._tasks.create_definition(
            detached,
            client_request_id=f"agent-task:{secrets.token_urlsafe(16)}",
            title=str(kwargs.get("title", "")).strip(),
            goal=goal,
            launch_policy=policy,
        )
        await self._tasks.bind_launch(
            scope.principal_id,
            task.task_id,
            provider_kind="schedule",
            provider_id=schedule.schedule_id,
        )
        return {
            "task_id": task.task_id,
            "launch_policy": "scheduled",
            "state": task.state.value,
            "next_fire_at": schedule.next_fire_at,
        }

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "minLength": 1, "maxLength": 200000},
                    "title": {"type": "string", "maxLength": 200},
                    "run_at": {"type": "number", "minimum": 0},
                    "interval_seconds": {"type": "number", "minimum": 60},
                    "cron": {"type": "string", "maxLength": 128},
                    "timezone": {"type": "string", "default": "Asia/Shanghai"},
                },
                "required": ["goal"],
                "additionalProperties": False,
            },
        }
