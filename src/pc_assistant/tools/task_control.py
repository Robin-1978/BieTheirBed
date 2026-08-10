"""Compact Agent query/control surface for Tasks and their executions."""
from __future__ import annotations

import asyncio
import secrets
from typing import Any

from pc_assistant.agent_runtime.contracts import RuntimeScope
from pc_assistant.automation import ScheduleState, TriggerState
from pc_assistant.tasks import TERMINAL_TASK_STATES, TaskOrigin, TaskState
from pc_assistant.tools.base import ToolBase, ToolCapability, ToolEffect, ToolPolicy, ToolRisk


class TaskControlTool(ToolBase):
    name = "task"
    description = "List, inspect, pause, resume, cancel, or retry Tasks by public task_id."
    effect = ToolEffect.INTERNAL_WRITE
    capabilities = frozenset({ToolCapability.TASK_MANAGEMENT})
    risk = ToolRisk.LOW

    def __init__(self, sessions: Any, executions: Any, schedules: Any, triggers: Any) -> None:
        self._sessions = sessions
        self._executions = executions
        self._schedules = schedules
        self._triggers = triggers

    def policy_for(self, arguments: dict[str, Any]) -> ToolPolicy:
        if arguments.get("action") in {"list", "get"}:
            return ToolPolicy(
                effect=ToolEffect.READ_ONLY,
                capabilities=self.capabilities,
                risk=ToolRisk.LOW,
            )
        return self.policy

    async def execute(self, **kwargs: Any) -> Any:
        del kwargs
        return {"error": "Task access requires an authenticated scope"}

    async def execute_scoped(self, scope: RuntimeScope, **kwargs: Any) -> Any:
        action = str(kwargs.get("action", "")).strip()
        task_id = str(kwargs.get("task_id", "")).strip()
        if action == "list":
            executions, _cursor = await self._executions.list(
                scope.principal_id,
                origins=(TaskOrigin.USER, TaskOrigin.AGENT),
                limit=50,
            )
            schedules = await self._schedules.list(scope.principal_id, limit=50)
            triggers = await self._triggers.list(scope.principal_id, limit=50)
            items = [
                {
                    "task_id": item.task_id,
                    "goal": item.goal,
                    "launch_policy": "immediate",
                    "state": item.state.value,
                    "latest_execution_id": item.task_id,
                }
                for item in executions
            ]
            items.extend(
                {
                    "task_id": item.schedule_id,
                    "goal": item.goal,
                    "launch_policy": "scheduled",
                    "state": item.state.value,
                    "next_fire_at": item.next_fire_at,
                }
                for item in schedules
            )
            items.extend(
                {
                    "task_id": item.trigger_id,
                    "goal": item.goal,
                    "launch_policy": "event",
                    "state": item.state.value,
                }
                for item in triggers
            )
            return {"tasks": items}
        if not task_id:
            return {"error": "task_id is required"}
        target = await self._resolve(scope.principal_id, task_id)
        if target is None:
            return {"error": "Task not found"}
        kind, record = target
        if action == "get":
            return self._snapshot(kind, record)
        if action == "pause":
            if kind == "execution":
                record = await self._executions.pause(scope.principal_id, task_id, reason="Agent request")
                return {"task_id": task_id, "state": record.state.value}
            if kind == "scheduled":
                record = await self._schedules.pause(scope.principal_id, task_id)
            else:
                record = await self._triggers.set_paused(scope.principal_id, task_id, paused=True)
            return {"task_id": task_id, "state": record.state.value}
        if action == "resume":
            if kind == "execution":
                record = await self._executions.resume(scope.principal_id, task_id, reason="Agent request")
            elif kind == "scheduled":
                record = await self._schedules.resume(scope.principal_id, task_id)
            else:
                record = await self._triggers.set_paused(scope.principal_id, task_id, paused=False)
            return {"task_id": task_id, "state": record.state.value}
        if action == "cancel":
            if kind != "execution":
                return {"error": "Pause the Task to disable future launches"}
            result = await self._executions.cancel(scope.principal_id, task_id, reason="Agent request")
            return {"task_id": task_id, "state": result.state.value, "accepted": result.accepted}
        if action == "retry":
            if kind != "execution" or record.state not in TERMINAL_TASK_STATES:
                return {"error": "Only a finished execution can be retried"}
            detached = await asyncio.to_thread(
                self._sessions.create,
                scope.principal_id,
                activate=False,
            )
            retried = await self._executions.create(
                detached,
                client_request_id=f"retry:{secrets.token_urlsafe(16)}",
                goal=record.goal,
                attachments=record.attachments,
                tools_enabled=record.tools_enabled,
                priority=record.priority,
                parent_task_id=record.task_id,
                origin=record.origin,
            )
            return {"task_id": record.task_id, "execution_id": retried.task_id, "state": retried.state.value}
        return {"error": "Unknown action"}

    async def _resolve(self, principal_id: str, task_id: str) -> tuple[str, Any] | None:
        for kind, service in (
            ("execution", self._executions),
            ("scheduled", self._schedules),
            ("event", self._triggers),
        ):
            try:
                return kind, await service.get(principal_id, task_id)
            except LookupError:
                continue
        return None

    @staticmethod
    def _snapshot(kind: str, record: Any) -> dict[str, Any]:
        if kind == "execution":
            return {
                "task_id": record.task_id,
                "launch_policy": "immediate" if record.origin in {TaskOrigin.USER, TaskOrigin.AGENT} else record.origin.value,
                "state": record.state.value,
                "goal": record.goal,
                "latest_execution_id": record.task_id,
                "result": record.final_summary,
            }
        return {
            "task_id": record.schedule_id if kind == "scheduled" else record.trigger_id,
            "launch_policy": kind,
            "state": record.state.value,
            "goal": record.goal,
            "next_fire_at": getattr(record, "next_fire_at", None),
        }

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "get", "pause", "resume", "cancel", "retry"]},
                    "task_id": {"type": "string", "maxLength": 128},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        }
