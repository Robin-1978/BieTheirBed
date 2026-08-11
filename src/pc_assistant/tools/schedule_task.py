"""Agent-facing creation of a Task with a scheduled launch policy."""
from __future__ import annotations

import asyncio
import secrets
import time
from typing import Any, ClassVar, Protocol

from pc_assistant.agent_runtime.contracts import RuntimeScope
from pc_assistant.automation import (
    ScheduleKind,
    ScheduleRecord,
    ScheduleSpec,
    next_fire_at,
)
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
        "Cron uses exactly five fields (minute hour day month weekday), with no seconds. "
        "Returns the same public task_id used by the task query/control tool."
    )
    details = (
        "Choose one schedule mode: run_at, interval_seconds, or cron. An interval may "
        "also include run_at as its first-run anchor. run_at is a Unix timestamp in "
        "seconds. Cron is the standard five-field form: minute hour day month weekday; "
        "for example, '30 18 * * *' means every day at 18:30."
    )
    examples: ClassVar[list[dict[str, Any]]] = [
        {
            "title": "傍晚散步提醒",
            "goal": "查询上海天气，若不下雨则提醒用户散步。",
            "cron": "30 18 * * *",
            "timezone": "Asia/Shanghai",
        },
        {
            "title": "发送一次提醒",
            "goal": "提醒用户提交日报。",
            "run_at": 2_000_000_000,
        },
    ]
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
        modes = sum(
            (
                bool(cron),
                interval_seconds is not None,
                run_at is not None and interval_seconds is None,
            )
        )
        if modes == 0:
            return {"error": "Provide run_at, interval_seconds, or cron"}
        if modes > 1:
            return {"error": "Provide exactly one of run_at, interval_seconds, or cron"}
        if cron and len(cron.split()) != 5:
            return {
                "error": (
                    "Invalid cron: expected exactly five fields "
                    "(minute hour day month weekday), with no seconds. "
                    "For 18:30 every day, use '30 18 * * *'."
                )
            }
        try:
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
                    run_at=(
                        float(run_at)
                        if run_at is not None
                        else time.time() + interval
                    ),
                    interval_seconds=interval,
                    timezone=timezone,
                )
            else:
                spec = ScheduleSpec(
                    kind=ScheduleKind.ONE_TIME,
                    run_at=float(run_at),
                    timezone=timezone,
                )
            due = await asyncio.to_thread(next_fire_at, spec, after=time.time())
        except (TypeError, ValueError) as exc:
            return {"error": f"Invalid schedule: {exc}"}
        if due is None:
            return {"error": "Invalid schedule: the schedule has no future occurrence"}
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
                    "run_at": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Unix timestamp in seconds for a one-time task.",
                    },
                    "interval_seconds": {
                        "type": "number",
                        "minimum": 60,
                        "description": (
                            "Repeat interval in seconds. Optionally provide run_at as "
                            "the first-run Unix timestamp."
                        ),
                    },
                    "cron": {
                        "type": "string",
                        "maxLength": 128,
                        "description": (
                            "Standard five-field Cron: minute hour day month weekday. "
                            "Do not include seconds. Example: '30 18 * * *'."
                        ),
                    },
                    "timezone": {
                        "type": "string",
                        "default": "Asia/Shanghai",
                        "description": "IANA timezone name, for example Asia/Shanghai.",
                    },
                },
                "required": ["goal"],
                "oneOf": [
                    {
                        "required": ["run_at"],
                        "not": {
                            "anyOf": [
                                {"required": ["interval_seconds"]},
                                {"required": ["cron"]},
                            ]
                        },
                    },
                    {
                        "required": ["interval_seconds"],
                        "not": {"required": ["cron"]},
                    },
                    {
                        "required": ["cron"],
                        "not": {
                            "anyOf": [
                                {"required": ["run_at"]},
                                {"required": ["interval_seconds"]},
                            ]
                        },
                    },
                ],
                "additionalProperties": False,
            },
        }
