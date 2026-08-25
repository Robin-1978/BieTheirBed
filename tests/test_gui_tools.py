"""Tests for semantic GUI tools."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from knoa_agent.runtime import KnoaAgentRuntime
from knoa_platform.tools.ui import UiTool
from knoa_platform.vision.a11y import _AtspiBackend, _UiaBackend


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
    assert "characters=4" in description
    assert "knoa" not in description


def test_should_verify_skips_mouse_position() -> None:
    assert KnoaAgentRuntime._should_verify_gui_action("mouse", {"action": "position"}) is False
    assert KnoaAgentRuntime._should_verify_gui_action("mouse", {"action": "click"}) is True


def test_atspi_snapshot_preserves_original_window_index() -> None:
    def node(name: str) -> SimpleNamespace:
        return SimpleNamespace(
            name=name,
            roleName="frame",
            childCount=0,
            state=SimpleNamespace(getStates=lambda: []),
            queryComponent=lambda: (_ for _ in ()).throw(RuntimeError()),
        )

    children = [node("Other"), node("Target")]
    desktop = SimpleNamespace(
        childCount=len(children),
        getChildAtIndex=lambda index: children[index],
    )
    backend = object.__new__(_AtspiBackend)
    backend._pyatspi = SimpleNamespace(
        Registry=SimpleNamespace(getDesktop=lambda _index: desktop)
    )

    elements = backend.get_accessible_tree("Target")

    assert [element.path for element in elements] == ["1"]


def test_uia_snapshot_preserves_original_window_index() -> None:
    rectangle = SimpleNamespace(
        left=0,
        top=0,
        width=lambda: 100,
        height=lambda: 30,
    )

    def window(name: str) -> SimpleNamespace:
        wrapper = SimpleNamespace(
            element_info=SimpleNamespace(
                control_type="Button",
                name=name,
                rectangle=rectangle,
                enabled=True,
                visible=True,
            ),
            children=lambda: [],
        )
        return SimpleNamespace(
            window_text=lambda: name,
            wrapper_object=lambda: wrapper,
        )

    windows = [window("Other"), window("Target")]
    desktop = SimpleNamespace(windows=lambda: windows)
    backend = object.__new__(_UiaBackend)
    backend._Desktop = lambda **_kwargs: desktop

    elements = backend.get_accessible_tree("Target")

    assert [element.path for element in elements] == ["1"]
