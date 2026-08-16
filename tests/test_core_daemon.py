from __future__ import annotations

import stat
from types import SimpleNamespace

import pytest

from knoa_platform.config import AppConfig
from knoa_platform.service.core_daemon import CoreDaemon


class _Host:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


class _Extensions(_Host):
    pass


class _MCPResourceTasks(_Host):
    pass


class _TaskService(_Host):
    async def compact_expired_traces(self) -> int:
        return 0


class _ConversationService(_Host):
    async def compact_expired_details(self) -> int:
        return 0


class _ScheduleDispatcher(_Host):
    pass


class _TriggerDispatcher(_Host):
    pass


class _Interactions(_Host):
    async def close(self) -> None:
        await self.stop()


class _Delegations:
    def __init__(self) -> None:
        self.recovered = False

    async def recover_staged(self) -> None:
        self.recovered = True


@pytest.mark.asyncio
async def test_core_daemon_owns_host_pid_and_cleanup_lifecycle(
    tmp_path,
    monkeypatch,
) -> None:
    host = _Host()
    extensions = _Extensions()
    mcp_resource_tasks = _MCPResourceTasks()
    task_service = _TaskService()
    conversation_service = _ConversationService()
    schedule_dispatcher = _ScheduleDispatcher()
    trigger_dispatcher = _TriggerDispatcher()
    interactions = _Interactions()
    capability_mcp_host = _Host()
    delegations = _Delegations()
    pid = tmp_path / "service.pid"
    composition = SimpleNamespace(
        host=host,
        extensions=extensions,
        mcp_resource_tasks=mcp_resource_tasks,
        task_service=task_service,
        conversation_service=conversation_service,
        schedule_dispatcher=schedule_dispatcher,
        trigger_dispatcher=trigger_dispatcher,
        interactions=interactions,
        capability_mcp_host=capability_mcp_host,
        delegations=delegations,
        paths=SimpleNamespace(pid=pid),
        artifacts=SimpleNamespace(cleanup_expired=lambda: None),
    )
    monkeypatch.setattr(
        "knoa_platform.service.core_daemon.build_core_runtime",
        lambda config: composition,
    )
    daemon = CoreDaemon(
        AppConfig(fallback_enabled=False),
        log_path=tmp_path / "service.log",
    )

    await daemon.start()

    assert host.started
    assert extensions.started
    assert mcp_resource_tasks.started
    assert task_service.started
    assert conversation_service.started
    assert schedule_dispatcher.started
    assert trigger_dispatcher.started
    assert interactions.started
    assert capability_mcp_host.started
    assert delegations.recovered
    assert pid.exists()
    assert str(tmp_path / "service.log") in pid.read_text(encoding="utf-8")
    assert stat.S_IMODE(pid.stat().st_mode) == 0o600

    await daemon.stop()

    assert host.stopped
    assert extensions.stopped
    assert mcp_resource_tasks.stopped
    assert task_service.stopped
    assert conversation_service.stopped
    assert schedule_dispatcher.stopped
    assert trigger_dispatcher.stopped
    assert interactions.stopped
    assert capability_mcp_host.stopped
    assert not pid.exists()
