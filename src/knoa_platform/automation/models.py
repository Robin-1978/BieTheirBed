"""Typed forward-only contracts for durable automation schedules."""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


class ScheduleKind(str, Enum):
    ONE_TIME = "one_time"
    INTERVAL = "interval"
    CRON = "cron"


class ScheduleState(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class OccurrenceState(str, Enum):
    CLAIMED = "claimed"
    RETRY_WAIT = "retry_wait"
    TASK_CREATED = "task_created"
    DEAD = "dead"


class TriggerState(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"


class TriggerEventState(str, Enum):
    BASELINED = "baselined"
    RECEIVED = "received"
    CLAIMED = "claimed"
    RETRY_WAIT = "retry_wait"
    TASK_CREATED = "task_created"
    DEAD = "dead"


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


class ScheduleRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schedule_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    principal_id: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    session_handle: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    client_request_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    goal: Annotated[str, StringConstraints(min_length=1, max_length=200_000)]
    spec: ScheduleSpec
    tools_enabled: bool = True
    priority: int = Field(default=0, ge=0, le=9)
    state: ScheduleState
    next_fire_at: float | None = Field(default=None, ge=0.0)
    last_fire_at: float | None = Field(default=None, ge=0.0)
    fire_count: int = Field(default=0, ge=0)
    created_at: float = Field(ge=0.0)
    updated_at: float = Field(ge=0.0)


class ScheduleOccurrenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    occurrence_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    schedule_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    principal_id: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    session_handle: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    ordinal: int = Field(gt=0)
    scheduled_for: float = Field(ge=0.0)
    state: OccurrenceState
    attempt_count: int = Field(ge=0)
    next_attempt_at: float | None = Field(default=None, ge=0.0)
    lease_owner: Annotated[str, StringConstraints(max_length=128)] = ""
    lease_expires_at: float | None = Field(default=None, ge=0.0)
    task_id: Annotated[str, StringConstraints(max_length=128)] = ""
    failure_code: Annotated[str, StringConstraints(max_length=256)] = ""
    created_at: float = Field(ge=0.0)
    updated_at: float = Field(ge=0.0)


class TriggerRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    trigger_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    principal_id: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    session_handle: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    client_request_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    name: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    goal: Annotated[str, StringConstraints(min_length=1, max_length=64_000)]
    tools_enabled: bool = True
    priority: int = Field(default=0, ge=0, le=9)
    state: TriggerState
    event_count: int = Field(default=0, ge=0)
    last_event_at: float | None = Field(default=None, ge=0.0)
    created_at: float = Field(ge=0.0)
    updated_at: float = Field(ge=0.0)


class TriggerEventRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    trigger_event_id: Annotated[
        str, StringConstraints(min_length=1, max_length=128)
    ]
    trigger_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    principal_id: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    session_handle: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    external_event_id: Annotated[
        str, StringConstraints(min_length=1, max_length=256)
    ]
    payload: dict[str, Any] = Field(default_factory=dict)
    state: TriggerEventState
    attempt_count: int = Field(ge=0)
    next_attempt_at: float | None = Field(default=None, ge=0.0)
    lease_owner: Annotated[str, StringConstraints(max_length=128)] = ""
    lease_expires_at: float | None = Field(default=None, ge=0.0)
    task_id: Annotated[str, StringConstraints(max_length=128)] = ""
    failure_code: Annotated[str, StringConstraints(max_length=256)] = ""
    received_at: float = Field(ge=0.0)
    updated_at: float = Field(ge=0.0)
