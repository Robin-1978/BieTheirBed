"""Tests for WindowTool using the modern pywinctl API (box / getAppName / getPID)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pc_assistant.tools.window import WindowTool


class FakeWin:
    """Mimics pywinctl 0.4.x LinuxWindow: `box`, `getAppName()`, `getPID()`.

    Deliberately omits `bounds` / `className` / `processID` to catch the
    regression where the tool accessed the old API and raised AttributeError.
    """

    def __init__(self, title, app, pid, left, top, width, height, **flags):
        self.title = title
        self.app = app
        self.pid = pid
        self.box = SimpleNamespace(left=left, top=top, width=width, height=height)
        self.isVisible = flags.get("isVisible", True)
        self.isMinimized = flags.get("isMinimized", False)
        self.isMaximized = flags.get("isMaximized", False)
        self.isActive = flags.get("isActive", False)
        self.activated = False

    def getAppName(self):
        return self.app

    def getPID(self):
        return self.pid

    def activate(self):
        self.activated = True

    def restore(self):
        self.isMinimized = False


FAKE_WINDOWS = [
    FakeWin("Terminal", "gnome-terminal-", 100, 0, 0, 800, 600),
    FakeWin("Browser", "firefox", 200, 200, 50, 1200, 900, isMaximized=True),
]


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def tool():
    return WindowTool()


class TestWindowList:
    def test_lists_windows(self, tool):
        with patch("pywinctl.getAllWindows", return_value=FAKE_WINDOWS):
            res = _run(tool.execute(action="list"))
        assert res["count"] == 2
        titles = [w["title"] for w in res["windows"]]
        assert titles == sorted(titles)
        first = next(w for w in res["windows"] if w["title"] == "Browser")
        assert first["x"] == 200
        assert first["y"] == 50
        assert first["width"] == 1200
        assert first["height"] == 900
        assert first["process_id"] == 200
        assert first["app_name"] == "firefox"
        assert first["window_id"] == "200:Browser"
        assert first["is_active"] is False
        assert first["is_maximized"] is True

    def test_list_errors_when_pywinctl_missing(self, tool):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *a, **k):
            if name == "pywinctl":
                raise ImportError("no pywinctl")
            return real_import(name, *a, **k)

        with patch("builtins.__import__", side_effect=fake_import):
            res = _run(tool.execute(action="list"))
        assert "pywinctl not installed" in res["error"]


class TestWindowInfo:
    def test_info_by_title(self, tool):
        with patch("pywinctl.getAllWindows", return_value=FAKE_WINDOWS):
            res = _run(tool.execute(action="info", window_id="Browser"))
        assert res["title"] == "Browser"
        assert res["process_id"] == 200
        assert res["width"] == 1200

    def test_info_missing_window(self, tool):
        with patch("pywinctl.getAllWindows", return_value=FAKE_WINDOWS):
            res = _run(tool.execute(action="info", window_id="Nope"))
        assert "Window not found" in res["error"]

    def test_info_requires_window_id(self, tool):
        res = _run(tool.execute(action="info"))
        assert "window_id is required" in res["error"]

    def test_active_returns_directly_usable_window(self, tool):
        active = FakeWin("Editor", "code", 300, 5, 10, 900, 700, isActive=True)
        with patch("pywinctl.getActiveWindow", return_value=active):
            res = _run(tool.execute(action="active"))
        assert res["found"] is True
        assert res["window"]["window_id"] == "300:Editor"
        assert res["window"]["is_active"] is True


class TestWindowFocus:
    def test_focus_calls_activate(self, tool):
        with (
            patch("pywinctl.getAllWindows", return_value=FAKE_WINDOWS),
            patch("pywinctl.getActiveWindow", return_value=FAKE_WINDOWS[0]),
        ):
            res = _run(tool.execute(action="focus", window_id="Terminal"))
        assert res["success"] is True
        assert FAKE_WINDOWS[0].activated is True

    def test_focus_restores_minimized_window_and_verifies_active(self, tool):
        window = FakeWin("Chat", "chat", 300, 0, 0, 800, 600, isMinimized=True)
        with (
            patch("pywinctl.getAllWindows", return_value=[window]),
            patch("pywinctl.getActiveWindow", return_value=window),
        ):
            res = _run(tool.execute(action="focus", window_id="Chat"))

        assert res["success"] is True
        assert res["restored"] is True
        assert res["verified_active"] is True
        assert window.isMinimized is False

    def test_focus_fails_when_activation_cannot_be_verified(self, tool):
        window = FakeWin("Chat", "chat", 300, 0, 0, 800, 600)
        with (
            patch("pywinctl.getAllWindows", return_value=[window]),
            patch("pywinctl.getActiveWindow", return_value=None),
            patch("pc_assistant.tools.window.get_platform", return_value="windows"),
        ):
            res = _run(tool.execute(action="focus", window_id="Chat"))

        assert "not verified" in res["error"]

    def test_ambiguous_partial_title_fails_closed(self, tool):
        windows = [
            FakeWin("Project Alpha", "editor", 1, 0, 0, 100, 100),
            FakeWin("Project Beta", "editor2", 2, 0, 0, 100, 100),
        ]
        with patch("pywinctl.getAllWindows", return_value=windows):
            res = _run(tool.execute(action="focus", window_id="Project"))
        assert "Window not found" in res["error"]
        assert not any(window.activated for window in windows)
