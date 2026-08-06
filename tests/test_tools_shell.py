from __future__ import annotations

import pytest

from pc_assistant.tools.shell import ShellTool


class TestShellToolName:
    def test_name(self):
        t = ShellTool()
        assert t.name == "run_command"

    def test_schema(self):
        t = ShellTool()
        s = t.schema()
        assert s["name"] == "run_command"
        assert "parameters" in s


class TestShellEcho:
    @pytest.mark.asyncio
    async def test_echo(self):
        t = ShellTool()
        result = await t.execute(command="echo hello")
        assert "hello" in result.get("stdout", "") or "hello" in result.get("output", "")


class TestShellTimeout:
    @pytest.mark.asyncio
    async def test_timeout(self):
        t = ShellTool()
        result = await t.execute(command="Start-Sleep -Seconds 30", timeout=1)
        assert result.get("returncode", 0) != 0 or "error" in result or "timeout" in str(result).lower()

    @pytest.mark.asyncio
    async def test_timeout_kills_descendants_and_returns_promptly(self):
        """A descendant must not keep stdout open after the shell is killed."""
        import time

        t = ShellTool()
        started = time.monotonic()
        result = await t.execute(command="sleep 30 & wait", timeout_seconds=0.2)
        elapsed = time.monotonic() - started

        assert "timed out" in result.get("error", "").lower()
        assert elapsed < 3


class TestShellNoCommand:
    @pytest.mark.asyncio
    async def test_no_command(self):
        t = ShellTool()
        result = await t.execute()
        assert "error" in result


class TestShellInvalidCommand:
    @pytest.mark.asyncio
    async def test_invalid_command(self):
        t = ShellTool()
        result = await t.execute(command="this_command_does_not_exist_12345")
        assert result.get("returncode", 0) != 0 or "error" in result


class TestShellWithCwd:
    @pytest.mark.asyncio
    async def test_shell_with_cwd(self, tmp_path):
        t = ShellTool()
        result = await t.execute(command="echo test", cwd=str(tmp_path))
        assert "test" in result.get("stdout", "") or "test" in result.get("output", "")


class TestShellStderr:
    @pytest.mark.asyncio
    async def test_stderr(self):
        t = ShellTool()
        result = await t.execute(command="Write-Error 'test error'")
        assert result.get("returncode", 0) != 0 or "error" in result.get("stderr", "").lower()
