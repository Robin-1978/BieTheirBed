"""Minimal Agent-facing delegation into an isolated background Task."""
from __future__ import annotations

import asyncio
import secrets
from typing import Any, Protocol

from pc_assistant.agent_runtime.contracts import RuntimeScope
from pc_assistant.tasks import TaskOrigin, TaskRecord
from pc_assistant.tools.base import ToolBase, ToolCapability, ToolEffect, ToolRisk


class DetachedSessionPort(Protocol):
    def create(self, principal_id: str, *, activate: bool = True) -> RuntimeScope: ...


class BackgroundTaskPort(Protocol):
    async def create(
        self,
        scope: RuntimeScope,
        *,
        client_request_id: str,
        goal: str,
        origin: TaskOrigin,
    ) -> TaskRecord: ...


class CreateTaskTool(ToolBase):
    name = "create_task"
    description = (
        "Create one independent background task for a complete goal that can proceed "
        "without blocking the current conversation. Returns immediately with its task ID."
    )
    effect = ToolEffect.INTERNAL_WRITE
    capabilities = frozenset({ToolCapability.TASK_MANAGEMENT})
    risk = ToolRisk.LOW

    def __init__(self, sessions: DetachedSessionPort, tasks: BackgroundTaskPort) -> None:
        self._sessions = sessions
        self._tasks = tasks

    async def execute(self, **kwargs: Any) -> Any:
        del kwargs
        return {"error": "Task creation requires an authenticated runtime scope"}

    async def execute_scoped(self, scope: RuntimeScope, **kwargs: Any) -> Any:
        goal = str(kwargs.get("goal", "")).strip()
        detached = await asyncio.to_thread(
            self._sessions.create,
            scope.principal_id,
            activate=False,
        )
        task = await self._tasks.create(
            detached,
            client_request_id=f"agent:{secrets.token_urlsafe(18)}",
            goal=goal,
            origin=TaskOrigin.AGENT,
        )
        return {
            "task_id": task.task_id,
            "state": task.state.value,
            "message": "任务已在后台开始",
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
                        "description": "Self-contained goal for the independent task.",
                    }
                },
                "required": ["goal"],
                "additionalProperties": False,
            },
        }
