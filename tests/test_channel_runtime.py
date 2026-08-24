from __future__ import annotations

import pytest

from knoa_platform.config import AppConfig
from knoa_platform.service.channel_runtime import ChannelRuntime


class _Channel:
    def __init__(self, name: str, events: list[str], *, fail: bool = False) -> None:
        self.name = name
        self._events = events
        self._fail = fail

    async def start(self) -> None:
        self._events.append(f"start:{self.name}")
        if self._fail:
            raise RuntimeError(self.name)

    async def stop(self) -> None:
        self._events.append(f"stop:{self.name}")


@pytest.mark.asyncio
async def test_channel_runtime_owns_mount_order() -> None:
    events = []
    runtime = ChannelRuntime((_Channel("first", events), _Channel("second", events)))

    await runtime.start()
    await runtime.stop()

    assert events == [
        "start:first",
        "start:second",
        "stop:second",
        "stop:first",
    ]


@pytest.mark.asyncio
async def test_channel_runtime_rolls_back_started_channels() -> None:
    events = []
    runtime = ChannelRuntime(
        (_Channel("first", events), _Channel("second", events, fail=True))
    )

    with pytest.raises(RuntimeError, match="second"):
        await runtime.start()

    assert events == ["start:first", "start:second", "stop:first"]


@pytest.mark.asyncio
async def test_channel_runtime_hot_enables_dingtalk_when_started_empty(
    tmp_path,
    monkeypatch,
) -> None:
    events: list[str] = []

    class _DingTalk(_Channel):
        def __init__(self, _config) -> None:
            super().__init__("dingtalk", events)

    monkeypatch.setattr("knoa_platform.channels.DingTalkChannel", _DingTalk)
    runtime = ChannelRuntime.from_config(
        AppConfig(runtime_root=str(tmp_path), fallback_enabled=False)
    )
    await runtime.start()

    status = await runtime.configure_dingtalk(
        enabled=True,
        client_id="ding-client",
        client_secret="ding-secret",
        robot_code="ding-robot",
        receive_id="",
    )

    assert status["running"] is True
    assert events == ["start:dingtalk"]
    await runtime.stop()
    assert events == ["start:dingtalk", "stop:dingtalk"]
