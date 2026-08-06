from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pc_assistant.harness.audit import AuditLogger
from pc_assistant.harness.limiter import RateLimiter
from pc_assistant.harness.safety import SafetyChecker, SafetyCheckResult
from pc_assistant.platform_ import get_default_dangerous_commands


class TestSafetyCheckResult:
    def test_allowed_true(self):
        r = SafetyCheckResult(True)
        assert bool(r) is True

    def test_allowed_false(self):
        r = SafetyCheckResult(False, "bad")
        assert bool(r) is False
        assert r.reason == "bad"

    def test_default_reason(self):
        r = SafetyCheckResult(True)
        assert r.reason == ""


class TestSafetyChecker:
    def test_default_dangerous_patterns(self):
        s = SafetyChecker()
        for pattern in get_default_dangerous_commands():
            assert not s.check_command(pattern).allowed

    def test_custom_dangerous_commands(self):
        s = SafetyChecker(dangerous_commands=["my_dangerous_cmd"])
        assert not s.check_command("my_dangerous_cmd").allowed
        assert s.check_command("safe_command").allowed

    def test_safe_commands(self):
        s = SafetyChecker()
        assert s.check_command("dir").allowed
        assert s.check_command("echo hello").allowed
        assert s.check_command("Get-Process").allowed

    def test_protected_path(self, tmp_path):
        protected = str(tmp_path / "secret")
        Path(protected).mkdir()
        s = SafetyChecker(protected_paths=[protected])
        assert not s.check_path(protected).allowed
        assert not s.check_path(str(Path(protected) / "file.txt")).allowed

    def test_allowed_path(self, tmp_path):
        s = SafetyChecker(protected_paths=[str(tmp_path / "secret")])
        assert s.check_path(str(tmp_path / "public")).allowed

    def test_invalid_path(self):
        s = SafetyChecker()
        result = s.check_path("\x00invalid")
        assert isinstance(result, SafetyCheckResult)

    def test_is_blocked_shell(self):
        s = SafetyChecker()
        dangerous_cmd = get_default_dangerous_commands()[0]
        result = s.is_blocked("run_command", {"command": dangerous_cmd})
        assert not result.allowed

    def test_is_blocked_shell_safe(self):
        s = SafetyChecker()
        result = s.is_blocked("run_command", {"command": "echo hello"})
        assert result.allowed

    def test_is_blocked_filesystem_protected(self, tmp_path):
        protected = str(tmp_path / "secret")
        Path(protected).mkdir()
        s = SafetyChecker(protected_paths=[protected])
        result = s.is_blocked("write_file", {"path": protected})
        assert not result.allowed

    def test_is_blocked_filesystem_safe(self):
        s = SafetyChecker()
        result = s.is_blocked("read_file", {"path": "C:\\Users\\test.txt"})
        assert result.allowed

    def test_is_blocked_process_list(self):
        s = SafetyChecker()
        result = s.is_blocked("process", {"action": "list"})
        assert result.allowed

    def test_is_blocked_unknown_tool(self):
        s = SafetyChecker()
        result = s.is_blocked("unknown_tool", {})
        assert result.allowed

    def test_needs_confirmation_shell_delete(self):
        s = SafetyChecker()
        needs, reason = s.needs_confirmation("run_command", {"command": "delete file.txt"})
        assert needs is True

    def test_needs_confirmation_shell_safe(self):
        s = SafetyChecker()
        needs, reason = s.needs_confirmation("run_command", {"command": "echo hello"})
        assert needs is False

    def test_needs_confirmation_shell_move(self):
        s = SafetyChecker()
        needs, reason = s.needs_confirmation("run_command", {"command": "move a.txt b.txt"})
        assert needs is True

    def test_needs_confirmation_shell_rename(self):
        s = SafetyChecker()
        needs, reason = s.needs_confirmation("run_command", {"command": "rename old.txt new.txt"})
        assert needs is True

    def test_needs_confirmation_filesystem_delete(self):
        s = SafetyChecker()
        needs, reason = s.needs_confirmation("write_file", {"path": "C:\\test.txt"})
        assert needs is True

    def test_needs_confirmation_filesystem_write(self):
        s = SafetyChecker()
        needs, reason = s.needs_confirmation("write_file", {"path": "C:\\test.txt"})
        assert needs is True

    def test_needs_confirmation_filesystem_read(self):
        s = SafetyChecker()
        needs, reason = s.needs_confirmation("read_file", {"path": "C:\\test.txt"})
        assert needs is False

    def test_needs_confirmation_filesystem_outside_working_dir(self, tmp_path):
        s = SafetyChecker(working_directory=str(tmp_path))
        outside = str(tmp_path.parent / "outside" / "file.txt")
        needs, reason = s.needs_confirmation("attach", {"path": outside})
        assert needs is True

    def test_needs_confirmation_filesystem_inside_working_dir(self, tmp_path):
        s = SafetyChecker(working_directory=str(tmp_path))
        needs, reason = s.needs_confirmation("read_file", {"path": str(tmp_path / "file.txt")})
        assert needs is False

    def test_needs_confirmation_process_list(self):
        s = SafetyChecker()
        needs, reason = s.needs_confirmation("process", {"action": "list"})
        assert needs is False

    def test_needs_confirmation_unknown_tool(self):
        s = SafetyChecker()
        needs, reason = s.needs_confirmation("unknown", {})
        assert needs is False

    def test_check_tool_call_delegates_to_is_blocked(self):
        s = SafetyChecker()
        dangerous_cmd = get_default_dangerous_commands()[0]
        result = s.check_tool_call("run_command", {"command": dangerous_cmd})
        assert not result.allowed

    def test_dangerous_result_is_hard_blocked(self):
        result = SafetyChecker().check_command("rm -rf /")
        assert result.overridable is False


