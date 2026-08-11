from __future__ import annotations

import json
import re
from datetime import datetime
from types import SimpleNamespace

import pytest

from pc_assistant.agent_runtime.contracts import RuntimeScope
from pc_assistant.automation import ScheduleKind, ScheduleState
from pc_assistant.tasks import (
    TaskDefinitionState,
    TaskLaunchKind,
    TaskLaunchPolicy,
)
from pc_assistant.tools.create_task import CreateTaskTool
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
        self.deleted = []
        self.fail_bind = False

    async def create_definition(self, scope, **kwargs):
        self.created.append((scope, kwargs))
        policy = kwargs.get("launch_policy", TaskLaunchPolicy())
        execution = (
            SimpleNamespace(execution_id="execution-a")
            if policy.kind is TaskLaunchKind.IMMEDIATE
            else None
        )
        return (
            SimpleNamespace(
                task_id="task-a",
                title=kwargs.get("title") or "整理资料",
                goal=kwargs["goal"],
                launch_policy=policy,
                state=TaskDefinitionState.ACTIVE,
                latest_execution_id=("execution-a" if execution else ""),
                execution_count=(1 if execution else 0),
            ),
            execution,
        )

    async def bind_launch(self, principal_id, task_id, **kwargs):
        if self.fail_bind:
            raise RuntimeError("binding failed")
        self.bound = (principal_id, task_id, kwargs)

    async def delete_definition(self, principal_id, task_id):
        self.deleted.append((principal_id, task_id))

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
        self.deleted = []

    async def create(self, scope, **kwargs):
        self.created.append((scope, kwargs))
        return SimpleNamespace(
            schedule_id="task-scheduled",
            state=ScheduleState.ACTIVE,
            next_fire_at=kwargs["spec"].run_at or 2_000_000_000.0,
        )

    async def delete(self, principal_id, schedule_id):
        self.deleted.append((principal_id, schedule_id))

    async def list(self, _principal_id, **_kwargs):
        return ()


class _Triggers:
    async def list(self, _principal_id, **_kwargs):
        return ()


@pytest.mark.asyncio
async def test_create_task_uses_detached_session_and_stable_task_definition() -> None:
    sessions = _Sessions()
    executions = _Executions()
    tool = CreateTaskTool(sessions, executions, _Schedules())

    result = await tool.execute_scoped(
        RuntimeScope(principal_id="personal:owner", session_handle="chat-a"),
        title="Organize files",
        goal="整理资料",
        launch={"kind": "immediate"},
    )

    assert sessions.calls == [("personal:owner", False)]
    assert executions.created[0][1]["goal"] == "整理资料"
    assert result["task_id"] == "task-a"
    assert result["execution_id"] == "execution-a"
    assert result["message"] == "Task created and execution started."


@pytest.mark.asyncio
async def test_create_task_builds_one_time_launch_policy() -> None:
    sessions = _Sessions()
    tasks = _Executions()
    schedules = _Schedules()
    tool = CreateTaskTool(sessions, tasks, schedules)

    result = await tool.execute_scoped(
        RuntimeScope(principal_id="personal:owner", session_handle="chat-a"),
        title="Daily report reminder",
        goal="发送日报",
        launch={"kind": "one_time", "at": "2030-01-02T18:30:00+08:00"},
    )

    spec = schedules.created[0][1]["spec"]
    assert spec.kind is ScheduleKind.ONE_TIME
    assert spec.run_at == datetime.fromisoformat("2030-01-02T18:30:00+08:00").timestamp()
    assert tasks.created[0][1]["launch_policy"].schedule_type == "one_time"
    assert result["task_id"] == "task-a"
    assert result["execution_id"] == ""
    assert result["launch_kind"] == "one_time"


@pytest.mark.asyncio
async def test_create_task_rejects_six_field_cron_with_actionable_error() -> None:
    sessions = _Sessions()
    tasks = _Executions()
    schedules = _Schedules()
    tool = CreateTaskTool(sessions, tasks, schedules)

    result = await tool.execute_scoped(
        RuntimeScope(principal_id="personal:owner", session_handle="chat-a"),
        title="Walking reminder",
        goal="查询天气后提醒散步",
        launch={
            "kind": "cron",
            "cron": "0 30 18 * * *",
            "timezone": "Asia/Shanghai",
        },
    )

    assert result == {
        "error": (
            "launch.cron must contain exactly five fields "
            "(minute hour day month weekday), with no seconds. "
            "For every day at 18:30, use '30 18 * * *'."
        )
    }
    assert sessions.calls == []
    assert schedules.created == []
    assert tasks.created == []


@pytest.mark.asyncio
async def test_create_task_allows_interval_with_first_run_anchor() -> None:
    sessions = _Sessions()
    tasks = _Executions()
    schedules = _Schedules()
    tool = CreateTaskTool(sessions, tasks, schedules)

    result = await tool.execute_scoped(
        RuntimeScope(principal_id="personal:owner", session_handle="chat-a"),
        title="Organize files regularly",
        goal="定期整理资料",
        launch={
            "kind": "interval",
            "interval_seconds": 3600,
            "at": "2030-01-02T18:30:00+08:00",
        },
    )

    spec = schedules.created[0][1]["spec"]
    assert spec.kind is ScheduleKind.INTERVAL
    assert spec.run_at == datetime.fromisoformat("2030-01-02T18:30:00+08:00").timestamp()
    assert spec.interval_seconds == 3600
    assert result["task_id"] == "task-a"


@pytest.mark.asyncio
async def test_create_task_rolls_back_scheduled_task_when_binding_fails() -> None:
    tasks = _Executions()
    tasks.fail_bind = True
    schedules = _Schedules()
    tool = CreateTaskTool(_Sessions(), tasks, schedules)

    result = await tool.execute_scoped(
        RuntimeScope(principal_id="personal:owner", session_handle="chat-a"),
        title="Evening job",
        goal="Run every evening",
        launch={
            "kind": "cron",
            "cron": "30 18 * * *",
            "timezone": "Asia/Shanghai",
        },
    )

    assert result == {
        "error": "Task creation failed before the launch policy was committed"
    }
    assert schedules.deleted == [("personal:owner", "task-scheduled")]
    assert tasks.deleted == [("personal:owner", "task-a")]


def test_create_task_help_documents_launch_policy_in_english() -> None:
    tool = CreateTaskTool(_Sessions(), _Executions(), _Schedules())

    definition = tool.definition()

    assert "explicit launch policy" in definition["description"]
    launch = definition["inputSchema"]["properties"]["launch"]
    assert launch["properties"]["kind"]["enum"] == [
        "immediate",
        "one_time",
        "interval",
        "cron",
    ]
    cron_description = launch["properties"]["cron"]["description"]
    assert "Do not include seconds" in cron_description
    rendered = json.dumps(
        {"definition": definition, "details": tool.details, "examples": tool.examples},
        ensure_ascii=False,
    )
    assert re.search(
        r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]",
        rendered,
    ) is None


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
