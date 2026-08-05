from __future__ import annotations

import pytest

from pc_assistant.context.memory_db import SQLiteMemoryRepository
from pc_assistant.tools.scheduler import (
    SchedulerTool,
    bind_scheduler_session,
    reset_scheduler_session,
)


@pytest.mark.asyncio
async def test_scheduler_persists_in_shared_sqlite_database(tmp_path):
    database = tmp_path / "assistant.db"
    SQLiteMemoryRepository(database)
    scheduler = SchedulerTool(database)

    created = await scheduler.execute(
        action="create",
        task_name="daily-check",
        command="echo ok",
        schedule="0 9 * * *",
    )
    assert created["task_id"]

    restored = SchedulerTool(database)
    listed = await restored.execute(action="list")
    assert listed["count"] == 1
    assert listed["tasks"][0]["name"] == "daily-check"


@pytest.mark.asyncio
async def test_scheduler_delete_is_durable(tmp_path):
    database = tmp_path / "assistant.db"
    scheduler = SchedulerTool(database)
    created = await scheduler.execute(
        action="create",
        task_name="temporary",
        command="say hello",
        schedule="0 9 * * *",
    )

    await scheduler.execute(action="delete", task_id=created["task_id"])

    restored = SchedulerTool(database)
    assert (await restored.execute(action="list"))["count"] == 0


@pytest.mark.asyncio
async def test_disabled_one_shot_does_not_start_background_task(tmp_path):
    scheduler = SchedulerTool(tmp_path / "assistant.db")
    created = await scheduler.execute(
        action="create",
        task_name="disabled",
        command="say hello",
        schedule="in 60s",
        enabled=False,
    )
    task = scheduler._tasks[created["task_id"]]
    assert task._task is None


@pytest.mark.asyncio
async def test_persisted_one_shot_is_reconciled_on_scheduler_start(tmp_path):
    database = tmp_path / "assistant.db"
    scheduler = SchedulerTool(database)
    created = await scheduler.execute(
        action="create",
        task_name="restartable",
        command="say hello",
        schedule="in 60s",
    )
    if scheduler._tasks[created["task_id"]]._task:
        scheduler._tasks[created["task_id"]]._task.cancel()

    restored = SchedulerTool(database)
    await restored.execute(action="start")
    assert restored._tasks[created["task_id"]]._task is not None
    await restored.execute(action="stop")


@pytest.mark.asyncio
async def test_scheduled_agent_run_keeps_origin_session_and_delivers_result(tmp_path):
    class FakeAgent:
        async def run(self, prompt, *, session_id=""):
            assert prompt == "检查状态并总结"
            assert session_id == "feishu:ou-owner"
            from pc_assistant.agent import AgentEvent

            yield AgentEvent(type="final_answer", content="状态正常")

    delivered = []
    scheduler = SchedulerTool(tmp_path / "assistant.db")
    scheduler.set_agent(FakeAgent())
    scheduler.set_result_callback(lambda task, result: delivered.append((task.session_id, result)))
    token = bind_scheduler_session("feishu:ou-owner")
    try:
        created = await scheduler.execute(
            action="create",
            task_name="状态巡检",
            command="检查状态并总结",
            schedule="in 1s",
        )
    finally:
        reset_scheduler_session(token)

    task = scheduler._tasks[created["task_id"]]
    assert task.session_id == "feishu:ou-owner"
    await scheduler._execute_task(task)
    assert delivered[0][0] == "feishu:ou-owner"
    assert delivered[0][1]["result"] == "状态正常"
