"""Tests for semantic GUI tools."""
from __future__ import annotations

import pytest

from knoa_agent.runtime import KnoaAgentRuntime
from knoa_platform.tools.ui import UiTool


def test_ui_snapshot_reports_missing_backend_on_unsupported_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "knoa_platform.vision.a11y.get_platform",
        lambda: "macos",
    )
    tool = UiTool(ui_backend="auto")

    async def run() -> dict:
        return await tool.execute(action="snapshot")

    result = __import__("asyncio").run(run())
    assert "error" in result
    assert "not supported" in result["error"].casefold()


def test_ui_click_requires_target() -> None:
    tool = UiTool(ui_backend="none")

    async def run() -> dict:
        return await tool.execute(action="click")

    result = __import__("asyncio").run(run())
    assert result == {"error": "element_path or name is required"}


def test_describe_gui_action_for_ui_fill() -> None:
    description = KnoaAgentRuntime._describe_gui_action(
        "ui",
        {"action": "fill", "name": "Search", "value": "knoa"},
    )
    assert "ui fill" in description
    assert "Search" in description
    assert "knoa" in description


def test_should_verify_skips_mouse_position() -> None:
    assert KnoaAgentRuntime._should_verify_gui_action("mouse", {"action": "position"}) is False
    assert KnoaAgentRuntime._should_verify_gui_action("mouse", {"action": "click"}) is True
