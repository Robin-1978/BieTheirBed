"""Public, versioned Secure Gateway HTTP protocol models."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from pc_assistant.agent_runtime.contracts import RuntimeStatus, ToolListResult
from pc_assistant.agent_runtime.contracts import ArtifactTranscriptionResult
from pc_assistant.artifacts import ArtifactRef
from pc_assistant.service.core_api import ArtifactInputRef, TaskSnapshot
from pc_assistant.tasks import ApprovalState, TaskState


class GatewayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PairChallengeRequest(GatewayRequest):
    grant_id: str = Field(min_length=1, max_length=128)


class PairCompleteRequest(PairChallengeRequest):
    grant_secret: str = Field(min_length=32, max_length=256)
    challenge_id: str = Field(min_length=1, max_length=128)
    nonce: str = Field(min_length=32, max_length=256)
    display_name: str = Field(min_length=1, max_length=80)
    public_key: str = Field(min_length=40, max_length=64)
    signature: str = Field(min_length=80, max_length=128)


class AuthChallengeRequest(GatewayRequest):
    device_id: str = Field(min_length=1, max_length=128)


class AuthCompleteRequest(AuthChallengeRequest):
    challenge_id: str = Field(min_length=1, max_length=128)
    nonce: str = Field(min_length=32, max_length=256)
    signature: str = Field(min_length=80, max_length=128)


class CreateTaskRequest(GatewayRequest):
    session_handle: str = Field(min_length=1, max_length=256)
    input: str = Field(default="", max_length=200_000)
    attachments: tuple[ArtifactInputRef, ...] = Field(default=(), max_length=8)
    tools_enabled: bool = True
    priority: int = Field(default=0, ge=0, le=9)
    parent_task_id: str = Field(default="", max_length=128)

    def require_content(self) -> None:
        if not self.input.strip() and not self.attachments:
            raise ValueError("Task request requires input or an attachment")


class CancelTaskRequest(GatewayRequest):
    reason: str = Field(default="", max_length=1000)


class PauseTaskRequest(GatewayRequest):
    reason: str = Field(default="", max_length=1000)


class ResumeTaskRequest(PauseTaskRequest):
    acknowledge_outcome_unknown: bool = False


class RetryTaskRequest(PauseTaskRequest):
    pass


class ResolveApprovalRequest(GatewayRequest):
    approved: bool


class GatewayQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskListQuery(GatewayQuery):
    session_handle: str = Field(default="", max_length=256)
    state: TaskState | None = None
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str = Field(default="", max_length=512)


class EventQuery(GatewayQuery):
    after_id: int = Field(default=0, ge=0, le=9_223_372_036_854_775_807)


class ArtifactUploadQuery(GatewayQuery):
    session_handle: str = Field(min_length=1, max_length=256)
    name: str = Field(default="", max_length=160)
    caption: str = Field(default="", max_length=1000)


class ArtifactDownloadQuery(GatewayQuery):
    session_handle: str = Field(min_length=1, max_length=256)


class RuntimeQuery(GatewayQuery):
    session_handle: str = Field(min_length=1, max_length=256)


class AuditQuery(GatewayQuery):
    after_id: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=200)


class ErrorResponse(BaseModel):
    error: str


class HealthResponse(BaseModel):
    status: str
    scope: str


class ChallengeResponse(BaseModel):
    challenge_id: str
    nonce: str
    expires_at: float


class PairCompleteResponse(BaseModel):
    device_id: str
    principal_id: str


class AuthCompleteResponse(BaseModel):
    token: str
    expires_at: float
    device_id: str


class SessionResponse(BaseModel):
    session_id: str
    device_id: str
    principal_id: str
    expires_at: float


class SessionCreatedResponse(BaseModel):
    session_handle: str


class TaskAcceptedResponse(BaseModel):
    task_id: str
    state: TaskState


class TaskCommandResponse(BaseModel):
    accepted: bool
    state: TaskState


class TaskResponse(BaseModel):
    task: TaskSnapshot


class TaskListResponse(BaseModel):
    tasks: tuple[TaskSnapshot, ...]
    next_cursor: str = ""


class ApprovalResolvedResponse(BaseModel):
    approval_id: str
    resolved: bool
    state: ApprovalState


class ArtifactResponse(BaseModel):
    artifact: ArtifactRef


class ArtifactTranscriptionResponse(BaseModel):
    result: ArtifactTranscriptionResult


class RuntimeStatusResponse(BaseModel):
    result: RuntimeStatus


class ToolListResponse(BaseModel):
    result: ToolListResult


class AuditEventResponse(BaseModel):
    event_id: int
    event_type: str
    occurred_at: float
    remote_address_hash: str
    detail_code: str


class AuditListResponse(BaseModel):
    events: tuple[AuditEventResponse, ...]
