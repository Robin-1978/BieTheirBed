from __future__ import annotations

import base64

import pytest

from knoa_platform import desktop_companion
from knoa_platform.artifacts import ArtifactStore
from knoa_platform.context.scope import MemoryScope, reset_memory_scope, set_memory_scope
from knoa_platform.tools.base import ToolBase, ToolCapability, ToolEffect, ToolRisk
from knoa_platform.tools.screenshot import ScreenshotTool
from knoa_platform.tools.ui import UiTool


class _DesktopTool(ToolBase):
    name = "mouse"
    description = "test desktop tool"
    effect = ToolEffect.DESKTOP_CONTROL
    capabilities = frozenset({ToolCapability.DESKTOP_CONTROL})
    risk = ToolRisk.HIGH

    async def execute(self, **kwargs):
        return {"direct": kwargs}

    def definition(self):
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {"type": "object", "properties": {}},
        }


@pytest.mark.asyncio
async def test_desktop_tool_uses_companion_when_service_session_requires_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(desktop_companion, "desktop_companion_required", lambda: True)
    monkeypatch.setattr(
        desktop_companion,
        "invoke_desktop_companion",
        lambda name, arguments: calls.append((name, arguments)) or {"success": True},
    )

    result = await _DesktopTool().execute_scoped(None, x=10)

    assert result == {"success": True}
    assert calls == [("mouse", {"x": 10})]


@pytest.mark.asyncio
async def test_desktop_tool_executes_directly_in_user_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(desktop_companion, "desktop_companion_required", lambda: False)

    assert await _DesktopTool().execute_scoped(None, x=10) == {"direct": {"x": 10}}


@pytest.mark.asyncio
async def test_ui_tool_forwards_backend_to_desktop_companion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(desktop_companion, "desktop_companion_required", lambda: True)
    monkeypatch.setattr(
        desktop_companion,
        "invoke_desktop_companion",
        lambda name, arguments: calls.append((name, arguments)) or {"success": True},
    )

    result = await UiTool(ui_backend="uia").execute_scoped(None, action="snapshot")

    assert result == {"success": True}
    assert calls == [("ui", {"action": "snapshot", "_ui_backend": "uia"})]


@pytest.mark.asyncio
async def test_companion_screenshot_is_registered_as_user_artifact(tmp_path) -> None:
    store = ArtifactStore(
        tmp_path / "attachments",
        db_path=tmp_path / "data" / "assistant.db",
    )
    tool = ScreenshotTool(store, tmp_path / "attachments" / "screenshots")
    token = set_memory_scope(MemoryScope(principal_id="local", session_id="session-a"))
    try:
        result = await tool.consume_desktop_companion_result(
            {
                "media_type": "image/jpeg",
                "content_base64": base64.b64encode(b"jpeg-content").decode("ascii"),
            }
        )
    finally:
        reset_memory_scope(token)

    assert result["success"] is True
    assert result["artifact"]["visibility"] == "user"
    assert result["image_ref"]["visibility"] == "agent"


def test_companion_pipe_is_bound_to_one_interactive_session() -> None:
    assert desktop_companion._pipe_address(7) == r"\\.\pipe\knoa-desktop-7"
    with pytest.raises(desktop_companion.DesktopCompanionError):
        desktop_companion._pipe_address(0)


def test_companion_rejects_short_authentication_token(tmp_path) -> None:
    token = tmp_path / "companion.token"
    token.write_text("short", encoding="utf-8")

    with pytest.raises(desktop_companion.DesktopCompanionError, match="invalid"):
        desktop_companion._read_authkey(token)
