from __future__ import annotations

from types import SimpleNamespace

import pytest

from pc_assistant import async_main


@pytest.mark.asyncio
async def test_async_main_uses_textual_async_runner(monkeypatch) -> None:
    calls: list[str] = []

    class Config:
        @staticmethod
        def resolve_model():
            return SimpleNamespace(
                driver="http",
                provider_name="test",
                api_key="",
            )

    class Client:
        async def health(self):
            return SimpleNamespace(healthy=True, detail="")

        async def create_session(self):
            return "session-a"

        async def disconnect(self):
            calls.append("disconnect")

    class ChatApp:
        def __init__(self, _config, _client, session_handle):
            assert session_handle == "session-a"

        async def run_async(self):
            calls.append("run_async")

        def run(self):
            raise AssertionError("Textual sync runner must not be used")

    async def get_client(_config):
        return Client()

    monkeypatch.setattr("pc_assistant.config.load_config", lambda _path: Config())
    monkeypatch.setattr(
        "pc_assistant.service.core_lifecycle.get_core_client",
        get_client,
    )
    monkeypatch.setattr("pc_assistant.ui.core_app.CoreChatApp", ChatApp)

    assert await async_main(None, False) == 0
    assert calls == ["run_async", "disconnect"]
