from __future__ import annotations

from types import SimpleNamespace

import pytest

from pc_assistant.config import AppConfig
from pc_assistant.service.core_lifecycle import get_core_client


@pytest.mark.asyncio
async def test_lifecycle_prefers_protected_unix_core_endpoint(
    tmp_path,
    monkeypatch,
) -> None:
    socket_path = tmp_path / "core.sock"
    socket_path.touch()
    expected = object()
    calls = []

    monkeypatch.setattr(
        "pc_assistant.service.core_lifecycle.RuntimePaths.from_root",
        lambda root: SimpleNamespace(socket=socket_path),
    )

    async def connect_unix(cls, path, **kwargs):
        del cls
        calls.append((path, kwargs))
        return expected

    monkeypatch.setattr(
        "pc_assistant.service.core_lifecycle.CoreClient.connect_unix",
        classmethod(connect_unix),
    )
    monkeypatch.setattr(
        "pc_assistant.service.core_lifecycle._start_core_daemon",
        lambda config: pytest.fail("daemon should not be started"),
    )

    result = await get_core_client(
        AppConfig(fallback_enabled=False),
    )

    assert result is expected
    assert calls[0][0] == str(socket_path)
