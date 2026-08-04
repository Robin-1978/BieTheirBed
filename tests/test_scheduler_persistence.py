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
