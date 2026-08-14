from __future__ import annotations

import json
import re
from datetime import datetime
from types import SimpleNamespace

import pytest

from knoa_platform.agent_runtime.contracts import RuntimeScope
from knoa_platform.automation import ScheduleKind, ScheduleState
from knoa_platform.tasks import (
    TaskDefinitionState,
    TaskLaunchKind,
    TaskLaunchPolicy,
    TaskState,
)
from knoa_platform.tools.base import ToolRisk
from knoa_platform.tools.create_task import CreateTaskTool
from knoa_platform.tools.task_control import TaskControlTool


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
        self.deleted_executions = []
        self.fail_bind = False
        self.bound = None
        self.current = None
        self.executions = ()

    @staticmethod
    def _record(**changes):
        values = {
            "task_id": "task-a",
            "session_handle": "detached-a",
            "title": "整理资料",
            "goal": "整理资料",
            "launch_policy": TaskLaunchPolicy(kind=TaskLaunchKind.IMMEDIATE),
            "state": TaskDefinitionState.ACTIVE,
            "revision": 1,
            "latest_execution_id": "execution-a",
            "execution_count": 1,
            "attachments": (),
            "tools_enabled": True,
            "priority": 0,
            "notification_policy": {},
        }
        values.update(changes)
        return SimpleNamespace(**values)

    async def create_definition(self, scope, **kwargs):
        self.created.append((scope, kwargs))
        policy = kwargs.get("launch_policy", TaskLaunchPolicy())
        execution = (
            SimpleNamespace(execution_id="execution-a")
            if policy.kind is TaskLaunchKind.IMMEDIATE
            else None
        )
        self.current = self._record(
            title=kwargs.get("title") or "整理资料",
            goal=kwargs["goal"],
            launch_policy=policy,
            latest_execution_id=("execution-a" if execution else ""),
            execution_count=(1 if execution else 0),
        )
        return self.current, execution

    async def bind_launch(self, principal_id, task_id, **kwargs):
        if self.fail_bind:
            raise RuntimeError("binding failed")
        self.bound = (
            kwargs["provider_kind"],
            kwargs["provider_id"],
        )

    async def launch_binding(self, _principal_id, _task_id):
        return self.bound

    async def unbind_launch(self, _principal_id, _task_id):
        binding, self.bound = self.bound, None
        return binding

    async def delete_definition(self, principal_id, task_id):
        self.deleted.append((principal_id, task_id))

    async def delete_execution(self, principal_id, execution_id):
        self.deleted_executions.append((principal_id, execution_id))

    async def get_definition(self, _principal_id, _task_id):
        return self.current or self._record()

    async def update_definition(self, _principal_id, _task_id, **changes):
        current = self.current or self._record()
        changes.pop("expected_revision", None)
        self.current = self._record(
            **{
                **vars(current),
                **changes,
                "revision": current.revision + 1,
            }
        )
        return self.current

    async def set_definition_state(self, _principal_id, _task_id, state):
        current = self.current or self._record()
        self.current = self._record(
            **{
                **vars(current),
                "state": state,
                "revision": current.revision + 1,
            }
        )
        return self.current

    async def list_executions(self, _principal_id, _task_id, **_kwargs):
        return self.executions

    async def list_definitions(self, _principal_id, **_kwargs):
        return (self.current or self._record(),)


class _Schedules:
    def __init__(self) -> None:
        self.created = []
        self.deleted = []
        self.records = {}
        self.paused = []
        self.resumed = []
        self.fail_next_create = False

    async def create(self, scope, **kwargs):
        if self.fail_next_create:
            self.fail_next_create = False
            raise RuntimeError("schedule creation failed")
        self.created.append((scope, kwargs))
        schedule_id = (
            "task-scheduled"
            if len(self.created) == 1
            else f"task-scheduled-{len(self.created)}"
        )
        record = SimpleNamespace(
            schedule_id=schedule_id,
            state=ScheduleState.ACTIVE,
            next_fire_at=kwargs["spec"].run_at or 2_000_000_000.0,
        )
        self.records[schedule_id] = record
        return record

    async def get(self, _principal_id, schedule_id):
        return self.records[schedule_id]

    async def pause(self, principal_id, schedule_id):
        self.paused.append((principal_id, schedule_id))
        current = self.records[schedule_id]
        record = SimpleNamespace(
            **{**vars(current), "state": ScheduleState.PAUSED}
        )
        self.records[schedule_id] = record
        return record

    async def resume(self, principal_id, schedule_id):
        self.resumed.append((principal_id, schedule_id))
        current = self.records[schedule_id]
        record = SimpleNamespace(
            **{**vars(current), "state": ScheduleState.ACTIVE}
        )
        self.records[schedule_id] = record
        return record

    async def delete(self, principal_id, schedule_id):
        self.deleted.append((principal_id, schedule_id))
        self.records.pop(schedule_id, None)

    async def list(self, _principal_id, **_kwargs):
        return ()


