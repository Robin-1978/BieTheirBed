"""Stable, channel-neutral approval presentation data."""

from __future__ import annotations

import json
import re
from typing import Any

_SENSITIVE = ("token", "secret", "password", "credential", "authorization", "api_key")
_TARGET_KEYS = (
    "issue_key",
    "job_id",
    "pipeline_id",
    "project_id",
    "path",
    "file_path",
    "url",
    "assignee",
    "user_id",
    "branch",
    "name",
    "id",
    "cmd",
    "command",
)
_REVIEW = re.compile(
    r"reviewer\[(?P<reviewer>[^\]/]+)(?:/(?P<model>[^\]]*))?\]="
    r"(?P<decision>approve|deny|escalate):\s*(?P<reason>.*?)"
    r"(?:;\s*rules=|$)",
    re.IGNORECASE,
)


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
    *,
    human_instruction: str = "",
) -> dict[str, Any]:
    effect, _, risk = reason.partition(":")
    effect = effect.strip() or "unknown"
    risk = risk.split(";", 1)[0].strip() or "unknown"
    safe_arguments = _redacted(arguments)
    encoded = json.dumps(safe_arguments, ensure_ascii=False, sort_keys=True)
    if len(encoded) > 1600:
        encoded = encoded[:1597] + "..."
    review = _REVIEW.search(reason)
    reviewer_decision = review.group("decision").lower() if review else ""
    reviewer_reason = review.group("reason").strip() if review else ""
    manual_reason = (
        "reviewer_escalated"
        if reviewer_decision == "escalate"
        else "high_risk"
        if risk == "high"
        else "reviewer_suggest_only"
        if reviewer_decision
        else "policy_confirmation"
    )
    return {
        "tool_name": tool_name,
        "effect": effect,
        "risk": risk,
        "arguments_preview": encoded,
        "reversible": effect in {"read_only", "internal_write"},
        "action_summary": _action_summary(tool_name),
        "target_summary": _target_summary(safe_arguments),
        "instruction_excerpt": _excerpt(human_instruction, 500),
        "reviewer_decision": reviewer_decision,
        "reviewer_reason": _excerpt(reviewer_reason, 500),
        "reviewer_id": review.group("reviewer").strip() if review else "",
        "reviewer_model": review.group("model").strip() if review else "",
        "manual_reason": manual_reason,
    }


def _action_summary(tool_name: str) -> str:
    normalized = tool_name.strip()
    if not normalized:
        return ""
    parts = normalized.split(".")
    action = parts[-1].replace("_", " ").replace("-", " ").strip()
    namespace = " · ".join(part.replace("_", " ") for part in parts[:-1])
    return f"{namespace} · {action}" if namespace else action


def _target_summary(arguments: Any) -> str:
    if not isinstance(arguments, dict):
        return ""
    lowered = {str(key).lower(): (str(key), value) for key, value in arguments.items()}
    for candidate in _TARGET_KEYS:
        item = lowered.get(candidate)
        if item is None:
            continue
        key, value = item
        if value in (None, "", [], {}):
            continue
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True) if not isinstance(value, str) else value
        return f"{key.replace('_', ' ')}: {_excerpt(rendered, 240)}"
    return ""


def _excerpt(value: str, limit: int) -> str:
    compact = " ".join(value.strip().split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."
