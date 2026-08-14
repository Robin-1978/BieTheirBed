"""Typed domain records for durable Tasks."""
from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from knoa_platform.agent_runtime.contracts import ArtifactAttachment
from knoa_platform.artifacts import ArtifactRef
from knoa_platform.interactions import HumanInteraction

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Identifier = Annotated[NonEmpty, StringConstraints(max_length=128)]
_MCP_EVENT_SOURCE = re.compile(r"^mcp:[A-Za-z][A-Za-z0-9_-]{0,23}$")


class TaskModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class TaskState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskOrigin(str, Enum):
    USER = "user"
    AGENT = "agent"
    SCHEDULED = "scheduled"
    EVENT = "event"


class ApprovalState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class TaskAttemptState(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class TaskToolStepState(str, Enum):
    COMMITTING = "committing"
    COMPLETED = "completed"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


TERMINAL_TASK_STATES = frozenset(
    {
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.CANCELLED,
    }
)


TaskEventType = Literal[
    "task_created",
    "state_changed",
    "reasoning_delta",
    "content_delta",
    "plan",
    "tool_call",
    "tool_result",
    "approval_requested",
    "approval_resolved",
    "interaction_requested",
    "interaction_resolved",
    "artifact",
    "context_compacted",
    "warning",
    "final_output",
    "completed",
    "failed",
    "cancelled",
]


TaskTraceEntryType = Literal[
    "reasoning",
    "content",
    "plan",
    "tool_call",
    "tool_result",
    "artifact",
    "context_compacted",
    "warning",
    "final_output",
]


class TaskEventPayload(TaskModel):
    content: str = ""
    previous_state: TaskState | None = None
    state: TaskState | None = None
    phase: Annotated[str, StringConstraints(max_length=256)] = ""
    reason: Annotated[str, StringConstraints(max_length=2000)] = ""
    approval_id: Annotated[str, StringConstraints(max_length=128)] = ""
    interaction_id: Annotated[str, StringConstraints(max_length=128)] = ""
    interaction_kind: Annotated[str, StringConstraints(max_length=64)] = ""
    interaction_display: dict[str, Any] = Field(default_factory=dict)
    interaction_schema: dict[str, Any] = Field(default_factory=dict)
    tool_step_id: Annotated[str, StringConstraints(max_length=128)] = ""
    tool_call_id: Annotated[str, StringConstraints(max_length=256)] = ""
    tool_name: Annotated[str, StringConstraints(max_length=256)] = ""
    tool_args: dict[str, Any] = Field(default_factory=dict)
    tool_result: Any = None
    artifact: ArtifactRef | None = None
    artifact_id: Annotated[str, StringConstraints(max_length=128)] = ""
    blocked: bool = False
    iteration: int = Field(default=0, ge=0)


class TaskRecord(TaskModel):
    task_id: Identifier
    principal_id: Annotated[NonEmpty, StringConstraints(max_length=256)]
    session_handle: Annotated[NonEmpty, StringConstraints(max_length=256)]
    agent_id: Identifier
    client_request_id: Identifier
    origin: TaskOrigin = TaskOrigin.USER
    parent_task_id: Annotated[str, StringConstraints(max_length=128)] = ""
    goal: Annotated[str, StringConstraints(min_length=1, max_length=200_000)]
    attachments: tuple[ArtifactAttachment, ...] = Field(default=(), max_length=8)
    tools_enabled: bool = True
    priority: int = Field(default=0, ge=0, le=9)
    state: TaskState
    phase: Annotated[str, StringConstraints(max_length=256)] = ""
    attempt_count: int = Field(default=0, ge=0)
    cancel_requested: bool = False
    final_summary: Annotated[str, StringConstraints(max_length=200_000)] = ""
    failure_code: Annotated[str, StringConstraints(max_length=256)] = ""
    lease_owner: Annotated[str, StringConstraints(max_length=128)] = ""
    lease_expires_at: float | None = None
    created_at: float
    updated_at: float
    started_at: float | None = None
    finished_at: float | None = None
    next_event_seq: int = Field(gt=0)
    revision: int = Field(ge=0)


class TaskEvent(TaskModel):
    task_id: Identifier
    event_seq: int = Field(gt=0)
    event_type: TaskEventType
    payload: TaskEventPayload = Field(default_factory=TaskEventPayload)
    occurred_at: float = Field(ge=0.0)


class PrincipalTaskEvent(TaskModel):
    feed_event_id: int = Field(gt=0)
    principal_id: Annotated[NonEmpty, StringConstraints(max_length=256)]
    event: TaskEvent


class TaskTraceEntry(TaskModel):
    entry_type: TaskTraceEntryType
    iteration: int = Field(default=0, ge=0)
    content: str = ""
    tool_call_id: Annotated[str, StringConstraints(max_length=256)] = ""
    tool_name: Annotated[str, StringConstraints(max_length=256)] = ""
    tool_args: dict[str, Any] = Field(default_factory=dict)
    tool_result: Any = None
    artifact: ArtifactRef | None = None
    occurred_at: float = Field(ge=0.0)


class TaskExecutionTrace(TaskModel):
    task_id: Identifier
    entries: tuple[TaskTraceEntry, ...] = ()
    final_output: Annotated[str, StringConstraints(max_length=200_000)] = ""
    created_at: float = Field(ge=0.0)
    updated_at: float = Field(ge=0.0)
    retained_until: float = Field(ge=0.0)
    compacted_at: float | None = Field(default=None, ge=0.0)
    revision: int = Field(ge=0)


class TaskApprovalRecord(TaskModel):
    approval_id: Identifier
    task_id: Identifier
    principal_id: Annotated[NonEmpty, StringConstraints(max_length=256)]
    tool_step_id: Identifier
    tool_call_id: Annotated[NonEmpty, StringConstraints(max_length=256)]
    tool_name: Annotated[NonEmpty, StringConstraints(max_length=256)]
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: Annotated[str, StringConstraints(max_length=2000)] = ""
    state: ApprovalState
    request_event_seq: int = Field(gt=0)
    created_at: float
    resolved_at: float | None = None
    expires_at: float | None = None
    resolved_by: Annotated[str, StringConstraints(max_length=256)] = ""


class TaskAttemptRecord(TaskModel):
    attempt_id: Identifier
    task_id: Identifier
    ordinal: int = Field(gt=0)
    state: TaskAttemptState
    started_at: float = Field(ge=0.0)
    finished_at: float | None = Field(default=None, ge=0.0)
    failure_code: Annotated[str, StringConstraints(max_length=256)] = ""


class TaskToolStepRecord(TaskModel):
    tool_step_id: Identifier
    task_id: Identifier
    principal_id: Annotated[NonEmpty, StringConstraints(max_length=256)]
    tool_call_id: Annotated[NonEmpty, StringConstraints(max_length=256)]
    tool_name: Annotated[NonEmpty, StringConstraints(max_length=256)]
    arguments: dict[str, Any] = Field(default_factory=dict)
    effect: Literal[
        "read_only",
        "internal_write",
        "local_write",
        "external_side_effect",
        "desktop_control",
        "unknown",
    ]
    risk: Literal["low", "medium", "high"]
    state: TaskToolStepState
    result: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(ge=0.0)
    updated_at: float = Field(ge=0.0)


class TaskCancelResult(TaskModel):
    accepted: bool
    state: TaskState | None = None


class TaskPauseResult(TaskModel):
    accepted: bool
    state: TaskState


class TaskDefinitionState(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class TaskLaunchKind(str, Enum):
    IMMEDIATE = "immediate"
    SCHEDULED = "scheduled"
    EVENT = "event"


class TaskLaunchReason(str, Enum):
    CREATED = "created"
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    EVENT = "event"
    RERUN = "rerun"
    FOLLOW_UP = "follow_up"


class TaskLaunchPolicy(TaskModel):
    kind: TaskLaunchKind = TaskLaunchKind.IMMEDIATE
    schedule_type: Literal["one_time", "interval", "cron"] | None = None
    run_at: float | None = Field(default=None, ge=0.0)
    interval_seconds: float | None = Field(default=None, gt=0.0)
    cron: Annotated[str, StringConstraints(max_length=256)] = ""
    timezone: Annotated[str, StringConstraints(max_length=128)] = "Asia/Shanghai"
    event_source: Annotated[str, StringConstraints(max_length=128)] = ""
    source_config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_kind_fields(self) -> TaskLaunchPolicy:
        if self.kind is TaskLaunchKind.IMMEDIATE:
            if self.schedule_type is not None or self.run_at is not None or self.interval_seconds is not None or self.cron or self.event_source or self.source_config:
                raise ValueError("Immediate launch policy cannot contain schedule or event fields")
        elif self.kind is TaskLaunchKind.SCHEDULED:
            if self.schedule_type is None:
                raise ValueError("Scheduled launch policy requires schedule_type")
            if self.event_source or self.source_config:
                raise ValueError("Scheduled launch policy cannot contain event fields")
            if self.schedule_type == "one_time" and self.run_at is None:
                raise ValueError("One-time launch policy requires run_at")
            if self.schedule_type == "interval" and self.interval_seconds is None:
                raise ValueError("Interval launch policy requires interval_seconds")
            if self.schedule_type == "cron" and not self.cron.strip():
                raise ValueError("Cron launch policy requires cron")
        else:
            if not self.event_source.strip():
                raise ValueError("Event launch policy requires event_source")
            if self.schedule_type is not None or self.run_at is not None or self.interval_seconds is not None or self.cron:
                raise ValueError("Event launch policy cannot contain schedule fields")
            if self.event_source.startswith("mcp:"):
                if not _MCP_EVENT_SOURCE.fullmatch(self.event_source):
                    raise ValueError("MCP event_source must be mcp:<server_id>")
                exact = self.source_config.get("resource_uri")
                prefix = self.source_config.get("resource_uri_prefix")
                if (exact is None) == (prefix is None):
                    raise ValueError(
                        "MCP event requires exactly one resource_uri or "
                        "resource_uri_prefix"
                    )
                resource_uri = exact if exact is not None else prefix
                if not isinstance(resource_uri, str) or not resource_uri.strip():
                    raise ValueError("MCP Resource URI must be a non-empty string")
                parsed = urlsplit(resource_uri.strip())
                if not parsed.scheme or parsed.query or parsed.fragment:
                    raise ValueError("MCP Resource URI must be an absolute URI without query or fragment")
        return self


class TaskDefinitionRecord(TaskModel):
    task_id: Identifier
    principal_id: Annotated[NonEmpty, StringConstraints(max_length=256)]
    session_handle: Annotated[NonEmpty, StringConstraints(max_length=256)]
    agent_id: Identifier
    title: Annotated[NonEmpty, StringConstraints(max_length=200)]
    goal: Annotated[str, StringConstraints(min_length=1, max_length=200_000)]
    attachments: tuple[ArtifactAttachment, ...] = Field(default=(), max_length=8)
    tools_enabled: bool = True
    priority: int = Field(default=0, ge=0, le=9)
    launch_policy: TaskLaunchPolicy = Field(default_factory=TaskLaunchPolicy)
    notification_policy: dict[str, bool] = Field(default_factory=dict)
    state: TaskDefinitionState = TaskDefinitionState.ACTIVE
    revision: int = Field(ge=1)
    latest_execution_id: Annotated[str, StringConstraints(max_length=128)] = ""
    execution_count: int = Field(default=0, ge=0)
    created_at: float = Field(ge=0.0)
    updated_at: float = Field(ge=0.0)


class TaskExecutionRecord(TaskModel):
    execution_id: Identifier
    task_id: Identifier
    agent_id_snapshot: Identifier
    task_revision: int = Field(ge=1)
    launch_reason: TaskLaunchReason
    goal_snapshot: Annotated[str, StringConstraints(min_length=1, max_length=200_000)]
    attachment_snapshots: tuple[ArtifactAttachment, ...] = Field(default=(), max_length=8)
    policy_snapshot: TaskLaunchPolicy
    state: TaskState
    phase: Annotated[str, StringConstraints(max_length=256)] = ""
    cancel_requested: bool = False
    final_result: Annotated[str, StringConstraints(max_length=200_000)] = ""
    failure_code: Annotated[str, StringConstraints(max_length=256)] = ""
    created_at: float = Field(ge=0.0)
    updated_at: float = Field(ge=0.0)
    started_at: float | None = Field(default=None, ge=0.0)
    finished_at: float | None = Field(default=None, ge=0.0)
    trace: TaskExecutionTrace | None = None
    approvals: tuple[TaskApprovalRecord, ...] = ()
    interactions: tuple[HumanInteraction, ...] = ()