class _Triggers:
    async def create(self, _scope, **_kwargs):
        return SimpleNamespace(trigger_id="trigger-a", state=SimpleNamespace(value="active"))

    async def get(self, _principal_id, _trigger_id):
        return SimpleNamespace(state=SimpleNamespace(value="active"))

    async def set_paused(self, _principal_id, _trigger_id, *, paused):
        return SimpleNamespace(
            state=SimpleNamespace(value="paused" if paused else "active")
        )

    async def delete(self, _principal_id, _trigger_id):
        return None

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
    assert result["launch"] == {
        "kind": "one_time",
        "at": "2030-01-02T10:30:00Z",
    }
    assert result["next_fire_at"] == "2030-01-02T10:30:00Z"


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
async def test_create_task_builds_mcp_event_launch_policy() -> None:
    sessions = _Sessions()
    tasks = _Executions()
    triggers = _Triggers()
    tool = CreateTaskTool(sessions, tasks, _Schedules(), triggers)

    result = await tool.execute_scoped(
        RuntimeScope(principal_id="personal:owner", session_handle="chat-a"),
        title="Analyze assigned Jira issues",
        goal="Analyze each newly assigned Jira issue.",
        launch={
            "kind": "event",
            "event_source": "mcp:jira",
            "source_config": {
                "resource_uri_prefix": "jira://assigned-to-me/events"
            },
        },
    )

    assert tasks.bound == ("event", "trigger-a")
    assert result["execution_id"] == ""
    assert result["launch"] == {
        "kind": "event",
        "event_source": "mcp:jira",
        "source_config": {
            "resource_uri_prefix": "jira://assigned-to-me/events",
            "include_root": True,
            "include_descendants": False,
        },
    }


def test_mcp_event_requires_an_explicit_resource_match() -> None:
    with pytest.raises(ValueError, match="Resource URI"):
        TaskLaunchPolicy(
            kind=TaskLaunchKind.EVENT,
            event_source="mcp:jira",
        )


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
        "event",
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
            "launch": {"kind": "immediate"},
            "launch_state": None,
            "next_fire_at": None,
            "state": "active",
            "revision": 1,
            "latest_execution_id": "execution-a",
            "execution_count": 1,
        }
    ]


@pytest.mark.asyncio
async def test_task_tool_pauses_and_resumes_bound_schedule() -> None:
    sessions = _Sessions()
    tasks = _Executions()
    schedules = _Schedules()
    triggers = _Triggers()
    create = CreateTaskTool(sessions, tasks, schedules)
    control = TaskControlTool(sessions, tasks, schedules, triggers)
    scope = RuntimeScope(
        principal_id="personal:owner",
        session_handle="chat-a",
    )
    await create.execute_scoped(
        scope,
        title="Evening job",
        goal="Run every evening",
        launch={
            "kind": "cron",
            "cron": "30 18 * * *",
            "timezone": "Asia/Shanghai",
        },
    )

    paused = await control.execute_scoped(scope, action="pause", task_id="task-a")
    resumed = await control.execute_scoped(scope, action="resume", task_id="task-a")

    assert schedules.paused == [("personal:owner", "task-scheduled")]
    assert schedules.resumed == [("personal:owner", "task-scheduled")]
    assert paused["state"] == "paused"
    assert paused["launch_state"] == "paused"
    assert resumed["state"] == "active"
    assert resumed["launch_state"] == "active"


@pytest.mark.asyncio
async def test_task_tool_updates_launch_and_replaces_bound_schedule() -> None:
    sessions = _Sessions()
    tasks = _Executions()
    schedules = _Schedules()
    control = TaskControlTool(sessions, tasks, schedules, _Triggers())
    scope = RuntimeScope(
        principal_id="personal:owner",
        session_handle="chat-a",
    )
    await CreateTaskTool(sessions, tasks, schedules).execute_scoped(
        scope,
        title="Evening job",
        goal="Run every evening",
        launch={
            "kind": "cron",
            "cron": "30 18 * * *",
            "timezone": "Asia/Shanghai",
        },
    )

    result = await control.execute_scoped(
        scope,
        action="update",
        task_id="task-a",
        title="Later evening job",
        launch={
            "kind": "cron",
            "cron": "0 19 * * *",
            "timezone": "Asia/Shanghai",
        },
    )

    assert schedules.deleted == [("personal:owner", "task-scheduled")]
    assert tasks.bound == ("schedule", "task-scheduled-2")
    assert result["title"] == "Later evening job"
    assert result["launch"] == {
        "kind": "cron",
        "cron": "0 19 * * *",
        "timezone": "Asia/Shanghai",
    }
    assert result["launch_state"] == "active"


