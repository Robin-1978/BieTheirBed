from __future__ import annotations

from typing import Any

import pytest

from pc_assistant.tools.base import ToolBase
from pc_assistant.tools.filesystem import FilesystemTool


class TestFilesystemToolName:
    def test_name(self):
        t = FilesystemTool()
        assert t.name == "filesystem"

    def test_schema(self):
        t = FilesystemTool()
        s = t.schema()
        assert s["name"] == "filesystem"
        assert "parameters" in s


class TestFilesystemWriteAndRead:
    @pytest.mark.asyncio
    async def test_write_and_read(self, tmp_path):
        t = FilesystemTool()
        path = str(tmp_path / "test.txt")
        await t.execute(action="write", path=path, content="hello world")
        result = await t.execute(action="read", path=path)
        assert result["content"] == "hello world"

    @pytest.mark.asyncio
    async def test_write_creates_dirs(self, tmp_path):
        t = FilesystemTool()
        path = str(tmp_path / "sub" / "dir" / "test.txt")
        result = await t.execute(action="write", path=path, content="nested")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_bytes_written(self, tmp_path):
        t = FilesystemTool()
        path = str(tmp_path / "test.txt")
        result = await t.execute(action="write", path=path, content="hello")
        assert result["bytes_written"] == 5

    @pytest.mark.asyncio
    async def test_read_expands_home_directory(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        desktop = tmp_path / "Desktop"
        desktop.mkdir()
        (desktop / "usable.txt").write_text("works", encoding="utf-8")

        result = await FilesystemTool().execute(
            action="read",
            path="~/Desktop/usable.txt",
        )

        assert result["content"] == "works"

    @pytest.mark.asyncio
    async def test_relative_paths_use_configured_working_directory(self, tmp_path):
        (tmp_path / "relative.txt").write_text("bounded", encoding="utf-8")

        result = await FilesystemTool(working_directory=tmp_path).execute(
            action="read",
            path="relative.txt",
        )

        assert result["content"] == "bounded"


class TestFilesystemRead:
    @pytest.mark.asyncio
    async def test_read_nonexistent(self, tmp_path):
        t = FilesystemTool()
        result = await t.execute(action="read", path=str(tmp_path / "nope.txt"))
        assert "error" in result

    @pytest.mark.asyncio
    async def test_read_directory(self, tmp_path):
        t = FilesystemTool()
        result = await t.execute(action="read", path=str(tmp_path))
        assert "error" in result


class TestFilesystemList:
    @pytest.mark.asyncio
    async def test_list(self, tmp_path):
        t = FilesystemTool()
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        result = await t.execute(action="list", path=str(tmp_path))
        assert "entries" in result
        names = [e["name"] for e in result["entries"]]
        assert "a.txt" in names
        assert "b.txt" in names

    @pytest.mark.asyncio
    async def test_list_not_directory(self, tmp_path):
        t = FilesystemTool()
        file_path = tmp_path / "file.txt"
        file_path.write_text("content")
        result = await t.execute(action="list", path=str(file_path))
        assert "error" in result


class TestFilesystemMkdir:
    @pytest.mark.asyncio
    async def test_mkdir(self, tmp_path):
        t = FilesystemTool()
        path = str(tmp_path / "new_dir")
        result = await t.execute(action="mkdir", path=path)
        assert result["success"] is True


class TestFilesystemExists:
    @pytest.mark.asyncio
    async def test_exists(self, tmp_path):
        t = FilesystemTool()
        path = str(tmp_path / "test.txt")
        await t.execute(action="write", path=path, content="hi")
        result = await t.execute(action="exists", path=path)
        assert result["exists"] is True

    @pytest.mark.asyncio
    async def test_exists_not_found(self, tmp_path):
        t = FilesystemTool()
        result = await t.execute(action="exists", path=str(tmp_path / "nope.txt"))
        assert result["exists"] is False


class TestFilesystemDelete:
    @pytest.mark.asyncio
    async def test_delete(self, tmp_path):
        t = FilesystemTool()
        path = str(tmp_path / "test.txt")
        await t.execute(action="write", path=path, content="hi")
        result = await t.execute(action="delete", path=path)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_delete_directory(self, tmp_path):
        t = FilesystemTool()
        dir_path = tmp_path / "subdir"
        dir_path.mkdir()
        (dir_path / "file.txt").write_text("content")
        result = await t.execute(action="delete", path=str(dir_path))
        assert result["success"] is True


class TestFilesystemCopy:
    @pytest.mark.asyncio
    async def test_copy(self, tmp_path):
        t = FilesystemTool()
        src = str(tmp_path / "src.txt")
        dst = str(tmp_path / "dst.txt")
        await t.execute(action="write", path=src, content="copy me")
        result = await t.execute(action="copy", path=src, destination=dst)
        assert result["success"] is True
        read_result = await t.execute(action="read", path=dst)
        assert read_result["content"] == "copy me"


class TestFilesystemMove:
    @pytest.mark.asyncio
    async def test_move(self, tmp_path):
        t = FilesystemTool()
        src = str(tmp_path / "src.txt")
        dst = str(tmp_path / "dst.txt")
        await t.execute(action="write", path=src, content="move me")
        result = await t.execute(action="move", path=src, destination=dst)
        assert result["success"] is True
        src_exists = await t.execute(action="exists", path=src)
        assert src_exists["exists"] is False
        dst_exists = await t.execute(action="exists", path=dst)
        assert dst_exists["exists"] is True


class TestFilesystemUnknownAction:
    @pytest.mark.asyncio
    async def test_unknown_action(self):
        t = FilesystemTool()
        result = await t.execute(action="unknown")
        assert "error" in result