class TestRateLimiter:
    def test_allows_within_limit(self):
        r = RateLimiter(max_calls=5, window_seconds=60)
        assert r.is_allowed("key1") is True

    def test_remaining(self):
        r = RateLimiter(max_calls=5, window_seconds=60)
        r.is_allowed("key1")
        assert r.remaining("key1") == 4

    def test_blocks_over_limit(self):
        r = RateLimiter(max_calls=2, window_seconds=60)
        assert r.is_allowed("key1") is True
        assert r.is_allowed("key1") is True
        assert r.is_allowed("key1") is False

    def test_separate_keys(self):
        r = RateLimiter(max_calls=1, window_seconds=60)
        assert r.is_allowed("key1") is True
        assert r.is_allowed("key2") is True

    def test_reset_key(self):
        r = RateLimiter(max_calls=1, window_seconds=60)
        r.is_allowed("key1")
        r.reset("key1")
        assert r.is_allowed("key1") is True

    def test_reset_all(self):
        r = RateLimiter(max_calls=1, window_seconds=60)
        r.is_allowed("key1")
        r.is_allowed("key2")
        r.reset()
        assert r.is_allowed("key1") is True
        assert r.is_allowed("key2") is True


class TestAuditLogger:
    def test_log_and_get_entries(self, tmp_path):
        a = AuditLogger(log_dir=str(tmp_path / "audit"))
        a.log(action="test_action", tool="test_tool", parameters={"k": "v"})
        entries = a.get_entries()
        assert len(entries) == 1
        assert entries[0]["action"] == "test_action"
        assert entries[0]["tool"] == "test_tool"

    def test_log_blocked(self, tmp_path):
        a = AuditLogger(log_dir=str(tmp_path / "audit"))
        a.log(action="blocked_action", allowed=False, reason="dangerous")
        entries = a.get_entries()
        assert entries[0]["allowed"] is False
        assert entries[0]["reason"] == "dangerous"

    def test_query_by_action(self, tmp_path):
        a = AuditLogger(log_dir=str(tmp_path / "audit"))
        a.log(action="action_a")
        a.log(action="action_b")
        a.log(action="action_a")
        results = a.query(action="action_a")
        assert len(results) == 2

    def test_query_by_tool(self, tmp_path):
        a = AuditLogger(log_dir=str(tmp_path / "audit"))
        a.log(action="call", tool="files")
        a.log(action="call", tool="run_command")
        a.log(action="call", tool="files")
        results = a.query(tool="files")
        assert len(results) == 2

    def test_query_by_since(self, tmp_path):
        a = AuditLogger(log_dir=str(tmp_path / "audit"))
        a.log(action="old_action")
        from datetime import timedelta
        future = datetime.now(tz=timezone.utc) + timedelta(hours=1)
        results = a.query(since=future)
        assert len(results) == 0

    def test_session_summary(self, tmp_path):
        a = AuditLogger(log_dir=str(tmp_path / "audit"))
        a.log(action="tool_call", tool="files", allowed=True)
        a.log(action="tool_call_blocked", tool="run_command", allowed=False)
        a.log(action="tool_call", tool="files", allowed=True, result="Error: something")
        summary = a.session_summary()
        assert summary["total_entries"] == 3
        assert summary["action_counts"]["tool_call"] == 2
        assert summary["tool_counts"]["files"] == 2
        assert summary["blocked_count"] == 1
        assert summary["error_count"] == 1

    def test_clear(self, tmp_path):
        a = AuditLogger(log_dir=str(tmp_path / "audit"))
        a.log(action="test")
        a.clear()
        assert len(a.get_entries()) == 0

    def test_result_summary_truncation(self, tmp_path):
        a = AuditLogger(log_dir=str(tmp_path / "audit"))
        long_result = "x" * 1000
        a.log(action="test", result=long_result)
        entries = a.get_entries()
        assert len(entries[0]["result_summary"]) <= 500

    def test_query_no_filters(self, tmp_path):
        a = AuditLogger(log_dir=str(tmp_path / "audit"))
        a.log(action="a1")
        a.log(action="a2")
        results = a.query()
        assert len(results) == 2
