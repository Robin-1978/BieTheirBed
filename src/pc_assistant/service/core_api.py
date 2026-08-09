"""Strict, versioned Core API v1 wire contracts.

These models are the new public protocol. They intentionally do not accept the
legacy ``method + params: dict`` message shape.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    model_validator,
)

from pc_assistant.agent_runtime.contracts import (
    ArtifactDownloadResult,
    ConfigSetResult,
    HealthStatus,
    HistoryResult,
    MemoryClearResult,
    MemoryListResult,
    RuntimeStatus,
    ToolListResult,
)
from pc_assistant.artifacts import ArtifactRef
from pc_assistant.automation import (
    ScheduleRecord,
    ScheduleSpec,
    ScheduleState,
)
from pc_assistant.tasks import (
    ApprovalState,
    TaskCancelResult,
    TaskEvent,
    TaskPauseResult,
    TaskRecord,
    TaskState,
)


NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
RequestId = Annotated[NonEmpty, StringConstraints(max_length=128)]
SessionHandle = Annotated[NonEmpty, StringConstraints(max_length=256)]
TaskId = Annotated[NonEmpty, StringConstraints(max_length=128)]
CORE_WS_MAX_SIZE = 70 * 1024 * 1024


class CoreModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ArtifactInputRef(CoreModel):
    artifact_id: Annotated[NonEmpty, StringConstraints(max_length=128)]
    caption: Annotated[str, StringConstraints(max_length=1000)] = ""


class TaskSnapshot(CoreModel):
    task_id: TaskId
    session_handle: SessionHandle
    client_request_id: RequestId
    parent_task_id: Annotated[str, StringConstraints(max_length=128)] = ""
    goal: Annotated[str, StringConstraints(max_length=200_000)]
    attachments: tuple[ArtifactInputRef, ...] = Field(default=(), max_length=8)
    tools_enabled: bool
    priority: int = Field(ge=0, le=9)
    state: TaskState
    phase: Annotated[str, StringConstraints(max_length=256)] = ""
    attempt_count: int = Field(ge=0)
    cancel_requested: bool
    final_summary: Annotated[str, StringConstraints(max_length=200_000)] = ""
    failure_code: Annotated[str, StringConstraints(max_length=256)] = ""
    created_at: float = Field(ge=0.0)
    updated_at: float = Field(ge=0.0)
    started_at: float | None = Field(default=None, ge=0.0)
    finished_at: float | None = Field(default=None, ge=0.0)
    next_event_seq: int = Field(gt=0)

    @classmethod
    def from_record(cls, task: TaskRecord) -> TaskSnapshot:
        return cls(
            task_id=task.task_id,
            session_handle=task.session_handle,
            client_request_id=task.client_request_id,
            parent_task_id=task.parent_task_id,
            goal=task.goal,
            attachments=tuple(
                ArtifactInputRef(
                    artifact_id=attachment.artifact_id,
                    caption=attachment.caption,
                )
                for attachment in task.attachments
            ),
            tools_enabled=task.tools_enabled,
            priority=task.priority,
            state=task.state,
            phase=task.phase,
            attempt_count=task.attempt_count,
            cancel_requested=task.cancel_requested,
            final_summary=task.final_summary,
            failure_code=task.failure_code,
            created_at=task.created_at,
            updated_at=task.updated_at,
            started_at=task.started_at,
            finished_at=task.finished_at,
            next_event_seq=task.next_event_seq,
        )


class ScheduleSnapshot(CoreModel):
    schedule_id: Annotated[NonEmpty, StringConstraints(max_length=128)]
    session_handle: SessionHandle
    goal: Annotated[str, StringConstraints(min_length=1, max_length=200_000)]
    spec: ScheduleSpec
    tools_enabled: bool
    priority: int = Field(ge=0, le=9)
    state: ScheduleState
    next_fire_at: float | None = Field(default=None, ge=0.0)
    last_fire_at: float | None = Field(default=None, ge=0.0)
    fire_count: int = Field(ge=0)
    created_at: float = Field(ge=0.0)
    updated_at: float = Field(ge=0.0)

    @classmethod
    def from_record(cls, schedule: ScheduleRecord) -> ScheduleSnapshot:
        return cls(
            schedule_id=schedule.schedule_id,
            session_handle=schedule.session_handle,
            goal=schedule.goal,
            spec=schedule.spec,
            tools_enabled=schedule.tools_enabled,
            priority=schedule.priority,
            state=schedule.state,
            next_fire_at=schedule.next_fire_at,
            last_fire_at=schedule.last_fire_at,
            fire_count=schedule.fire_count,
            created_at=schedule.created_at,
            updated_at=schedule.updated_at,
        )


class AuthenticateRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["authenticate"] = "authenticate"
    credential: Annotated[NonEmpty, StringConstraints(max_length=4096)]


class HealthRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["health"] = "health"


class CreateSessionRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["create_session"] = "create_session"


class CreateTaskRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["create_task"] = "create_task"
    session_handle: SessionHandle
    input: Annotated[str, StringConstraints(max_length=200_000)] = ""
    attachments: tuple[ArtifactInputRef, ...] = Field(default=(), max_length=8)
    tools_enabled: bool = True
    priority: int = Field(default=0, ge=0, le=9)
    parent_task_id: Annotated[str, StringConstraints(max_length=128)] = ""

    @model_validator(mode="after")
    def require_input_or_attachment(self) -> CreateTaskRequest:
        if not self.input.strip() and not self.attachments:
            raise ValueError("Task request requires input or an attachment")
        return self


class SubscribeTaskRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["subscribe_task"] = "subscribe_task"
    task_id: TaskId
    after_seq: int = Field(default=0, ge=0)


class GetTaskRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["get_task"] = "get_task"
    task_id: TaskId


class ListTasksRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["list_tasks"] = "list_tasks"
    session_handle: Annotated[str, StringConstraints(max_length=256)] = ""
    state: TaskState | None = None
    limit: int = Field(default=50, ge=1, le=100)
    cursor: Annotated[str, StringConstraints(max_length=512)] = ""


class CancelTaskRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["cancel_task"] = "cancel_task"
    task_id: TaskId
    reason: Annotated[str, StringConstraints(max_length=1000)] = ""


class PauseTaskRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["pause_task"] = "pause_task"
    task_id: TaskId
    reason: Annotated[str, StringConstraints(max_length=1000)] = ""


class ResumeTaskRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["resume_task"] = "resume_task"
    task_id: TaskId
    reason: Annotated[str, StringConstraints(max_length=1000)] = ""
    acknowledge_outcome_unknown: bool = False


class ResolveApprovalRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["approval_resolve"] = "approval_resolve"
    approval_id: Annotated[NonEmpty, StringConstraints(max_length=128)]
    approved: bool


class CreateScheduleRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["create_schedule"] = "create_schedule"
    session_handle: SessionHandle
    goal: Annotated[str, StringConstraints(min_length=1, max_length=200_000)]
    spec: ScheduleSpec
    tools_enabled: bool = True
    priority: int = Field(default=0, ge=0, le=9)


class GetScheduleRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["get_schedule"] = "get_schedule"
    schedule_id: Annotated[NonEmpty, StringConstraints(max_length=128)]


class ListSchedulesRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["list_schedules"] = "list_schedules"
    state: ScheduleState | None = None
    limit: int = Field(default=50, ge=1, le=100)


class SessionRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    session_handle: SessionHandle


class GetStatusRequest(SessionRequest):
    method: Literal["status"] = "status"


class GetHistoryRequest(SessionRequest):
    method: Literal["history"] = "history"


class ListMemoryRequest(SessionRequest):
    method: Literal["memory_list"] = "memory_list"


class ClearMemoryRequest(SessionRequest):
    method: Literal["memory_clear"] = "memory_clear"


class ListToolsRequest(SessionRequest):
    method: Literal["tools"] = "tools"


class SetConfigRequest(SessionRequest):
    method: Literal["config_set"] = "config_set"
    field_name: Annotated[
        str,
        StringConstraints(strip_whitespace=True, pattern=r"^[a-z][a-z0-9_]{0,63}$"),
    ]
    value: bool | int | float | str


class UploadArtifactRequest(SessionRequest):
    method: Literal["artifact_upload"] = "artifact_upload"
    data_url: Annotated[str, StringConstraints(min_length=1, max_length=64 * 1024 * 1024)]
    media_type: Annotated[str, StringConstraints(min_length=1, max_length=128)] = "image/jpeg"
    caption: Annotated[str, StringConstraints(max_length=1000)] = ""


class DownloadArtifactRequest(SessionRequest):
    method: Literal["artifact_download"] = "artifact_download"
    artifact_id: Annotated[NonEmpty, StringConstraints(max_length=128)]


CoreRequest: TypeAlias = Annotated[
    AuthenticateRequest
    | HealthRequest
    | CreateSessionRequest
    | CreateTaskRequest
    | SubscribeTaskRequest
    | GetTaskRequest
    | ListTasksRequest
    | CancelTaskRequest
    | PauseTaskRequest
    | ResumeTaskRequest
    | ResolveApprovalRequest
    | CreateScheduleRequest
    | GetScheduleRequest
    | ListSchedulesRequest
    | GetStatusRequest
    | GetHistoryRequest
    | ListMemoryRequest
    | ClearMemoryRequest
    | ListToolsRequest
    | SetConfigRequest
    | UploadArtifactRequest
    | DownloadArtifactRequest,
    Field(discriminator="method"),
]
_CORE_REQUEST_ADAPTER = TypeAdapter(CoreRequest)


def parse_core_request_json(raw: str | bytes) -> CoreRequest:
    """Parse one strict Core API v1 request."""
    return _CORE_REQUEST_ADAPTER.validate_json(raw)


ErrorCode = Literal[
    "invalid_request",
    "resource_exhausted",
    "unauthenticated",
    "session_not_found",
    "artifact_not_found",
    "artifact_too_large",
    "task_not_found",
    "schedule_not_found",
    "capability_denied",
    "confirmation_denied",
    "tool_invalid_arguments",
    "tool_failed",
    "provider_failed",
    "cancelled",
    "connection_lost",
    "internal_error",
]


class CoreError(CoreModel):
    message_type: Literal["error"] = "error"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    code: ErrorCode
    message: Annotated[str, StringConstraints(max_length=2000)]
    correlation_id: Annotated[NonEmpty, StringConstraints(max_length=128)]


class AuthenticatedMessage(CoreModel):
    message_type: Literal["authenticated"] = "authenticated"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId


class SessionCreatedMessage(CoreModel):
    message_type: Literal["session_created"] = "session_created"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    session_handle: SessionHandle


class TaskAcceptedMessage(CoreModel):
    message_type: Literal["task_accepted"] = "task_accepted"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    task_id: TaskId
    state: TaskState


class TaskSubscribedMessage(CoreModel):
    message_type: Literal["task_subscribed"] = "task_subscribed"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    task_id: TaskId
    after_seq: int = Field(ge=0)


class TaskSnapshotMessage(CoreModel):
    message_type: Literal["task_snapshot"] = "task_snapshot"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    task: TaskSnapshot


class TaskListMessage(CoreModel):
    message_type: Literal["task_list"] = "task_list"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    tasks: tuple[TaskSnapshot, ...]
    next_cursor: Annotated[str, StringConstraints(max_length=512)] = ""


class TaskEventMessage(CoreModel):
    message_type: Literal["task_event"] = "task_event"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    event: TaskEvent


class HealthMessage(CoreModel):
    message_type: Literal["health"] = "health"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    result: HealthStatus


class StatusMessage(CoreModel):
    message_type: Literal["status"] = "status"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    result: RuntimeStatus


class HistoryMessage(CoreModel):
    message_type: Literal["history"] = "history"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    result: HistoryResult


class MemoryListMessage(CoreModel):
    message_type: Literal["memory_list"] = "memory_list"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    result: MemoryListResult


class MemoryClearedMessage(CoreModel):
    message_type: Literal["memory_cleared"] = "memory_cleared"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    result: MemoryClearResult


class ToolsMessage(CoreModel):
    message_type: Literal["tools"] = "tools"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    result: ToolListResult


class ConfigSetMessage(CoreModel):
    message_type: Literal["config_set"] = "config_set"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    result: ConfigSetResult


class ArtifactUploadedMessage(CoreModel):
    message_type: Literal["artifact_uploaded"] = "artifact_uploaded"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    result: ArtifactRef


class ArtifactDownloadedMessage(CoreModel):
    message_type: Literal["artifact_downloaded"] = "artifact_downloaded"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    result: ArtifactDownloadResult


class TaskCancelResultMessage(CoreModel):
    message_type: Literal["task_cancel_result"] = "task_cancel_result"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    result: TaskCancelResult


class TaskPauseResultMessage(CoreModel):
    message_type: Literal["task_pause_result"] = "task_pause_result"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    result: TaskPauseResult


class TaskResumedMessage(CoreModel):
    message_type: Literal["task_resumed"] = "task_resumed"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    task_id: TaskId
    state: TaskState


class ApprovalResolvedMessage(CoreModel):
    message_type: Literal["approval_resolved"] = "approval_resolved"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    approval_id: Annotated[NonEmpty, StringConstraints(max_length=128)]
    resolved: bool
    state: ApprovalState


class ScheduleAcceptedMessage(CoreModel):
    message_type: Literal["schedule_accepted"] = "schedule_accepted"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    schedule: ScheduleSnapshot


class ScheduleSnapshotMessage(CoreModel):
    message_type: Literal["schedule_snapshot"] = "schedule_snapshot"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    schedule: ScheduleSnapshot


class ScheduleListMessage(CoreModel):
    message_type: Literal["schedule_list"] = "schedule_list"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    schedules: tuple[ScheduleSnapshot, ...]


CoreServerMessage: TypeAlias = Annotated[
    AuthenticatedMessage
    | SessionCreatedMessage
    | TaskAcceptedMessage
    | TaskSubscribedMessage
    | TaskSnapshotMessage
    | TaskListMessage
    | TaskEventMessage
    | HealthMessage
    | StatusMessage
    | HistoryMessage
    | MemoryListMessage
    | MemoryClearedMessage
    | ToolsMessage
    | ConfigSetMessage
    | ArtifactUploadedMessage
    | ArtifactDownloadedMessage
    | TaskCancelResultMessage
    | TaskPauseResultMessage
    | TaskResumedMessage
    | ApprovalResolvedMessage
    | ScheduleAcceptedMessage
    | ScheduleSnapshotMessage
    | ScheduleListMessage
    | CoreError,
    Field(discriminator="message_type"),
]
_CORE_SERVER_MESSAGE_ADAPTER = TypeAdapter(CoreServerMessage)


def parse_core_server_message_json(raw: str | bytes) -> CoreServerMessage:
    return _CORE_SERVER_MESSAGE_ADAPTER.validate_json(raw)


def core_request_schema() -> dict[str, Any]:
    """Expose the canonical JSON Schema for protocol/tooling generation."""
    return _CORE_REQUEST_ADAPTER.json_schema()
