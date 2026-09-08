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


class TestShellReadOnlyPolicy:
    def test_read_only_inspection_commands(self):
        from knoa_platform.tools.base import ToolEffect, ToolRisk
        from knoa_platform.tools.shell import is_read_only_shell_command

        t = ShellTool()

        read_commands = [
            "ls -la /home/robin/.local/share/knoa/ 2>/dev/null; echo ====; find /home/robin/.local/share/knoa -maxdepth 3 -type d 2>/dev/null | head -40",
            "find /home/robin/.local/share/knoa -maxdepth 4 \\( -iname \"*.log\" -o -iname \"*.db\" \\) 2>/dev/null | head -50",
            "echo ====CONTROL_DB_TABLES====; sqlite3 /home/robin/.local/share/knoa/hosted-hub/control.db \".tables\" 2>&1",
            "sqlite3 -header -column /home/robin/.local/share/knoa/hosted-hub/tenants/ws_8bl_VSJVTyBYHu37Aq7tGwKF/hub.db \"SELECT * FROM nodes;\" 2>&1",
            "sqlite3 /path/db.sqlite \".schema nodes\" 2>&1; echo ====DEPLOYMENTS====; sqlite3 -header -column /path/db.sqlite \"SELECT * FROM deployments;\" 2>&1 | head -40",
            "cat /tmp/test.log | grep -i error",
            "git status",
            "git log -n 5",
            "git diff HEAD~1",
            "ps aux | grep knoa",
            "df -h",
            "uptime",
            "tail -n 100 /tmp/file.log",
        ]

        for cmd in read_commands:
            assert is_read_only_shell_command(cmd) is True, f"Expected read-only for: {cmd}"
            policy = t.policy_for({"command": cmd})
            assert policy.effect == ToolEffect.READ_ONLY
            assert policy.risk == ToolRisk.LOW
            assert policy.requires_confirmation is False

    def test_write_and_side_effect_commands_require_confirmation(self):
        from knoa_platform.tools.base import ToolEffect, ToolRisk
        from knoa_platform.tools.shell import is_read_only_shell_command

        t = ShellTool()

        write_commands = [
            "rm -rf /tmp/test",
            "sqlite3 db.sqlite \"INSERT INTO users VALUES (1);\"",
            "sqlite3 db.sqlite \"DROP TABLE nodes;\"",
            "sqlite3 db.sqlite \"UPDATE nodes SET name='x';\"",
            "sqlite3 db.sqlite \"DELETE FROM nodes;\"",
            "ls -la > /tmp/output.txt",
            "echo hello > /tmp/file",
            "find /tmp -delete",
            "find /tmp -exec rm {} \\;",
            "git commit -m test",
            "git push origin master",
            "echo $(rm -rf /)",
            "echo `whoami`",
            "curl -X POST https://example.com/api",
            "systemctl restart knoa",
            "",
        ]

        for cmd in write_commands:
            assert is_read_only_shell_command(cmd) is False, f"Expected write/risky for: {cmd}"
            policy = t.policy_for({"command": cmd})
            assert policy.effect == ToolEffect.LOCAL_WRITE
            assert policy.risk == ToolRisk.HIGH
            assert policy.requires_confirmation is True
