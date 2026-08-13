"""Deterministic recurrence calculation without daemon or Channel concerns."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from knoa_platform.automation.models import ScheduleKind, ScheduleSpec


@dataclass(frozen=True)
class _CronField:
    values: frozenset[int]
    unrestricted: bool


def _cron_field(raw: str, minimum: int, maximum: int, *, sunday: bool = False) -> _CronField:
    text = raw.strip()
    if not text:
        raise ValueError("Cron field must not be empty")
    values: set[int] = set()
    unrestricted = text == "*"
    for item in text.split(","):
        base, separator, step_text = item.partition("/")
        if separator:
            try:
                step = int(step_text)
            except ValueError as exc:
                raise ValueError("Cron step must be an integer") from exc
            if step <= 0:
                raise ValueError("Cron step must be positive")
        else:
            step = 1
        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            start_text, end_text = base.split("-", 1)
            try:
                start, end = int(start_text), int(end_text)
            except ValueError as exc:
                raise ValueError("Cron range must contain integers") from exc
        else:
            try:
                start = end = int(base)
            except ValueError as exc:
                raise ValueError("Cron value must be an integer") from exc
        allowed_maximum = 7 if sunday and maximum == 6 else maximum
        if start < minimum or end > allowed_maximum or start > end:
            raise ValueError("Cron value is outside its allowed range")
        for value in range(start, end + 1, step):
            values.add(0 if sunday and value == 7 else value)
    return _CronField(frozenset(values), unrestricted)


def _parse_cron(expression: str) -> tuple[_CronField, ...]:
    parts = expression.split()
    if len(parts) != 5:
        raise ValueError("Cron expression must contain five fields")
    return (
        _cron_field(parts[0], 0, 59),
        _cron_field(parts[1], 0, 23),
        _cron_field(parts[2], 1, 31),
        _cron_field(parts[3], 1, 12),
        _cron_field(parts[4], 0, 6, sunday=True),
    )


def _cron_matches(local: datetime, fields: tuple[_CronField, ...]) -> bool:
    minute, hour, day, month, weekday = fields
    cron_weekday = (local.weekday() + 1) % 7
    day_matches = local.day in day.values
    weekday_matches = cron_weekday in weekday.values
    if day.unrestricted and weekday.unrestricted:
        calendar_matches = True
    elif day.unrestricted:
        calendar_matches = weekday_matches
    elif weekday.unrestricted:
        calendar_matches = day_matches
    else:
        calendar_matches = day_matches or weekday_matches
    return (
        local.minute in minute.values
        and local.hour in hour.values
        and local.month in month.values
        and calendar_matches
    )


def next_fire_at(spec: ScheduleSpec, *, after: float) -> float | None:
    """Return the first occurrence strictly after ``after``."""

    if not math.isfinite(after) or after < 0:
        raise ValueError("Schedule cursor must be a finite non-negative timestamp")
    if spec.kind is ScheduleKind.ONE_TIME:
        assert spec.run_at is not None
        return spec.run_at if spec.run_at > after else None
    if spec.kind is ScheduleKind.INTERVAL:
        assert spec.run_at is not None and spec.interval_seconds is not None
        if spec.run_at > after:
            return spec.run_at
        elapsed = after - spec.run_at
        ordinal = math.floor(elapsed / spec.interval_seconds) + 1
        return spec.run_at + ordinal * spec.interval_seconds

    fields = _parse_cron(spec.cron_expression)
    timezone = ZoneInfo(spec.timezone)
    cursor = datetime.fromtimestamp(after, UTC).replace(second=0, microsecond=0)
    cursor += timedelta(minutes=1)
    for _ in range(366 * 24 * 60):
        if _cron_matches(cursor.astimezone(timezone), fields):
            return cursor.timestamp()
        cursor += timedelta(minutes=1)
    raise ValueError("Cron expression has no occurrence within one year")
