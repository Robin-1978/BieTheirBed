"""Compact Agent query/control surface for Tasks and their executions."""
from __future__ import annotations

import asyncio
from typing import Any, ClassVar

from pc_assistant.agent_runtime.contracts import RuntimeScope
from pc_assistant.service.product_task_lifecycle import ProductTaskLifecycle
from pc_assistant.tasks import TaskDefinitionState, TaskTransitionError
from pc_assistant.tools.base import (
    ToolBase,
    ToolCapability,
    ToolEffect,
    ToolPolicy,
    ToolRisk,
)
from pc_assistant.tools.task_launch import (
    LaunchPolicyError,
    launch_schema,
    public_launch,
    resolve_task_launch,
    timestamp_text,
)


class TaskControlTool(ToolBase):
    name = "task"
    description = (
        "List, inspect, update, delete, pause, resume, archive, restore, or execute "
        "stable Tasks; or control, rerun, and delete TaskExecutions."
    )
    details = (
        "Use task_id for Task actions and execution_id for TaskExecution actions. "
        "Updating launch replaces only future launches. Deleting a Task also deletes "
        "its launch provider and execution history, and therefore requires confirmation."
    )
    examples: ClassVar[list[dict[str, Any]]] = [
        {"action": "list"},
        {"action": "get", "task_id": "task-id"},
        {
            "action": "update",
            "task_id": "task-id",
            "launch": {
                "kind": "cron",
                "cron": "0 19 * * *",
                "timezone": "Asia/Shanghai",
            },
        },
        {"action": "rerun", "execution_id": "execution-id"},
    ]
    effect = ToolEffect.INTERNAL_WRITE
    capabilities = frozenset({ToolCapability.TASK_MANAGEMENT})
    risk = ToolRisk.LOW

    def __init__(self, sessions: Any, tasks: Any, schedules: Any, triggers: Any) -> None:
        del sessions
        self._tasks = tasks
        self._lifecycle = ProductTaskLifecycle(tasks, schedules, triggers)

    def policy_for(self, arguments: dict[str, Any]) -> ToolPolicy:
        action = arguments.get("action")
        if action in {"list", "get"}:
            return ToolPolicy(
                effect=ToolEffect.READ_ONLY,
                capabilities=self.capabilities,
                risk=ToolRisk.LOW,
            )
        if action in {"delete", "delete_execution"}:
            return ToolPolicy(
                effect=ToolEffect.INTERNAL_WRITE,
                capabilities=self.capabilities,
                risk=ToolRisk.HIGH,
            )
        return self.policy

    async def execute(self, **kwargs: Any) -> Any:
        del kwargs
        return {"error": "Task access requires an authenticated scope"}

    @staticmethod
    def _unexpected_arguments(
        arguments: dict[str, Any],
        *,
        allowed: frozenset[str],
    ) -> dict[str, str] | None:
        unexpected = sorted(set(arguments) - allowed)
        if not unexpected:
            return None
        return {
            "error": (
                f"action does not accept these fields: {', '.join(unexpected)}"
            )
        }

    async def _definition_snapshot(
        self,
        principal_id: str,
        task: Any,
        *,
        include_executions: bool = False,
    ) -> dict[str, Any]:
        provider = await self._lifecycle.launch_status(principal_id, task.task_id)
        snapshot = {
            "task_id": task.task_id,
            "title": task.title,
            "goal": task.goal,
            "launch": public_launch(task.launch_policy),
            "launch_state": provider.state,
            "next_fire_at": timestamp_text(provider.next_fire_at),
            "state": task.state.value,
            "revision": task.revision,
            "latest_execution_id": task.latest_execution_id,
            "execution_count": task.execution_count,
        }
        if include_executions:
            executions = await self._tasks.list_executions(
                principal_id,
                task.task_id,
                limit=20,
            )
            snapshot["executions"] = [
                self._execution_snapshot(item) for item in executions
            ]
        return snapshot

    async def execute_scoped(self, scope: RuntimeScope, **kwargs: Any) -> Any:
        action = str(kwargs.get("action", "")).strip()
        task_id = str(kwargs.get("task_id", "")).strip()
        execution_id = str(kwargs.get("execution_id", "")).strip()
        if action == "list":
            invalid = self._unexpected_arguments(
                kwargs,
                allowed=frozenset({"action", "include_archived"}),
            )
            if invalid is not None:
                return invalid
            tasks = await self._tasks.list_definitions(
                scope.principal_id,
                include_archived=bool(kwargs.get("include_archived", False)),
                limit=50,
            )
            return {
                "tasks": list(
                    await asyncio.gather(
                        *(
                            self._definition_snapshot(scope.principal_id, item)
                            for item in tasks
                        )
                    )
                )
            }
        if action == "get":
            invalid = self._unexpected_arguments(
                kwargs,
                allowed=frozenset({"action", "task_id"}),
            )
            if invalid is not None:
                return invalid
            if not task_id:
                return {"error": "task_id is required for get"}
            try:
                task = await self._tasks.get_definition(scope.principal_id, task_id)
                return await self._definition_snapshot(
                    scope.principal_id,
                    task,
                    include_executions=True,
                )
            except LookupError:
                return {"error": "Task not found"}
            except (TaskTransitionError, ValueError) as exc:
                return {"error": str(exc)}
        if action == "update":
            invalid = self._unexpected_arguments(
                kwargs,
                allowed=frozenset(
                    {
                        "action",
                        "task_id",
                        "title",
                        "goal",
                        "launch",
                        "expected_revision",
                    }
                ),
            )
            if invalid is not None:
                return invalid
            if not task_id:
                return {"error": "task_id is required for update"}
            changes: dict[str, Any] = {}
            if "title" in kwargs:
                title = str(kwargs.get("title", "")).strip()
                if not title:
                    return {"error": "title must not be empty"}
                changes["title"] = title
            if "goal" in kwargs:
                goal = str(kwargs.get("goal", "")).strip()
                if not goal:
                    return {"error": "goal must not be empty"}
                changes["goal"] = goal
            if "launch" in kwargs:
                try:
                    changes["launch_policy"] = (
                        await resolve_task_launch(kwargs.get("launch"))
                    ).policy
                except LaunchPolicyError as exc:
                    return {"error": str(exc)}
            if "expected_revision" in kwargs:
                changes["expected_revision"] = int(kwargs["expected_revision"])
            if not changes or set(changes) == {"expected_revision"}:
                return {
                    "error": "update requires title, goal, or launch"
                }
            try:
                task = await self._lifecycle.update_definition(
                    scope.principal_id,
                    task_id,
                    **changes,
                )
                return await self._definition_snapshot(scope.principal_id, task)
            except LookupError:
                return {"error": "Task not found"}
            except (TaskTransitionError, ValueError) as exc:
                return {"error": str(exc)}
        if action in {"pause", "resume", "archive", "restore", "execute"}:
            invalid = self._unexpected_arguments(
                kwargs,
                allowed=frozenset({"action", "task_id"}),
            )
            if invalid is not None:
                return invalid
            if not task_id:
                return {"error": f"task_id is required for {action}"}
            try:
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
                task = await self._lifecycle.set_definition_state(
                    scope.principal_id,
                    task_id,
                    state,
                )
                return await self._definition_snapshot(scope.principal_id, task)
            except LookupError:
                return {"error": "Task not found"}
            except (TaskTransitionError, ValueError) as exc:
                return {"error": str(exc)}
        if action == "delete":
            invalid = self._unexpected_arguments(
                kwargs,
                allowed=frozenset({"action", "task_id"}),
            )
            if invalid is not None:
                return invalid
            if not task_id:
                return {"error": "task_id is required for delete"}
            try:
                await self._lifecycle.delete_definition(scope.principal_id, task_id)
            except LookupError:
                return {"error": "Task not found"}
            except (TaskTransitionError, ValueError) as exc:
                return {"error": str(exc)}
            return {"task_id": task_id, "deleted": True}
        if action in {
            "cancel_execution",
            "pause_execution",
            "resume_execution",
            "rerun",
            "delete_execution",
        }:
            invalid = self._unexpected_arguments(
                kwargs,
                allowed=frozenset({"action", "execution_id"}),
            )
            if invalid is not None:
                return invalid
            if not execution_id:
                return {"error": f"execution_id is required for {action}"}
            try:
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
                if action == "delete_execution":
                    await self._tasks.delete_execution(
                        scope.principal_id,
                        execution_id,
                    )
                    return {"execution_id": execution_id, "deleted": True}
                execution = await self._tasks.rerun_execution(
                    scope.principal_id,
                    execution_id,
                )
                return self._execution_snapshot(execution)
            except LookupError:
                return {"error": "TaskExecution not found"}
            except (TaskTransitionError, ValueError) as exc:
                return {"error": str(exc)}
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
                        "description": "The Task or TaskExecution operation to perform.",
                        "enum": [
                            "list",
                            "get",
                            "update",
                            "pause",
                            "resume",
                            "archive",
                            "restore",
                            "delete",
                            "execute",
                            "cancel_execution",
                            "pause_execution",
                            "resume_execution",
                            "rerun",
                            "delete_execution",
                        ],
                    },
                    "task_id": {
                        "type": "string",
                        "maxLength": 128,
                        "description": "Required for actions that operate on a Task.",
                    },
                    "execution_id": {
                        "type": "string",
                        "maxLength": 128,
                        "description": (
                            "Required for actions that operate on a TaskExecution."
                        ),
                    },
                    "include_archived": {
                        "type": "boolean",
                        "default": False,
                        "description": "Include archived Tasks when action is list.",
                    },
                    "title": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 200,
                        "description": "New Task title for update.",
                    },
                    "goal": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 200000,
                        "description": "New self-contained Task goal for update.",
                    },
                    "launch": launch_schema(),
                    "expected_revision": {
                        "type": "integer",
                        "minimum": 1,
                        "description": (
                            "Optional optimistic-lock revision for update."
                        ),
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        }
