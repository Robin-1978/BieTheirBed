from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from knoa_platform.tools.clipboard import ClipboardTool


class TestClipboardToolName:
    def test_name(self):
        t = ClipboardTool()
        assert t.name == "clipboard"

    def test_schema(self):
        t = ClipboardTool()
        s = t.definition()
        assert s["name"] == "clipboard"
        assert "inputSchema" in s


class TestClipboardRead:
    @pytest.mark.asyncio
    async def test_read_success(self):
        t = ClipboardTool()
        mock_pc = MagicMock()
        mock_pc.paste.return_value = "clipboard content"
        mock_pc.PyperclipException = Exception
        with patch.dict("sys.modules", {"pyperclip": mock_pc}):
            result = await t.execute(action="read")
            assert result["content"] == "clipboard content"

    @pytest.mark.asyncio
    async def test_read_error(self):
        t = ClipboardTool()
        mock_pc = MagicMock()
        exc_cls = type("PyperclipException", (Exception,), {})
        mock_pc.PyperclipException = exc_cls
        mock_pc.paste.side_effect = exc_cls("no clipboard")
        with patch.dict("sys.modules", {"pyperclip": mock_pc}):
            result = await t.execute(action="read")
            assert "error" in result


class TestClipboardWrite:
    @pytest.mark.asyncio
    async def test_write_success(self):
        t = ClipboardTool()
        mock_pc = MagicMock()
        mock_pc.PyperclipException = Exception
        with patch.dict("sys.modules", {"pyperclip": mock_pc}):
            result = await t.execute(action="write", content="new content")
            assert result["success"] is True
            mock_pc.copy.assert_called_once_with("new content")

    @pytest.mark.asyncio
    async def test_write_error(self):
        t = ClipboardTool()
        mock_pc = MagicMock()
        exc_cls = type("PyperclipException", (Exception,), {})
        mock_pc.PyperclipException = exc_cls
        mock_pc.copy.side_effect = exc_cls("no clipboard")
        with patch.dict("sys.modules", {"pyperclip": mock_pc}):
            result = await t.execute(action="write", content="test")
            assert "error" in result


class TestClipboardUnknownAction:
    @pytest.mark.asyncio
    async def test_unknown_action(self):
        t = ClipboardTool()
        result = await t.execute(action="unknown")
        assert "error" in result
