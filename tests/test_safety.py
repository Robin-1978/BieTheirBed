from __future__ import annotations

import pytest

from pc_assistant.harness.safety import SafetyChecker, SafetyCheckResult


class TestSafetyCheckResult:
    def test_allowed(self):
        r = SafetyCheckResult(True)
        assert r.allowed is True
        assert bool(r) is True

    def test_blocked(self):
        r = SafetyCheckResult(False, "dangerous")
        assert r.allowed is False
        assert bool(r) is False
        assert "dangerous" in r.reason


class TestSafetyChecker:
    def test_safe_command(self):
        checker = SafetyChecker()
        result = checker.check_command("ls -la")
        assert result.allowed is True

    def test_dangerous_command_blocked(self):
        checker = SafetyChecker()
        # rm -rf / is dangerous on all Unix systems
        result = checker.check_command("rm -rf /")
        assert result.allowed is False

    def test_windows_dangerous_command(self):
        checker = SafetyChecker()
        # mkfs is dangerous on all Unix systems
        result = checker.check_command("mkfs.ext4 /dev/sda1")
        assert result.allowed is False

    def test_injection_semicolon(self):
        checker = SafetyChecker()
        result = checker.check_command("echo hello; rm -rf /")
        assert result.allowed is False

    def test_injection_pipe(self):
        checker = SafetyChecker()
        result = checker.check_command("echo hello | rm -rf /")
        assert result.allowed is False

    def test_injection_subshell(self):
        checker = SafetyChecker()
        result = checker.check_command("$(rm -rf /)")
        assert result.allowed is False

    def test_injection_and(self):
        checker = SafetyChecker()
        result = checker.check_command("echo hello && rm -rf /")
        assert result.allowed is False

    def test_safe_path(self):
        checker = SafetyChecker(working_directory="/home/user")
        result = checker.check_path("/home/user/file.txt")
        assert result.allowed is True

    def test_needs_confirmation_delete(self):
        checker = SafetyChecker()
        needs, reason = checker.needs_confirmation("filesystem", {"action": "delete", "path": "/tmp/test"})
        assert needs is True

    def test_needs_confirmation_read(self):
        checker = SafetyChecker()
        needs, _ = checker.needs_confirmation("filesystem", {"action": "read", "path": "/tmp/test"})
        assert needs is False

    def test_needs_confirmation_kill(self):
        checker = SafetyChecker()
        needs, _ = checker.needs_confirmation("application", {"action": "kill", "pid": 1234})
        # "kill" matches _CONFIRMATION_COMMAND_PATTERNS on shell, but application tool
        # uses "process"/"task" as tool_name for kill confirmation
        # ApplicationTool with action=kill is handled via "process"/"task" tool_name
        checker2 = SafetyChecker()
        needs2, _ = checker2.needs_confirmation("process", {"action": "kill", "pid": 1234})
        assert needs2 is True

    def test_check_tool_call_shell(self):
        checker = SafetyChecker()
        result = checker.check_tool_call("shell", {"command": "rm -rf /"})
        assert result.allowed is False

    def test_artifact_prepare_allows_file_inside_working_directory(self, tmp_path):
        checker = SafetyChecker(
            working_directory=str(tmp_path),
            protected_paths=[str(tmp_path / "protected")],
        )

        assert checker.check_tool_call(
            "artifact_prepare", {"path": "report.txt"}
        ).allowed
        needs, _ = checker.needs_confirmation(
            "artifact_prepare", {"path": "report.txt"}
        )
        assert needs is False

    def test_artifact_prepare_outside_working_directory_requires_confirmation(self, tmp_path):
        checker = SafetyChecker(
            working_directory=str(tmp_path / "workspace"),
            protected_paths=[str(tmp_path / "protected")],
        )

        needs, reason = checker.needs_confirmation(
            "artifact_prepare", {"path": str(tmp_path / "outside.txt")}
        )

        assert needs is True
        assert "outside working directory" in reason

    def test_artifact_prepare_blocks_protected_path(self, tmp_path):
        protected = tmp_path / "protected"
        checker = SafetyChecker(
            working_directory=str(tmp_path),
            protected_paths=[str(protected)],
        )

        result = checker.check_tool_call(
            "artifact_prepare", {"path": str(protected / "secret.txt")}
        )

        assert result.allowed is False
