from __future__ import annotations

import pytest

from knoa_platform.tools.read_file import ReadFileTool
from knoa_platform.tools.write_file import WriteFileTool


class TestReadFileToolName:
    def test_name(self):
        t = ReadFileTool()
        assert t.name == "read_file"

    def test_schema(self):
        t = ReadFileTool()
        s = t.definition()
        assert s["name"] == "read_file"
        assert "inputSchema" in s


class TestWriteFileToolName:
    def test_name(self):
        t = WriteFileTool()
        assert t.name == "write_file"

    def test_schema(self):
        t = WriteFileTool()
        s = t.definition()
        assert s["name"] == "write_file"
        assert "inputSchema" in s


class TestWriteAndRead:
    @pytest.mark.asyncio
    async def test_write_and_read(self, tmp_path):
        write_tool = WriteFileTool()
        read_tool = ReadFileTool()
        path = str(tmp_path / "test.txt")
        await write_tool.execute(path=path, content="hello world")
        result = await read_tool.execute(path=path)
        assert result["content"] == "hello world"

    @pytest.mark.asyncio
    async def test_write_creates_dirs(self, tmp_path):
        write_tool = WriteFileTool()
        path = str(tmp_path / "sub" / "dir" / "test.txt")
        result = await write_tool.execute(path=path, content="nested")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_bytes_written(self, tmp_path):
        write_tool = WriteFileTool()
        path = str(tmp_path / "test.txt")
        result = await write_tool.execute(path=path, content="hello")
        assert result["bytes_written"] == 5

    @pytest.mark.asyncio
    async def test_read_expands_home_directory(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        desktop = tmp_path / "Desktop"
        desktop.mkdir()
        (desktop / "usable.txt").write_text("works", encoding="utf-8")

        result = await ReadFileTool().execute(path="~/Desktop/usable.txt")

        assert result["content"] == "works"

    @pytest.mark.asyncio
    async def test_relative_paths_use_configured_working_directory(self, tmp_path):
        (tmp_path / "relative.txt").write_text("bounded", encoding="utf-8")

        result = await ReadFileTool(working_directory=tmp_path).execute(path="relative.txt")

        assert result["content"] == "bounded"


class TestReadFileRead:
    @pytest.mark.asyncio
    async def test_read_nonexistent(self, tmp_path):
        read_tool = ReadFileTool()
        result = await read_tool.execute(path=str(tmp_path / "nope.txt"))
        assert "error" in result

    @pytest.mark.asyncio
    async def test_read_directory(self, tmp_path):
        read_tool = ReadFileTool()
        result = await read_tool.execute(path=str(tmp_path))
        assert "error" in result

    @pytest.mark.asyncio
    async def test_large_file_reads_only_bounded_prefix(self, tmp_path):
        path = tmp_path / "large.txt"
        path.write_bytes(b"a" * 600_000)

        result = await ReadFileTool().execute(path=str(path))

        assert result["truncated"] is True
        assert len(result["content"].encode()) == 512_000

    @pytest.mark.asyncio
    async def test_line_limit_is_bounded(self, tmp_path):
        path = tmp_path / "lines.txt"
        path.write_text("one\ntwo\n", encoding="utf-8")

        result = await ReadFileTool().execute(path=str(path), limit=1_001)

        assert result == {"error": "limit must contain 1-1000 lines"}

    @pytest.mark.asyncio
    async def test_large_file_line_window_remains_bounded(self, tmp_path):
        path = tmp_path / "large-lines.txt"
        path.write_bytes((b"skip\n" * 120_000) + b"target\nrest\n")

        result = await ReadFileTool().execute(
            path=str(path),
            offset=120_001,
            limit=1,
        )

        assert result["content"] == "target\n"
        assert result["truncated"] is True
