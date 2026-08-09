from __future__ import annotations

import pytest

from pc_assistant.config import AppConfig
from pc_assistant.service.application_daemon import ApplicationDaemon


class _Lifecycle:
    def __init__(self, name: str, events: list[str]) -> None:
        self.name = name
        self.names = ("feishu",) if name == "channels" else ()
        self._events = events

    async def start(self) -> None:
        self._events.append(f"start:{self.name}")

    async def stop(self) -> None:
        self._events.append(f"stop:{self.name}")


@pytest.mark.asyncio
async def test_application_daemon_composes_core_and_channels_above_both_layers(
    tmp_path,
    monkeypatch,
) -> None:
    events = []
    core = _Lifecycle("core", events)
    channels = _Lifecycle("channels", events)
    monkeypatch.setattr(
        "pc_assistant.service.application_daemon.CoreDaemon",
        lambda config, log_path: core,
    )
    monkeypatch.setattr(
        "pc_assistant.service.application_daemon.ChannelRuntime.from_config",
        lambda config: channels,
    )
    daemon = ApplicationDaemon(
        AppConfig(fallback_enabled=False),
        log_path=tmp_path / "service.log",
    )

    await daemon.start()
    await daemon.stop()

    assert events == [
        "start:core",
        "start:channels",
        "stop:channels",
        "stop:core",
    ]


@pytest.mark.asyncio
async def test_application_daemon_mounts_webhook_outside_core(
    tmp_path,
    monkeypatch,
) -> None:
    events = []
    core = _Lifecycle("core", events)
    channels = _Lifecycle("channels", events)
    webhooks = _Lifecycle("webhook", events)
    monkeypatch.setattr(
        "pc_assistant.service.application_daemon.CoreDaemon",
        lambda config, log_path: core,
    )
    monkeypatch.setattr(
        "pc_assistant.service.application_daemon.ChannelRuntime.from_config",
        lambda config: channels,
    )
    monkeypatch.setattr(
        "pc_assistant.adapters.WebhookAdapter",
        lambda config: webhooks,
    )
    daemon = ApplicationDaemon(
        AppConfig(
            fallback_enabled=False,
            webhook_enabled=True,
            webhook_routes={
                "jira": {
                    "trigger_id": "trigger-a",
                    "principal_id": "local",
                    "secret": "0123456789abcdef0123456789abcdef",
                }
            },
        ),
        log_path=tmp_path / "service.log",
    )

    await daemon.start()
    await daemon.stop()

    assert events == [
        "start:core",
        "start:webhook",
        "start:channels",
        "stop:channels",
        "stop:webhook",
        "stop:core",
    ]
