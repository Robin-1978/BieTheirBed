from __future__ import annotations

import pytest

from knoa_platform.config import AppConfig
from knoa_platform.service.core_lifecycle import get_core_client


@pytest.mark.asyncio
async def test_lifecycle_connects_to_authenticated_loopback_websocket(
    tmp_path,
    monkeypatch,
) -> None:
    expected = object()
    calls = []

    async def connect(cls, uri, credential, **kwargs):
        del cls
        calls.append((uri, credential, kwargs))
        return expected

    monkeypatch.setattr(
        "knoa_platform.service.core_lifecycle.CoreClient.connect",
        classmethod(connect),
    )
    monkeypatch.setattr(
        "knoa_platform.service.core_lifecycle.resolve_local_service_token",
        lambda paths: "local-secret",
    )
    monkeypatch.setattr(
        "knoa_platform.service.core_lifecycle._start_core_daemon",
        lambda config: pytest.fail("daemon should not be started"),
    )

    result = await get_core_client(
        AppConfig(
            fallback_enabled=False,
            runtime_root=str(tmp_path),
            service_host="127.0.0.1",
            service_port=9527,
            service_token="",
        ),
    )

    assert result is expected
    assert calls[0][:2] == ("ws://127.0.0.1:9527", "local-secret")
