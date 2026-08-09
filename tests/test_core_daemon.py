from __future__ import annotations

import stat
from types import SimpleNamespace

import pytest

from pc_assistant.config import AppConfig
from pc_assistant.service.core_daemon import CoreDaemon


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


class _TaskService(_Host):
    pass


class _ScheduleDispatcher(_Host):
    pass


class _TriggerDispatcher(_Host):
    pass


@pytest.mark.asyncio
async def test_core_daemon_owns_host_pid_and_cleanup_lifecycle(
    tmp_path,
    monkeypatch,
) -> None:
    host = _Host()
    extensions = _Extensions()
    task_service = _TaskService()
    schedule_dispatcher = _ScheduleDispatcher()
    trigger_dispatcher = _TriggerDispatcher()
    pid = tmp_path / "service.pid"
    composition = SimpleNamespace(
        host=host,
        extensions=extensions,
        task_service=task_service,
        schedule_dispatcher=schedule_dispatcher,
        trigger_dispatcher=trigger_dispatcher,
        paths=SimpleNamespace(pid=pid),
        artifacts=SimpleNamespace(cleanup_expired=lambda: None),
    )
    monkeypatch.setattr(
        "pc_assistant.service.core_daemon.build_core_runtime",
        lambda config: composition,
    )
    daemon = CoreDaemon(
        AppConfig(fallback_enabled=False),
        log_path=tmp_path / "service.log",
    )

    await daemon.start()

    assert host.started
    assert extensions.started
    assert task_service.started
    assert schedule_dispatcher.started
    assert trigger_dispatcher.started
    assert pid.exists()
    assert str(tmp_path / "service.log") in pid.read_text(encoding="utf-8")
    assert stat.S_IMODE(pid.stat().st_mode) == 0o600

    await daemon.stop()

    assert host.stopped
    assert extensions.stopped
    assert task_service.stopped
    assert schedule_dispatcher.stopped
    assert trigger_dispatcher.stopped
    assert not pid.exists()
