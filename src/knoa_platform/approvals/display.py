"""Stable, channel-neutral approval presentation data."""

from __future__ import annotations

import json
from typing import Any

_SENSITIVE = ("token", "secret", "password", "credential", "authorization", "api_key")


def _redacted(value: Any, *, key: str = "") -> Any:
    lowered = key.lower()
    if any(part in lowered for part in _SENSITIVE):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(k): _redacted(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redacted(item, key=key) for item in value[:20]]
    if isinstance(value, str) and len(value) > 500:
        return value[:497] + "..."
    return value


def approval_display(
    tool_name: str,
    arguments: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    effect, _, risk = reason.partition(":")
    effect = effect.strip() or "unknown"
    risk = risk.split(";", 1)[0].strip() or "unknown"
    safe_arguments = _redacted(arguments)
    encoded = json.dumps(safe_arguments, ensure_ascii=False, sort_keys=True)
    if len(encoded) > 1600:
        encoded = encoded[:1597] + "..."
    return {
        "tool_name": tool_name,
        "effect": effect,
        "risk": risk,
        "arguments_preview": encoded,
        "reversible": effect in {"read_only", "internal_write"},
    }
