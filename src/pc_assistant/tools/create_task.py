"""Agent-facing creation of one Task with an explicit launch policy."""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from datetime import datetime
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

logger = logging.getLogger(__name__)


class DetachedSessionPort(Protocol):
    def create(self, principal_id: str, *, activate: bool = True) -> RuntimeScope: ...


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

    async def delete_definition(self, principal_id: str, task_id: str) -> None: ...


class SchedulePort(Protocol):
    async def create(
        self,
        scope: RuntimeScope,
        *,
        client_request_id: str,
        goal: str,
        spec: ScheduleSpec,
    ) -> ScheduleRecord: ...

    async def delete(self, principal_id: str, schedule_id: str) -> None: ...


class CreateTaskTool(ToolBase):
    name = "create_task"
    description = (
        "Create one independent Task with an explicit launch policy. The Task can start "
        "immediately, run once at a future time, repeat at a fixed interval, or follow "
        "a standard five-field Cron schedule."
    )
    details = (
        "Use launch.kind='immediate' for background work that should start now; "
        "'one_time' with an RFC 3339 timestamp for one future run; 'interval' with an "
        "interval in seconds; or 'cron' with exactly five fields (minute hour day month "
        "weekday) and an IANA timezone. The goal must be self-contained because the Task "
        "runs independently from the current conversation."
    )
    examples: ClassVar[list[dict[str, Any]]] = [
        {
            "title": "Organize today's downloads",
            "goal": "Organize the files in the Downloads directory by file type.",
            "launch": {"kind": "immediate"},
        },
        {
            "title": "Evening walking reminder",
            "goal": (
                "Check the weather in Shanghai. If it is not raining, remind the user "
                "to go outside for a walk."
            ),
            "launch": {
                "kind": "cron",
                "cron": "30 18 * * *",
                "timezone": "Asia/Shanghai",
            },
        },
        {
            "title": "One-time report reminder",
            "goal": "Remind the user to submit the daily report.",
            "launch": {
                "kind": "one_time",
                "at": "2030-01-02T18:30:00+08:00",
            },
        },
    ]
    effect = ToolEffect.INTERNAL_WRITE
    capabilities = frozenset({ToolCapability.TASK_MANAGEMENT})
    risk = ToolRisk.LOW

    def __init__(
        self,
        sessions: DetachedSessionPort,
        tasks: ProductTaskPort,
        schedules: SchedulePort,
    ) -> None:
        self._sessions = sessions
        self._tasks = tasks
        self._schedules = schedules

    async def execute(self, **kwargs: Any) -> Any:
        del kwargs
        return {"error": "Task creation requires an authenticated runtime scope"}

    @staticmethod
    def _timestamp(raw: Any, *, field: str) -> float:
        text = str(raw or "").strip()
        if not text:
            raise ValueError(f"launch.{field} is required")
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError(
                f"launch.{field} must be an RFC 3339 timestamp with a timezone offset"
            ) from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(
                f"launch.{field} must include a timezone offset, for example +08:00"
            )
        return parsed.timestamp()

    @staticmethod
    def _unexpected_fields(
        launch: dict[str, Any],
        *,
        allowed: frozenset[str],
    ) -> dict[str, str] | None:
        unexpected = sorted(set(launch) - allowed)
        if not unexpected:
            return None
        return {
            "error": (
                f"launch.kind does not accept these fields: {', '.join(unexpected)}"
            )
        }

    @staticmethod
    def _validation_message(exc: TypeError | ValueError) -> str:
        errors = getattr(exc, "errors", None)
        if callable(errors):
            details = errors()
            if details:
                message = str(details[0].get("msg", "")).strip()
                if message.startswith("Value error, "):
                    return message.removeprefix("Value error, ")
                if message:
                    return message
        return str(exc)

    async def _launch_policy(
        self,
        launch: dict[str, Any],
    ) -> tuple[TaskLaunchPolicy, ScheduleSpec | None, float | None] | dict[str, str]:
        kind = str(launch.get("kind", "")).strip()
        now = time.time()
        if kind == "immediate":
            invalid = self._unexpected_fields(
                launch,
                allowed=frozenset({"kind"}),
            )
            if invalid is not None:
                return invalid
            return TaskLaunchPolicy(kind=TaskLaunchKind.IMMEDIATE), None, None

        try:
            if kind == "one_time":
                invalid = self._unexpected_fields(
                    launch,
                    allowed=frozenset({"kind", "at"}),
                )
                if invalid is not None:
                    return invalid
                run_at = self._timestamp(launch.get("at"), field="at")
                spec = ScheduleSpec(
                    kind=ScheduleKind.ONE_TIME,
                    run_at=run_at,
                )
            elif kind == "interval":
                invalid = self._unexpected_fields(
                    launch,
                    allowed=frozenset({"kind", "at", "interval_seconds"}),
                )
                if invalid is not None:
                    return invalid
                if launch.get("interval_seconds") is None:
                    return {
                        "error": "launch.interval_seconds is required for interval"
                    }
                interval = float(launch["interval_seconds"])
                run_at = (
                    self._timestamp(launch.get("at"), field="at")
                    if launch.get("at") is not None
                    else now + interval
                )
                spec = ScheduleSpec(
                    kind=ScheduleKind.INTERVAL,
                    run_at=run_at,
                    interval_seconds=interval,
                )
            elif kind == "cron":
                invalid = self._unexpected_fields(
                    launch,
                    allowed=frozenset({"kind", "cron", "timezone"}),
                )
                if invalid is not None:
                    return invalid
                expression = str(launch.get("cron", "")).strip()
                if len(expression.split()) != 5:
                    return {
                        "error": (
                            "launch.cron must contain exactly five fields "
                            "(minute hour day month weekday), with no seconds. "
                            "For every day at 18:30, use '30 18 * * *'."
                        )
                    }
                spec = ScheduleSpec(
                    kind=ScheduleKind.CRON,
                    cron_expression=expression,
                    timezone=str(
                        launch.get("timezone", "Asia/Shanghai")
                    ).strip(),
                )
            else:
                return {
                    "error": (
                        "launch.kind must be one of: immediate, one_time, interval, cron"
                    )
                }
            due = await asyncio.to_thread(next_fire_at, spec, after=now)
        except (TypeError, ValueError) as exc:
            return {
                "error": (
                    f"Invalid launch policy: {self._validation_message(exc)}"
                )
            }
        if due is None:
            return {"error": "Invalid launch policy: no future occurrence exists"}
        policy = TaskLaunchPolicy(
            kind=TaskLaunchKind.SCHEDULED,
            schedule_type=spec.kind.value,
            run_at=spec.run_at,
            interval_seconds=spec.interval_seconds,
            cron=spec.cron_expression,
            timezone=spec.timezone,
        )
        return policy, spec, due

    async def execute_scoped(self, scope: RuntimeScope, **kwargs: Any) -> Any:
        title = str(kwargs.get("title", "")).strip()
        if not title:
            return {"error": "title is required"}
        goal = str(kwargs.get("goal", "")).strip()
        if not goal:
            return {"error": "goal is required"}
        launch = kwargs.get("launch")
        if not isinstance(launch, dict):
            return {"error": "launch must be an object with an explicit kind"}
        resolved = await self._launch_policy(launch)
        if isinstance(resolved, dict):
            return resolved
        policy, spec, due = resolved
        request_key = secrets.token_urlsafe(18)
        detached = await asyncio.to_thread(
            self._sessions.create,
            scope.principal_id,
            activate=False,
        )
        task, execution = await self._tasks.create_definition(
            detached,
            client_request_id=f"agent-task:{request_key}",
            title=title,
            goal=goal,
            launch_policy=policy,
        )
        schedule: ScheduleRecord | None = None
        if spec is not None:
            try:
                schedule = await self._schedules.create(
                    detached,
                    client_request_id=f"agent-schedule:{request_key}",
                    goal=goal,
                    spec=spec,
                )
                await self._tasks.bind_launch(
                    scope.principal_id,
                    task.task_id,
                    provider_kind="schedule",
                    provider_id=schedule.schedule_id,
                )
            except Exception:
                logger.exception("Failed to commit the Task launch policy")
                if schedule is not None:
                    try:
                        await self._schedules.delete(
                            scope.principal_id,
                            schedule.schedule_id,
                        )
                    except Exception:
                        logger.exception("Failed to roll back the Schedule")
                try:
                    await self._tasks.delete_definition(
                        scope.principal_id,
                        task.task_id,
                    )
                except Exception:
                    logger.exception("Failed to roll back the Task")
                return {
                    "error": "Task creation failed before the launch policy was committed"
                }
        return {
            "task_id": task.task_id,
            "execution_id": "" if execution is None else execution.execution_id,
            "state": task.state.value,
            "launch_kind": str(launch["kind"]),
            "next_fire_at": due,
            "message": (
                "Task created and execution started."
                if execution is not None
                else "Task created with its launch policy."
            ),
        }

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 200000,
                        "description": (
                            "A complete, self-contained goal for the independent Task."
                        ),
                    },
                    "title": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 200,
                        "description": "A short user-facing Task title.",
                    },
                    "launch": {
                        "type": "object",
                        "description": "The explicit policy that starts Task executions.",
                        "properties": {
                            "kind": {
                                "type": "string",
                                "enum": [
                                    "immediate",
                                    "one_time",
                                    "interval",
                                    "cron",
                                ],
                                "description": (
                                    "immediate starts now; one_time runs once at launch.at; "
                                    "interval repeats every launch.interval_seconds; cron "
                                    "uses launch.cron and launch.timezone."
                                ),
                            },
                            "at": {
                                "type": "string",
                                "format": "date-time",
                                "description": (
                                    "RFC 3339 timestamp with a timezone offset. Required "
                                    "for one_time and optional as the first interval run."
                                ),
                            },
                            "interval_seconds": {
                                "type": "number",
                                "minimum": 60,
                                "maximum": 31536000,
                                "description": (
                                    "Repeat interval in seconds. Used only by interval."
                                ),
                            },
                            "cron": {
                                "type": "string",
                                "maxLength": 128,
                                "description": (
                                    "Standard five-field Cron: minute hour day month "
                                    "weekday. Do not include seconds."
                                ),
                            },
                            "timezone": {
                                "type": "string",
                                "default": "Asia/Shanghai",
                                "description": (
                                    "IANA timezone for Cron, for example Asia/Shanghai."
                                ),
                            },
                        },
                        "required": ["kind"],
                        "additionalProperties": False,
                    },
                },
                "required": ["title", "goal", "launch"],
                "additionalProperties": False,
            },
        }
