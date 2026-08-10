"""Compact Agent query/control surface for Tasks and their executions."""
from __future__ import annotations

from typing import Any

from pc_assistant.agent_runtime.contracts import RuntimeScope
from pc_assistant.tasks import TaskDefinitionState
from pc_assistant.tools.base import ToolBase, ToolCapability, ToolEffect, ToolPolicy, ToolRisk


class TaskControlTool(ToolBase):
    name = "task"
    description = (
        "List or inspect stable Tasks, control future launches, execute the current "
        "Task definition, or control/rerun one TaskExecution."
    )
    effect = ToolEffect.INTERNAL_WRITE
    capabilities = frozenset({ToolCapability.TASK_MANAGEMENT})
    risk = ToolRisk.LOW

    def __init__(self, sessions: Any, tasks: Any, schedules: Any = None, triggers: Any = None) -> None:
        del sessions, schedules, triggers
        self._tasks = tasks

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
        execution_id = str(kwargs.get("execution_id", "")).strip()
        if action == "list":
            tasks = await self._tasks.list_definitions(
                scope.principal_id,
                include_archived=bool(kwargs.get("include_archived", False)),
                limit=50,
            )
            return {
                "tasks": [
                    {
                        "task_id": item.task_id,
                        "title": item.title,
                        "goal": item.goal,
                        "launch_policy": item.launch_policy.kind.value,
                        "state": item.state.value,
                        "latest_execution_id": item.latest_execution_id,
                        "execution_count": item.execution_count,
                    }
                    for item in tasks
                ]
            }
        if action == "get":
            if not task_id:
                return {"error": "task_id is required"}
            try:
                task = await self._tasks.get_definition(scope.principal_id, task_id)
                executions = await self._tasks.list_executions(
                    scope.principal_id,
                    task_id,
                    limit=20,
                )
            except LookupError:
                return {"error": "Task not found"}
            return {
                "task_id": task.task_id,
                "title": task.title,
                "goal": task.goal,
                "launch_policy": task.launch_policy.model_dump(mode="json"),
                "state": task.state.value,
                "revision": task.revision,
                "executions": [self._execution_snapshot(item) for item in executions],
            }
        if action in {"pause", "resume", "archive", "restore", "execute"}:
            if not task_id:
                return {"error": "task_id is required"}
            if action == "execute":
                execution = await self._tasks.execute_definition(
                    scope.principal_id,
                    task_id,
                )
                return self._execution_snapshot(execution)
            state = {
                "pause": TaskDefinitionState.PAUSED,
                "resume": TaskDefinitionState.ACTIVE,
                "archive": TaskDefinitionState.ARCHIVED,
                "restore": TaskDefinitionState.ACTIVE,
            }[action]
            task = await self._tasks.set_definition_state(
                scope.principal_id,
                task_id,
                state,
            )
            return {"task_id": task.task_id, "state": task.state.value}
        if action in {"cancel_execution", "pause_execution", "resume_execution", "rerun"}:
            if not execution_id:
                return {"error": "execution_id is required"}
            if action == "cancel_execution":
                result = await self._tasks.cancel(
                    scope.principal_id,
                    execution_id,
                    reason="Agent request",
                )
                return {
                    "execution_id": execution_id,
                    "accepted": result.accepted,
                    "state": None if result.state is None else result.state.value,
                }
            if action == "pause_execution":
                result = await self._tasks.pause(
                    scope.principal_id,
                    execution_id,
                    reason="Agent request",
                )
                return {"execution_id": execution_id, "state": result.state.value}
            if action == "resume_execution":
                resumed = await self._tasks.resume(
                    scope.principal_id,
                    execution_id,
                    reason="Agent request",
                )
                return {"execution_id": execution_id, "state": resumed.state.value}
            execution = await self._tasks.rerun_execution(
                scope.principal_id,
                execution_id,
            )
            return self._execution_snapshot(execution)
        return {"error": "Unknown action"}

    @staticmethod
    def _execution_snapshot(record: Any) -> dict[str, Any]:
        return {
            "task_id": record.task_id,
            "execution_id": record.execution_id,
            "task_revision": record.task_revision,
            "launch_reason": record.launch_reason.value,
            "state": record.state.value,
            "result": record.final_result,
            "failure_code": record.failure_code,
        }

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "list",
                            "get",
                            "pause",
                            "resume",
                            "archive",
                            "restore",
                            "execute",
                            "cancel_execution",
                            "pause_execution",
                            "resume_execution",
                            "rerun",
                        ],
                    },
                    "task_id": {"type": "string", "maxLength": 128},
                    "execution_id": {"type": "string", "maxLength": 128},
                    "include_archived": {"type": "boolean", "default": False},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        }
