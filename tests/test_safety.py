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
        needs, reason = checker.needs_confirmation("write_file", {"path": "/tmp/test"})
        assert needs is True

    def test_needs_confirmation_read(self):
        checker = SafetyChecker()
        needs, _ = checker.needs_confirmation("read_file", {"path": "/tmp/test"})
        assert needs is False

    def test_needs_confirmation_kill(self):
        checker = SafetyChecker()
        needs, _ = checker.needs_confirmation("run_command", {"command": "kill 1234"})
        assert needs is True

    def test_check_tool_call_shell(self):
        checker = SafetyChecker()
        result = checker.check_tool_call("run_command", {"command": "rm -rf /"})
        assert result.allowed is False

    def test_artifact_prepare_allows_file_inside_working_directory(self, tmp_path):
        checker = SafetyChecker(
            working_directory=str(tmp_path),
            protected_paths=[str(tmp_path / "protected")],
        )

        assert checker.check_tool_call(
            "attach", {"path": "report.txt"}
        ).allowed
        needs, _ = checker.needs_confirmation(
            "attach", {"path": "report.txt"}
        )
        assert needs is False

    def test_artifact_prepare_outside_working_directory_requires_confirmation(self, tmp_path):
        checker = SafetyChecker(
            working_directory=str(tmp_path / "workspace"),
            protected_paths=[str(tmp_path / "protected")],
        )

        needs, reason = checker.needs_confirmation(
            "attach", {"path": str(tmp_path / "outside.txt")}
        )

        assert needs is True
        assert "outside working directory" in reason

    @pytest.mark.parametrize("command", [
        "loginctl unlock-session 3",
        "passwd robin",
        "gsettings set org.gnome.desktop.screensaver lock-enabled false",
    ])
    def test_authentication_and_lock_commands_require_confirmation(self, command):
        checker = SafetyChecker()
        needs, _ = checker.needs_confirmation("run_command", {"command": command})
        assert needs is True

    def test_keyboard_text_requires_confirmation(self):
        checker = SafetyChecker()
        needs, reason = checker.needs_confirmation(
            "type_text", {"text": "sensitive"}
        )
        assert needs is True
        assert "text input" in reason

    def test_keyboard_shortcuts_require_confirmation(self):
        checker = SafetyChecker()
        needs, _ = checker.needs_confirmation(
            "hotkey", {"keys": ["ctrl", "s"]}
        )
        assert needs is True

    @pytest.mark.parametrize("key", ["enter", "delete", "backspace", "escape"])
    def test_keyboard_execution_keys_require_confirmation(self, key):
        checker = SafetyChecker()
        needs, _ = checker.needs_confirmation("press_key", {"key": key})
        assert needs is True

    def test_keyboard_navigation_key_does_not_require_confirmation(self):
        checker = SafetyChecker()
        needs, _ = checker.needs_confirmation("press_key", {"key": "left"})
        assert needs is False

    @pytest.mark.parametrize(
        "tool_name,arguments",
        [
            ("mouse", {"action": "click"}),
            ("mouse", {"action": "drag"}),
            ("ui", {"action": "click", "element_id": "button-1"}),
            ("ui", {"action": "type", "element_id": "field-1"}),
            ("windows", {"action": "close", "window_id": "Editor"}),
        ],
    )
    def test_desktop_state_changes_require_confirmation(self, tool_name, arguments):
        checker = SafetyChecker()
        needs, _ = checker.needs_confirmation(tool_name, arguments)
        assert needs is True

    @pytest.mark.parametrize(
        "tool_name,arguments",
        [
            ("mouse", {"action": "position"}),
            ("mouse", {"action": "move", "x": 10, "y": 20}),
            ("ui", {"action": "list"}),
            ("windows", {"action": "focus", "window_id": "Editor"}),
        ],
    )
    def test_desktop_observation_and_positioning_do_not_require_confirmation(
        self, tool_name, arguments
    ):
        checker = SafetyChecker()
        needs, _ = checker.needs_confirmation(tool_name, arguments)
        assert needs is False

    def test_artifact_prepare_blocks_protected_path(self, tmp_path):
        protected = tmp_path / "protected"
        checker = SafetyChecker(
            working_directory=str(tmp_path),
            protected_paths=[str(protected)],
        )

        result = checker.check_tool_call(
            "attach", {"path": str(protected / "secret.txt")}
        )

        assert result.allowed is False
