from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from pc_assistant.artifacts import ArtifactStore
from pc_assistant.context.scope import MemoryScope, reset_memory_scope, set_memory_scope
from pc_assistant.tools.screenshot import ScreenshotTool


class _FakeMss:
    monitors = [object()]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def grab(self, monitor):
        del monitor
        return SimpleNamespace(size=(1, 1), bgra=b"\x00\x00\x00\xff")


@pytest.mark.asyncio
async def test_screenshot_returns_public_artifact_and_agent_image_ref(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setitem(sys.modules, "mss", SimpleNamespace(mss=_FakeMss))
    store = ArtifactStore(
        tmp_path / "attachments",
        db_path=tmp_path / "data" / "assistant.db",
    )
    tool = ScreenshotTool(store, tmp_path / "attachments" / "screenshots")
    token = set_memory_scope(MemoryScope(principal_id="local", session_id="session-a"))
    try:
        result = await tool.execute()
    finally:
        reset_memory_scope(token)

    assert result["success"] is True
    assert result["artifact"]["visibility"] == "user"
    assert "path" not in result["artifact"]
    assert result["image_ref"]["type"] == "image_ref"
    assert result["image_ref"]["visibility"] == "agent"
    assert result["image_ref"]["artifact_id"] == result["artifact"]["artifact_id"]
    hydrated = store.hydrate_ref("session-a", result["image_ref"])
    assert hydrated["image_url"].startswith("data:image/png;base64,")
