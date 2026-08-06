"""Metadata-safe redaction shared by audit and persistence boundaries."""
from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEY = re.compile(r"(?:api[_-]?key|token|password|passwd|secret|authorization|cookie)", re.I)
_DATA_URL = re.compile(r"data:image/[^;\s]+;base64,[A-Za-z0-9+/=]+")
_SENSITIVE_COMMAND = re.compile(
    r"\b(passwd|chpasswd|usermod|useradd|userdel|groupmod|groupadd|groupdel|"
    r"loginctl\s+(?:unlock-session|unlock-sessions|lock-session|lock-sessions))\b",
    re.IGNORECASE,
)
_SENSITIVE_TEXT = re.compile(
    r"(?i)\b(?:password|passwd|passcode|验证码|api[_ -]?key|token|secret)\b\s*[:=：]\s*\S+"
)


def redact(value: Any) -> Any:
    if isinstance(value, str):
        text = _DATA_URL.sub("[binary image omitted]", value)
        return _SENSITIVE_TEXT.sub("[sensitive value omitted]", text)
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if _SENSITIVE_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    return value


def redact_message(text: str) -> str:
    """Redact secrets from free-form channel and observability text."""
    if not isinstance(text, str):
        return str(text)
    if text.strip().lower().startswith("/unlock"):
        return "/unlock [REDACTED]"
    return str(redact(text))


def redact_tool_parameters(tool: str | None, parameters: Any) -> Any:
    """Redact secrets whose field name is not itself sensitive.

    GUI keyboard text and shell authentication commands historically used
    ordinary ``text``/``command`` keys, so key-based redaction alone was not
    sufficient at the audit and confirmation boundaries.
    """
    safe = redact(parameters)
    if not isinstance(safe, dict):
        return safe
    if tool == "type_text" and "text" in safe:
        safe["text"] = "[redacted keyboard text]"
    # Keep redaction effective for persisted audit entries written before the
    # single-purpose tool split; this is storage hygiene, not a model-facing
    # tool alias.
    if tool == "keyboard" and str(safe.get("action", "")).lower() in ("type", "write"):
        if "text" in safe:
            safe["text"] = "[redacted keyboard text]"
    if tool in ("run_command", "shell") and _SENSITIVE_COMMAND.search(str(safe.get("command", ""))):
        safe["command"] = "[redacted sensitive command]"
    return safe
