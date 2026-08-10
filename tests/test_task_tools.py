from __future__ import annotations

from types import SimpleNamespace

import pytest

from pc_assistant.agent_runtime.contracts import RuntimeScope
from pc_assistant.automation import ScheduleKind, ScheduleState
from pc_assistant.tasks import (
    TaskDefinitionState,
    TaskLaunchKind,
    TaskLaunchPolicy,
    TaskLaunchReason,
    TaskState,
)
from pc_assistant.tools.create_task import CreateTaskTool
from pc_assistant.tools.schedule_task import ScheduleTaskTool
from pc_assistant.tools.task_control import TaskControlTool


class _Sessions:
    def __init__(self) -> None:
        self.calls = []

    def create(self, principal_id: str, *, activate: bool = True) -> RuntimeScope:
        self.calls.append((principal_id, activate))
        return RuntimeScope(principal_id=principal_id, session_handle="detached-a")


class _Executions:
    def __init__(self) -> None:
        self.created = []

    async def create_definition(self, scope, **kwargs):
        self.created.append((scope, kwargs))
        return (
            SimpleNamespace(
                task_id="task-a",
                title=kwargs.get("title") or "整理资料",
                goal=kwargs["goal"],
                launch_policy=kwargs.get("launch_policy", TaskLaunchPolicy()),
                state=TaskDefinitionState.ACTIVE,
                latest_execution_id="execution-a",
                execution_count=1,
            ),
            SimpleNamespace(execution_id="execution-a"),
        )

    async def bind_launch(self, principal_id, task_id, **kwargs):
        self.bound = (principal_id, task_id, kwargs)

    async def list_definitions(self, _principal_id, **_kwargs):
        return (
            SimpleNamespace(
                task_id="task-a",
                title="整理资料",
                goal="整理资料",
                launch_policy=TaskLaunchPolicy(kind=TaskLaunchKind.IMMEDIATE),
                state=TaskDefinitionState.ACTIVE,
                latest_execution_id="execution-a",
                execution_count=1,
            ),
        )


class _Schedules:
    def __init__(self) -> None:
        self.created = []

    async def create(self, scope, **kwargs):
        self.created.append((scope, kwargs))
        return SimpleNamespace(
            schedule_id="task-scheduled",
            state=ScheduleState.ACTIVE,
            next_fire_at=kwargs["spec"].run_at,
        )

    async def list(self, _principal_id, **_kwargs):
        return ()


class _Triggers:
    async def list(self, _principal_id, **_kwargs):
        return ()


@pytest.mark.asyncio
async def test_create_task_uses_detached_session_and_stable_task_definition() -> None:
    sessions = _Sessions()
    executions = _Executions()
    tool = CreateTaskTool(sessions, executions)

    result = await tool.execute_scoped(
        RuntimeScope(principal_id="personal:owner", session_handle="chat-a"),
        goal="整理资料",
    )

    assert sessions.calls == [("personal:owner", False)]
    assert executions.created[0][1]["goal"] == "整理资料"
    assert result["task_id"] == "task-a"
    assert result["execution_id"] == "execution-a"


@pytest.mark.asyncio
async def test_schedule_task_infers_one_time_policy() -> None:
    sessions = _Sessions()
    tasks = _Executions()
    schedules = _Schedules()
    tool = ScheduleTaskTool(sessions, tasks, schedules)

    result = await tool.execute_scoped(
        RuntimeScope(principal_id="personal:owner", session_handle="chat-a"),
        goal="发送日报",
        run_at=2_000_000_000.0,
    )

    spec = schedules.created[0][1]["spec"]
    assert spec.kind is ScheduleKind.ONE_TIME
    assert result == {
        "task_id": "task-a",
        "launch_policy": "scheduled",
        "state": "active",
        "next_fire_at": 2_000_000_000.0,
    }


@pytest.mark.asyncio
async def test_task_tool_lists_public_task_shape() -> None:
    tool = TaskControlTool(_Sessions(), _Executions(), _Schedules(), _Triggers())

    result = await tool.execute_scoped(
        RuntimeScope(principal_id="personal:owner", session_handle="chat-a"),
        action="list",
    )

    assert result["tasks"] == [
        {
            "task_id": "task-a",
            "title": "整理资料",
            "goal": "整理资料",
            "launch_policy": "immediate",
            "state": "active",
            "latest_execution_id": "execution-a",
            "execution_count": 1,
        }
    ]
