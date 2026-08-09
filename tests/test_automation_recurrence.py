from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pc_assistant.automation import ScheduleKind, ScheduleSpec, next_fire_at


def _timestamp(value: str) -> float:
    return datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp()


def test_one_time_schedule_fires_once() -> None:
    spec = ScheduleSpec(kind=ScheduleKind.ONE_TIME, run_at=100.0)

    assert next_fire_at(spec, after=99.0) == 100.0
    assert next_fire_at(spec, after=100.0) is None


def test_interval_schedule_uses_stable_anchor_without_drift() -> None:
    spec = ScheduleSpec(
        kind=ScheduleKind.INTERVAL,
        run_at=100.0,
        interval_seconds=60.0,
    )

    assert next_fire_at(spec, after=100.0) == 160.0
    assert next_fire_at(spec, after=221.0) == 280.0


def test_cron_schedule_uses_explicit_timezone() -> None:
    spec = ScheduleSpec(
        kind=ScheduleKind.CRON,
        cron_expression="0 9 * * 1-5",
        timezone="Asia/Shanghai",
    )

    result = next_fire_at(spec, after=_timestamp("2026-08-09T00:00:00"))

    assert result == _timestamp("2026-08-10T01:00:00")


def test_cron_day_and_weekday_follow_standard_or_semantics() -> None:
    spec = ScheduleSpec(
        kind=ScheduleKind.CRON,
        cron_expression="0 0 15 * 1",
        timezone="UTC",
    )

    result = next_fire_at(spec, after=_timestamp("2026-08-09T00:00:00"))

    assert result == _timestamp("2026-08-10T00:00:00")


@pytest.mark.parametrize(
    "expression",
    ("* * * *", "60 * * * *", "*/0 * * * *", "* * * * nope"),
)
def test_invalid_cron_is_rejected_when_calculated(expression: str) -> None:
    spec = ScheduleSpec(
        kind=ScheduleKind.CRON,
        cron_expression=expression,
        timezone="UTC",
    )

    with pytest.raises(ValueError):
        next_fire_at(spec, after=1.0)


def test_schedule_kind_rejects_mixed_fields() -> None:
    with pytest.raises(ValidationError):
        ScheduleSpec(
            kind=ScheduleKind.ONE_TIME,
            run_at=100.0,
            interval_seconds=60.0,
        )
