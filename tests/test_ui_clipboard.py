"""Tests for TUI clipboard helpers and message copy-text extraction."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from pc_assistant.ui.clipboard import available_tool, copy_or_save, copy_to_clipboard
from pc_assistant.config import AppConfig
from pc_assistant.ui.core_app import CoreChatApp
from pc_assistant.ui.widgets import CommandOutput, UserMessage


def _run(coro):
    return asyncio.run(coro)


class TestClipboardHelpers:
    def test_available_tool(self):
        assert isinstance(available_tool(), str)

    def test_empty_string_rejected(self):
        ok, detail = _run(copy_to_clipboard(""))
        assert ok is False
        assert "Nothing" in detail

    class FakeProc:
        returncode = 0

        def communicate(self, data, timeout=None):
            return (b"", b"")

    @staticmethod
    def _xclip_only(tool: str) -> str:
        return "/usr/bin/xclip" if tool == "xclip" else ""

    @patch("pc_assistant.ui.clipboard.shutil.which", side_effect=_xclip_only)
    def test_posix_popen_path(self, mock_which):
        with patch("pc_assistant.ui.clipboard.subprocess.Popen", return_value=self.FakeProc()) as popen:
            ok, detail = _run(copy_to_clipboard("hello clipboard"))
        assert ok is True
        assert "xclip" in detail
        popen.assert_called_once()
        assert popen.call_args.kwargs.get("start_new_session") is True

    @patch("pc_assistant.ui.clipboard.subprocess.Popen", side_effect=RuntimeError("boom"))
    @patch("pc_assistant.ui.clipboard.shutil.which", side_effect=_xclip_only)
    def test_proc_exception_returns_false(self, mock_which, mock_popen):
        ok, detail = _run(copy_to_clipboard("hi"))
        assert ok is False

    @patch("pc_assistant.ui.clipboard.shutil.which", return_value="")
    def test_fallback_writes_file(self, mock_which, tmp_path):
        fallback = str(tmp_path / "clip.txt")
        ok, detail = _run(copy_or_save("saved content", fallback_path=fallback))
        assert ok is True
        assert "saved" in detail
        with open(fallback, encoding="utf-8") as fh:
            assert fh.read() == "saved content"


class TestWidgetCopyText:
    def test_user_message_copy(self):
        msg = UserMessage("Hello there")
        assert msg.copy_text == "Hello there"

    def test_command_output_copy(self):
        out = CommandOutput("| a | b |")
        assert out.copy_text == "| a | b |"

    def test_assistant_message_copy(self):
        from types import SimpleNamespace

        from pc_assistant.ui.widgets import AssistantMessage, ToolCallPanel

        msg = AssistantMessage()
        panel = ToolCallPanel("shell", {"command": "ls"})
        fake_md = SimpleNamespace(source="# Answer\n\nHello")
        msg._nodes = [panel, fake_md]
        text = msg.copy_text
        assert "shell" in text
        assert "Hello" in text


class TestSelectionCopy:
    def test_right_click_copies_exact_selection_without_menu(self):
        from types import SimpleNamespace

        app = CoreChatApp(AppConfig(), _Client(), "session-a")
        copied = []
        stopped = []
        app._selected_text = lambda: "exact selected text"
        app._copy_worker = lambda text: copied.append(text)
        event = SimpleNamespace(
            button=3,
            prevent_default=lambda: stopped.append("prevented"),
            stop=lambda: stopped.append("stopped"),
        )

        app.on_mouse_down(event)

        assert copied == ["exact selected text"]
        assert stopped == ["prevented", "stopped"]

    def test_ctrl_c_is_not_overridden_and_escape_cancels(self):
        bindings = {key: action for key, action, _ in CoreChatApp.BINDINGS}
        assert "ctrl+c" not in bindings
        assert bindings["escape"] == "cancel_turn"

    async def test_textual_ctrl_c_copies_screen_selection(self):
        app = CoreChatApp(AppConfig(), _Client(), "session-a")
        copied: list[str] = []
        app.copy_to_clipboard = lambda text: copied.append(text)

        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            target = app.query_one(CommandOutput)
            target.text_select_all()
            selected = app.screen.get_selected_text()
            assert selected

            await pilot.press("ctrl+c")
            await pilot.pause()

        assert copied == [selected]


class _Client:
    def set_approval_handler(self, handler):
        self.handler = handler
