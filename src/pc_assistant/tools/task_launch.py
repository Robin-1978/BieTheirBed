"""Agent-facing Task launch parsing and serialization."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from pc_assistant.automation import ScheduleKind, ScheduleSpec, next_fire_at
from pc_assistant.tasks import TaskLaunchKind, TaskLaunchPolicy


class LaunchPolicyError(ValueError):
    """A launch policy cannot be represented by the public Agent contract."""


@dataclass(frozen=True)
class ResolvedTaskLaunch:
    policy: TaskLaunchPolicy
    next_fire_at: float | None


def _timestamp(raw: Any, *, field: str) -> float:
    text = str(raw or "").strip()
    if not text:
        raise LaunchPolicyError(f"launch.{field} is required")
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise LaunchPolicyError(
            f"launch.{field} must be an RFC 3339 timestamp with a timezone offset"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LaunchPolicyError(
            f"launch.{field} must include a timezone offset, for example +08:00"
        )
    return parsed.timestamp()


def _reject_unexpected(
    launch: dict[str, Any],
    *,
    allowed: frozenset[str],
) -> None:
    unexpected = sorted(set(launch) - allowed)
    if unexpected:
        raise LaunchPolicyError(
            f"launch.kind does not accept these fields: {', '.join(unexpected)}"
        )


def _validation_message(exc: TypeError | ValueError) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        details = errors()
        if details:
            message = str(details[0].get("msg", "")).strip()
            if message.startswith("Value error, "):
                return message.removeprefix("Value error, ")
            if message:
                return message
    return str(exc)


async def resolve_task_launch(raw: Any) -> ResolvedTaskLaunch:
    if not isinstance(raw, dict):
        raise LaunchPolicyError("launch must be an object with an explicit kind")
    launch = raw
    kind = str(launch.get("kind", "")).strip()
    now = time.time()
    if kind == "immediate":
        _reject_unexpected(launch, allowed=frozenset({"kind"}))
        return ResolvedTaskLaunch(
            policy=TaskLaunchPolicy(kind=TaskLaunchKind.IMMEDIATE),
            next_fire_at=None,
        )

    try:
        if kind == "one_time":
            _reject_unexpected(launch, allowed=frozenset({"kind", "at"}))
            spec = ScheduleSpec(
                kind=ScheduleKind.ONE_TIME,
                run_at=_timestamp(launch.get("at"), field="at"),
            )
        elif kind == "interval":
            _reject_unexpected(
                launch,
                allowed=frozenset({"kind", "at", "interval_seconds"}),
            )
            if launch.get("interval_seconds") is None:
                raise LaunchPolicyError(
                    "launch.interval_seconds is required for interval"
                )
            interval = float(launch["interval_seconds"])
            spec = ScheduleSpec(
                kind=ScheduleKind.INTERVAL,
                run_at=(
                    _timestamp(launch.get("at"), field="at")
                    if launch.get("at") is not None
                    else now + interval
                ),
                interval_seconds=interval,
            )
        elif kind == "cron":
            _reject_unexpected(
                launch,
                allowed=frozenset({"kind", "cron", "timezone"}),
            )
            expression = str(launch.get("cron", "")).strip()
            if len(expression.split()) != 5:
                raise LaunchPolicyError(
                    "launch.cron must contain exactly five fields "
                    "(minute hour day month weekday), with no seconds. "
                    "For every day at 18:30, use '30 18 * * *'."
                )
            spec = ScheduleSpec(
                kind=ScheduleKind.CRON,
                cron_expression=expression,
                timezone=str(launch.get("timezone", "Asia/Shanghai")).strip(),
            )
        else:
            raise LaunchPolicyError(
                "launch.kind must be one of: immediate, one_time, interval, cron"
            )
        due = await asyncio.to_thread(next_fire_at, spec, after=now)
    except LaunchPolicyError:
        raise
    except (TypeError, ValueError) as exc:
        raise LaunchPolicyError(
            f"Invalid launch policy: {_validation_message(exc)}"
        ) from exc
    if due is None:
        raise LaunchPolicyError("Invalid launch policy: no future occurrence exists")
    return ResolvedTaskLaunch(
        policy=TaskLaunchPolicy(
            kind=TaskLaunchKind.SCHEDULED,
            schedule_type=spec.kind.value,
            run_at=spec.run_at,
            interval_seconds=spec.interval_seconds,
            cron=spec.cron_expression,
            timezone=spec.timezone,
        ),
        next_fire_at=due,
    )


def timestamp_text(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def public_launch(policy: TaskLaunchPolicy) -> dict[str, Any]:
    if policy.kind is TaskLaunchKind.IMMEDIATE:
        return {"kind": "immediate"}
    if policy.kind is TaskLaunchKind.EVENT:
        return {
            "kind": "event",
            "event_source": policy.event_source,
        }
    if policy.schedule_type == "one_time":
        return {
            "kind": "one_time",
            "at": timestamp_text(policy.run_at),
        }
    if policy.schedule_type == "interval":
        return {
            "kind": "interval",
            "interval_seconds": policy.interval_seconds,
            "at": timestamp_text(policy.run_at),
        }
    return {
        "kind": "cron",
        "cron": policy.cron,
        "timezone": policy.timezone,
    }


def launch_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "description": "The explicit policy that starts Task executions.",
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["immediate", "one_time", "interval", "cron"],
                "description": (
                    "immediate starts now; one_time runs once at launch.at; "
                    "interval repeats every launch.interval_seconds; cron uses "
                    "launch.cron and launch.timezone."
                ),
            },
            "at": {
                "type": "string",
                "format": "date-time",
                "description": (
                    "RFC 3339 timestamp with a timezone offset. Required for one_time "
                    "and optional as the first interval run. If omitted for interval, "
                    "the first run occurs after one full interval."
                ),
            },
            "interval_seconds": {
                "type": "number",
                "minimum": 60,
                "maximum": 31536000,
                "description": "Repeat interval in seconds. Required for interval.",
            },
            "cron": {
                "type": "string",
                "maxLength": 128,
                "description": (
                    "Standard five-field Cron: minute hour day month weekday. "
                    "Required for cron. Do not include seconds."
                ),
            },
            "timezone": {
                "type": "string",
                "default": "Asia/Shanghai",
                "description": "IANA timezone for cron, for example Asia/Shanghai.",
            },
        },
        "required": ["kind"],
        "additionalProperties": False,
    }
