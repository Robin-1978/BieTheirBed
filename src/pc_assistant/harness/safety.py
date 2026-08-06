from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pc_assistant.platform_ import get_default_dangerous_commands, get_default_protected_paths


class SafetyCheckResult:
    def __init__(self, allowed: bool, reason: str = "", *, overridable: bool = True) -> None:
        self.allowed = allowed
        self.reason = reason
        self.overridable = overridable

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
    # Authentication, account, session, and security-policy changes always
    # require an explicit user confirmation.  These are intentionally
    # confirmation-gated rather than model-overridable.
    re.compile(r"\b(passwd|chpasswd|usermod|useradd|userdel|groupmod|groupadd|groupdel)\b", re.IGNORECASE),
    re.compile(r"\bloginctl\s+(unlock-session|unlock-sessions|lock-session|lock-sessions)\b", re.IGNORECASE),
    re.compile(r"\b(gsettings|dconf)\b.*(?:screensaver|lock|session-lock)", re.IGNORECASE),
    re.compile(r"\b(db(u|us)-send|gdbus)\b.*(?:ScreenSaver|screensaver|unlock|Lock)", re.IGNORECASE),
    re.compile(r"\b(systemctl)\b.*(?:display-manager|gdm|lightdm|sddm|remote-desktop)", re.IGNORECASE),
]

_SENSITIVE_COMMAND_PATTERNS: tuple[re.Pattern[str], ...] = (
    _CONFIRMATION_COMMAND_PATTERNS[-5],
    _CONFIRMATION_COMMAND_PATTERNS[-4],
    _CONFIRMATION_COMMAND_PATTERNS[-3],
    _CONFIRMATION_COMMAND_PATTERNS[-2],
    _CONFIRMATION_COMMAND_PATTERNS[-1],
)


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

    def _resolve_tool_path(self, path: str) -> Path:
        candidate = Path(path).expanduser()
        if candidate.is_absolute() or self._working_directory is None:
            return candidate.resolve()
        return (self._working_directory / candidate).resolve()

    def check_command(self, command: str) -> SafetyCheckResult:
        cmd_lower = command.lower().strip()
        for dangerous in self._dangerous_commands:
            if dangerous.lower() in cmd_lower:
                return SafetyCheckResult(
                    False,
                    f"Blocked dangerous command pattern: {dangerous}",
                    overridable=False,
                )
        for pattern in _INJECTION_PATTERNS:
            if pattern.search(command):
                return SafetyCheckResult(
                    False,
                    f"Blocked potential command injection: {pattern.pattern}",
                    overridable=False,
                )
        return SafetyCheckResult(True)

    def check_path(self, path: str, write: bool = False) -> SafetyCheckResult:
        try:
            resolved = self._resolve_tool_path(path)
        except (OSError, ValueError):
            return SafetyCheckResult(False, f"Invalid path: {path}")
        for protected in self._protected_paths:
            try:
                resolved.relative_to(protected)
                return SafetyCheckResult(
                    False,
                    f"Access denied: path is inside protected directory {protected}",
                    overridable=False,
                )
            except ValueError:
                pass
        return SafetyCheckResult(True)

    def is_blocked(self, tool_name: str, kwargs: dict[str, Any]) -> SafetyCheckResult:
        if tool_name == "run_command":
            command = kwargs.get("command", "")
            return self.check_command(command)
        if tool_name == "write_file":
            path = kwargs.get("path", "")
            return self.check_path(path, write=True)
        if tool_name == "attach":
            path = str(kwargs.get("path", ""))
            if not path:
                return SafetyCheckResult(False, "Artifact path is required")
            return self.check_path(path)
        return SafetyCheckResult(True)

    def needs_confirmation(self, tool_name: str, kwargs: dict[str, Any]) -> tuple[bool, str]:
        if tool_name == "run_command":
            command = kwargs.get("command", "")
            cmd_lower = command.lower().strip()
            for pattern in _CONFIRMATION_COMMAND_PATTERNS:
                if pattern.search(cmd_lower):
                    if any(sensitive.search(cmd_lower) for sensitive in _SENSITIVE_COMMAND_PATTERNS):
                        return (True, "Authentication, account, session, or security-policy command requires confirmation")
                    return (True, f"Command may be destructive: {command}")
            return (False, "")

        if tool_name == "type_text":
            return (True, "Keyboard text input requires explicit confirmation")

        if tool_name == "hotkey":
            return (True, "Keyboard shortcuts require explicit confirmation")

        if tool_name == "press_key":
            key = str(kwargs.get("key", "")).lower()
            if key in {
                "enter", "return", "delete", "backspace", "esc", "escape",
                "space", "tab",
            }:
                return (True, f"Keyboard key '{key}' may execute or change state")
            return (False, "")

        if tool_name == "mouse":
            action = str(kwargs.get("action", "")).lower()
            if action in {
                "click", "double_click", "right_click", "drag", "press", "release",
            }:
                return (True, f"Mouse {action} may activate or change desktop state")
            return (False, "")

        if tool_name == "ui":
            action = str(kwargs.get("action", "")).lower()
            if action in ("click", "type"):
                return (True, f"Semantic UI {action} requires explicit confirmation")
            return (False, "")

        if tool_name == "windows":
            if str(kwargs.get("action", "")).lower() == "close":
                return (True, "Closing a window requires explicit confirmation")
            return (False, "")

        if tool_name == "write_file":
            path = kwargs.get("path", "")
            return (True, f"Filesystem write operation on {path} requires confirmation")

        if tool_name == "attach":
            path = str(kwargs.get("path", ""))
            if self._working_directory:
                try:
                    self._resolve_tool_path(path).relative_to(self._working_directory)
                except (ValueError, OSError):
                    return (
                        True,
                        f"Delivering {path} from outside working directory "
                        f"{self._working_directory} requires confirmation",
                    )
            return (False, "")

        if tool_name == "memory":
            action = kwargs.get("action", "")
            if action in ("clear", "delete", "forget"):
                return (True, f"Memory {action} requires confirmation")
            if action == "store" and kwargs.get("importance") == "core":
                key = kwargs.get("key", "")
                return (
                    True,
                    f"Storing '{key}' as always-injected core memory requires confirmation",
                )
            return (False, "")

        if tool_name == "schedule":
            action = kwargs.get("action", "")
            if action in ("create", "delete", "clear"):
                return (True, f"Scheduler {action} requires confirmation")
            return (False, "")

        return (False, "")

    def check_tool_call(self, tool_name: str, kwargs: dict[str, Any]) -> SafetyCheckResult:
        return self.is_blocked(tool_name, kwargs)
