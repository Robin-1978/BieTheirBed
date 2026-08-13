from __future__ import annotations

import asyncio

import pytest

from knoa_platform.tools.shell import ShellTool


class TestShellToolName:
    def test_name(self):
        t = ShellTool()
        assert t.name == "run_command"

    def test_schema(self):
        t = ShellTool()
        s = t.definition()
        assert s["name"] == "run_command"
        assert "inputSchema" in s


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

    @pytest.mark.asyncio
    async def test_output_limit_kills_command_and_returns_promptly(self):
        import time

        t = ShellTool()
        started = time.monotonic()
        result = await t.execute(command="yes output", timeout_seconds=10)
        elapsed = time.monotonic() - started

        assert "output exceeded" in result.get("error", "").lower()
        assert result["output_truncated"] is True
        assert len(result["stdout"].encode()) <= 1024 * 1024
        assert elapsed < 3

    @pytest.mark.asyncio
    async def test_cancellation_kills_shell_process_group(self, tmp_path):
        import os

        pid_file = tmp_path / "shell.pid"
        t = ShellTool()
        task = asyncio.create_task(
            t.execute(
                command=f"echo $$ > {pid_file}; sleep 30",
                timeout_seconds=30,
            )
        )
        while not pid_file.exists():
            await asyncio.sleep(0.01)
        pid = int(pid_file.read_text().strip())

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


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
