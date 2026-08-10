"""Typed domain records for durable Tasks."""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from pc_assistant.agent_runtime.contracts import ArtifactAttachment
from pc_assistant.artifacts import ArtifactRef


NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Identifier = Annotated[NonEmpty, StringConstraints(max_length=128)]


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
    CHAT = "chat"
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
    "artifact",
    "context_compacted",
    "warning",
    "final_output",
    "completed",
    "failed",
    "cancelled",
]


class TaskEventPayload(TaskModel):
    content: str = ""
    previous_state: TaskState | None = None
    state: TaskState | None = None
    phase: Annotated[str, StringConstraints(max_length=256)] = ""
    reason: Annotated[str, StringConstraints(max_length=2000)] = ""
    approval_id: Annotated[str, StringConstraints(max_length=128)] = ""
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
    client_request_id: Identifier
    origin: TaskOrigin = TaskOrigin.CHAT
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