@pytest.mark.asyncio
async def test_task_title_update_keeps_existing_schedule() -> None:
    sessions = _Sessions()
    tasks = _Executions()
    schedules = _Schedules()
    control = TaskControlTool(sessions, tasks, schedules, _Triggers())
    scope = RuntimeScope(
        principal_id="personal:owner",
        session_handle="chat-a",
    )
    await CreateTaskTool(sessions, tasks, schedules).execute_scoped(
        scope,
        title="Evening job",
        goal="Run every evening",
        launch={
            "kind": "cron",
            "cron": "30 18 * * *",
            "timezone": "Asia/Shanghai",
        },
    )

    result = await control.execute_scoped(
        scope,
        action="update",
        task_id="task-a",
        title="Renamed evening job",
    )

    assert result["title"] == "Renamed evening job"
    assert tasks.bound == ("schedule", "task-scheduled")
    assert len(schedules.created) == 1
    assert schedules.deleted == []


@pytest.mark.asyncio
async def test_task_update_restores_previous_definition_when_provider_fails() -> None:
    sessions = _Sessions()
    tasks = _Executions()
    schedules = _Schedules()
    control = TaskControlTool(sessions, tasks, schedules, _Triggers())
    scope = RuntimeScope(
        principal_id="personal:owner",
        session_handle="chat-a",
    )
    await CreateTaskTool(sessions, tasks, schedules).execute_scoped(
        scope,
        title="Evening job",
        goal="Run every evening",
        launch={
            "kind": "cron",
            "cron": "30 18 * * *",
            "timezone": "Asia/Shanghai",
        },
    )
    schedules.fail_next_create = True

    with pytest.raises(RuntimeError, match="schedule creation failed"):
        await control.execute_scoped(
            scope,
            action="update",
            task_id="task-a",
            title="Broken replacement",
            launch={
                "kind": "cron",
                "cron": "0 19 * * *",
                "timezone": "Asia/Shanghai",
            },
        )

    assert tasks.current.title == "Evening job"
    assert tasks.current.launch_policy.cron == "30 18 * * *"
    assert tasks.bound == ("schedule", "task-scheduled-2")
    assert schedules.deleted == [("personal:owner", "task-scheduled")]


@pytest.mark.asyncio
async def test_task_tool_deletes_task_provider_and_terminal_execution() -> None:
    sessions = _Sessions()
    tasks = _Executions()
    schedules = _Schedules()
    control = TaskControlTool(sessions, tasks, schedules, _Triggers())
    scope = RuntimeScope(
        principal_id="personal:owner",
        session_handle="chat-a",
    )
    await CreateTaskTool(sessions, tasks, schedules).execute_scoped(
        scope,
        title="One-time job",
        goal="Run once",
        launch={"kind": "one_time", "at": "2030-01-02T18:30:00+08:00"},
    )

    deleted_task = await control.execute_scoped(
        scope,
        action="delete",
        task_id="task-a",
    )
    deleted_execution = await control.execute_scoped(
        scope,
        action="delete_execution",
        execution_id="execution-a",
    )

    assert deleted_task == {"task_id": "task-a", "deleted": True}
    assert tasks.deleted == [("personal:owner", "task-a")]
    assert schedules.deleted == [("personal:owner", "task-scheduled")]
    assert deleted_execution == {"execution_id": "execution-a", "deleted": True}
    assert tasks.deleted_executions == [("personal:owner", "execution-a")]


@pytest.mark.asyncio
async def test_task_delete_keeps_provider_when_an_execution_is_active() -> None:
    sessions = _Sessions()
    tasks = _Executions()
    schedules = _Schedules()
    control = TaskControlTool(sessions, tasks, schedules, _Triggers())
    scope = RuntimeScope(
        principal_id="personal:owner",
        session_handle="chat-a",
    )
    await CreateTaskTool(sessions, tasks, schedules).execute_scoped(
        scope,
        title="One-time job",
        goal="Run once",
        launch={"kind": "one_time", "at": "2030-01-02T18:30:00+08:00"},
    )
    tasks.executions = (SimpleNamespace(state=TaskState.RUNNING),)

    result = await control.execute_scoped(
        scope,
        action="delete",
        task_id="task-a",
    )

    assert result == {
        "error": "Task has active executions; stop them before deleting"
    }
    assert tasks.deleted == []
    assert schedules.deleted == []
    assert tasks.bound == ("schedule", "task-scheduled")


def test_task_tool_delete_actions_require_confirmation_and_schema_is_english() -> None:
    tool = TaskControlTool(_Sessions(), _Executions(), _Schedules(), _Triggers())

    assert tool.policy_for({"action": "delete"}).risk is ToolRisk.HIGH
    assert tool.policy_for({"action": "delete"}).requires_confirmation
    assert tool.policy_for({"action": "delete_execution"}).requires_confirmation
    definition = tool.definition()
    actions = definition["inputSchema"]["properties"]["action"]["enum"]
    assert {"update", "delete", "delete_execution"} <= set(actions)
    rendered = json.dumps(
        {"definition": definition, "details": tool.details, "examples": tool.examples},
        ensure_ascii=False,
    )
    assert re.search(
        r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]",
        rendered,
    ) is None
