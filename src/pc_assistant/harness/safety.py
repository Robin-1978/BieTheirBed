from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pc_assistant.platform_ import get_default_dangerous_commands, get_default_protected_paths


class SafetyCheckResult:
    def __init__(self, allowed: bool, reason: str = "") -> None:
        self.allowed = allowed
        self.reason = reason

    def __bool__(self) -> bool:
        return self.allowed


_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r";\s*(rm|del|format|shutdown|reboot|mkfs)\b", re.IGNORECASE),
    re.compile(r"\|\s*(rm|del|format|shutdown|reboot)\b", re.IGNORECASE),
    re.compile(r"`[^`]*(rm|del|format|shutdown)[^`]*`", re.IGNORECASE),
    re.compile(r"\$\([^)]*(rm|del|format|shutdown)[^)]*\)", re.IGNORECASE),
    re.compile(r"&&\s*(rm|del|format|shutdown|reboot|mkfs)\b", re.IGNORECASE),
]

_CONFIRMATION_COMMAND_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\b", re.IGNORECASE),
    re.compile(r"\bdelete\b", re.IGNORECASE),
    re.compile(r"\bremove-item\b", re.IGNORECASE),
    re.compile(r"\brmdir\b", re.IGNORECASE),
    re.compile(r"\brd\s", re.IGNORECASE),
    re.compile(r"\bdel\b", re.IGNORECASE),
    re.compile(r"\bkill\b", re.IGNORECASE),
    re.compile(r"\btaskkill\b", re.IGNORECASE),
    re.compile(r"\bstop-process\b", re.IGNORECASE),
    re.compile(r"\bmove\b", re.IGNORECASE),
    re.compile(r"\bmv\b", re.IGNORECASE),
    re.compile(r"\bren\b", re.IGNORECASE),
    re.compile(r"\brename\b", re.IGNORECASE),
    re.compile(r"\bchmod\b", re.IGNORECASE),
    re.compile(r"\bchown\b", re.IGNORECASE),
    re.compile(r"\bsudo\b", re.IGNORECASE),
    re.compile(r"\bdd\b", re.IGNORECASE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"\bshred\b", re.IGNORECASE),
]


class SafetyChecker:
    def __init__(
        self,
        dangerous_commands: list[str] | None = None,
        protected_paths: list[str] | None = None,
        working_directory: str | None = None,
    ) -> None:
        base_patterns = get_default_dangerous_commands()
        custom_patterns = [c.lower() for c in (dangerous_commands or [])]
        self._dangerous_commands = base_patterns + custom_patterns
        self._protected_paths = [Path(p).resolve() for p in (protected_paths or get_default_protected_paths())]
        self._working_directory = Path(working_directory).resolve() if working_directory else None

    def check_command(self, command: str) -> SafetyCheckResult:
        cmd_lower = command.lower().strip()
        for dangerous in self._dangerous_commands:
            if dangerous.lower() in cmd_lower:
                return SafetyCheckResult(False, f"Blocked dangerous command pattern: {dangerous}")
        for pattern in _INJECTION_PATTERNS:
            if pattern.search(command):
                return SafetyCheckResult(False, f"Blocked potential command injection: {pattern.pattern}")
        return SafetyCheckResult(True)

    def check_path(self, path: str, write: bool = False) -> SafetyCheckResult:
        try:
            resolved = Path(path).resolve()
        except (OSError, ValueError):
            return SafetyCheckResult(False, f"Invalid path: {path}")
        for protected in self._protected_paths:
            try:
                resolved.relative_to(protected)
                return SafetyCheckResult(
                    False, f"Access denied: path is inside protected directory {protected}"
                )
            except ValueError:
                pass
        return SafetyCheckResult(True)

    def is_blocked(self, tool_name: str, kwargs: dict[str, Any]) -> SafetyCheckResult:
        if tool_name == "shell":
            command = kwargs.get("command", "")
            return self.check_command(command)
        if tool_name == "filesystem":
            path = kwargs.get("path", "")
            action = kwargs.get("action", "")
            result = self.check_path(path, write=True)
            if not result:
                return result
            if action == "delete":
                return self.check_path(path, write=True)
            return SafetyCheckResult(True)
        if tool_name in ("application", "system", "process", "task"):
            action = kwargs.get("action", "")
            if action == "kill":
                return SafetyCheckResult(False, "Blocked: killing processes requires confirmation")
            return SafetyCheckResult(True)
        return SafetyCheckResult(True)

    def needs_confirmation(self, tool_name: str, kwargs: dict[str, Any]) -> tuple[bool, str]:
        if tool_name == "shell":
            command = kwargs.get("command", "")
            cmd_lower = command.lower().strip()
            for pattern in _CONFIRMATION_COMMAND_PATTERNS:
                if pattern.search(cmd_lower):
                    return (True, f"Command may be destructive: {command}")
            return (False, "")

        if tool_name == "filesystem":
            action = kwargs.get("action", "")
            path = kwargs.get("path", "")
            if action in ("delete", "move", "write"):
                return (True, f"Filesystem {action} operation on {path} requires confirmation")
            if self._working_directory:
                try:
                    resolved = Path(path).resolve()
                    resolved.relative_to(self._working_directory)
                except (ValueError, OSError):
                    return (True, f"Path {path} is outside working directory {self._working_directory}")
            return (False, "")

        if tool_name in ("application", "system", "process", "task"):
            action = kwargs.get("action", "")
            if action == "kill":
                return (True, "Killing a process requires confirmation")
            return (False, "")

        if tool_name == "memory":
            action = kwargs.get("action", "")
            if action in ("clear", "delete", "forget"):
                return (True, f"Memory {action} requires confirmation")
            return (False, "")

        if tool_name == "scheduler":
            action = kwargs.get("action", "")
            if action in ("create", "delete", "clear"):
                return (True, f"Scheduler {action} requires confirmation")
            return (False, "")

        return (False, "")

    def check_tool_call(self, tool_name: str, kwargs: dict[str, Any]) -> SafetyCheckResult:
        return self.is_blocked(tool_name, kwargs)
