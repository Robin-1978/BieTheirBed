"""Agent-facing creation of one Task with an explicit launch policy."""
from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Any, ClassVar, Protocol

from knoa_platform.agent_runtime.contracts import RuntimeScope
from knoa_platform.service.product_task_lifecycle import ProductTaskLifecycle
from knoa_platform.tools.base import ToolBase, ToolCapability, ToolEffect, ToolRisk
from knoa_platform.tools.task_launch import (
    LaunchPolicyError,
    launch_schema,
    public_launch,
    resolve_task_launch,
    timestamp_text,
)

logger = logging.getLogger(__name__)


class DetachedSessionPort(Protocol):
    def create(self, principal_id: str, *, activate: bool = True) -> RuntimeScope: ...


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
        {
            "title": "Hourly inbox review",
            "goal": "Review the inbox and summarize new high-priority messages.",
            "launch": {
                "kind": "interval",
                "interval_seconds": 3600,
                "at": "2030-01-02T09:00:00+08:00",
            },
        },
    ]
    effect = ToolEffect.INTERNAL_WRITE
    capabilities = frozenset({ToolCapability.TASK_MANAGEMENT})
    risk = ToolRisk.LOW

    def __init__(self, sessions: DetachedSessionPort, tasks: Any, schedules: Any) -> None:
        self._sessions = sessions
        self._lifecycle = ProductTaskLifecycle(tasks, schedules, None)

    async def execute(self, **kwargs: Any) -> Any:
        del kwargs
        return {"error": "Task creation requires an authenticated runtime scope"}

    async def execute_scoped(self, scope: RuntimeScope, **kwargs: Any) -> Any:
        title = str(kwargs.get("title", "")).strip()
        if not title:
            return {"error": "title is required"}
        goal = str(kwargs.get("goal", "")).strip()
        if not goal:
            return {"error": "goal is required"}
        try:
            resolved = await resolve_task_launch(kwargs.get("launch"))
        except LaunchPolicyError as exc:
            return {"error": str(exc)}
        request_key = secrets.token_urlsafe(18)
        detached = await asyncio.to_thread(
            self._sessions.create,
            scope.principal_id,
            activate=False,
        )
        try:
            task, execution, provider = await self._lifecycle.create_definition(
                detached,
                client_request_id=f"agent-task:{request_key}",
                title=title,
                goal=goal,
                launch_policy=resolved.policy,
            )
        except Exception:
            logger.exception("Failed to create the Task and its launch provider")
            return {
                "error": "Task creation failed before the launch policy was committed"
            }
        next_fire_at = provider.next_fire_at or resolved.next_fire_at
        return {
            "task_id": task.task_id,
            "execution_id": "" if execution is None else execution.execution_id,
            "state": task.state.value,
            "launch": public_launch(task.launch_policy),
            "next_fire_at": timestamp_text(next_fire_at),
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
                    "launch": launch_schema(),
                },
                "required": ["title", "goal", "launch"],
                "additionalProperties": False,
            },
        }
