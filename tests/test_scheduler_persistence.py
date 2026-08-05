from __future__ import annotations

import pytest

from pc_assistant.context.memory_db import SQLiteMemoryRepository
from pc_assistant.tools.scheduler import SchedulerTool


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
        message="hello",
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
        message="hello",
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
        message="hello",
        schedule="in 60s",
    )
    if scheduler._tasks[created["task_id"]]._task:
        scheduler._tasks[created["task_id"]]._task.cancel()

    restored = SchedulerTool(database)
    await restored.execute(action="start")
    assert restored._tasks[created["task_id"]]._task is not None
    await restored.execute(action="stop")
