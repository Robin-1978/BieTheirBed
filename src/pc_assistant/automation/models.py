"""Typed forward-only contracts for durable automation schedules."""
from __future__ import annotations

from enum import Enum
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


class ScheduleKind(str, Enum):
    ONE_TIME = "one_time"
    INTERVAL = "interval"
    CRON = "cron"


class ScheduleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: ScheduleKind
    run_at: float | None = Field(default=None, ge=0.0)
    interval_seconds: float | None = Field(default=None, ge=60.0, le=31_536_000.0)
    cron_expression: Annotated[str, StringConstraints(max_length=128)] = ""
    timezone: Annotated[str, StringConstraints(min_length=1, max_length=128)] = (
        "Asia/Shanghai"
    )

    @model_validator(mode="after")
    def validate_kind_fields(self) -> ScheduleSpec:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Schedule timezone is unknown") from exc
        if self.kind is ScheduleKind.ONE_TIME:
            if self.run_at is None or self.interval_seconds is not None:
                raise ValueError("One-time schedule requires only run_at")
            if self.cron_expression:
                raise ValueError("One-time schedule cannot contain Cron")
        elif self.kind is ScheduleKind.INTERVAL:
            if self.run_at is None or self.interval_seconds is None:
                raise ValueError("Interval schedule requires run_at and interval_seconds")
            if self.cron_expression:
                raise ValueError("Interval schedule cannot contain Cron")
        elif self.run_at is not None or self.interval_seconds is not None:
            raise ValueError("Cron schedule accepts only cron_expression")
        elif not self.cron_expression.strip():
            raise ValueError("Cron schedule requires cron_expression")
        return self
