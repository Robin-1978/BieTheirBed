"""Public, versioned Secure Gateway HTTP protocol models."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from knoa_platform.agent_runtime.contracts import (
    ArtifactTranscriptionResult,
    MCPResourceCatalogResult,
    RuntimeStatus,
    ToolListResult,
)
from knoa_platform.artifacts import ArtifactRef
from knoa_platform.service.core_api import (
    ArtifactInputRef,
    ChatApprovalSnapshot,
    ChatTurnSnapshot,
    ConversationSessionSnapshot,
    HumanInteractionSnapshot,
    ProductTaskExecutionSnapshot,
    ProductTaskSnapshot,
    TaskSnapshot,
)
from knoa_platform.tasks import (
    ApprovalState,
    TaskDefinitionState,
    TaskEvent,
    TaskLaunchPolicy,
    TaskState,
)


class GatewayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CreateSessionRequest(GatewayRequest):
    agent_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$")


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
    input: str = Field(default="", max_length=200_000)
    attachments: tuple[ArtifactInputRef, ...] = Field(default=(), max_length=8)
    tools_enabled: bool = True
    priority: int = Field(default=0, ge=0, le=9)
    parent_task_id: str = Field(default="", max_length=128)
    agent_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$")

    def require_content(self) -> None:
        if not self.input.strip() and not self.attachments:
            raise ValueError("Task request requires input or an attachment")


class CreateProductTaskRequest(GatewayRequest):
    client_request_id: str = Field(min_length=1, max_length=128)
    title: str = Field(default="", max_length=200)
    goal: str = Field(min_length=1, max_length=200_000)
    attachments: tuple[ArtifactInputRef, ...] = Field(default=(), max_length=8)
    tools_enabled: bool = True
    priority: int = Field(default=0, ge=0, le=9)
    launch_policy: TaskLaunchPolicy = Field(default_factory=TaskLaunchPolicy)
    notification_policy: dict[str, bool] = Field(default_factory=dict)
    agent_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$")


class UpdateProductTaskRequest(GatewayRequest):
    title: str | None = Field(default=None, max_length=200)
    goal: str | None = Field(default=None, min_length=1, max_length=200_000)
    attachments: tuple[ArtifactInputRef, ...] | None = Field(default=None, max_length=8)
    tools_enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=9)
    launch_policy: TaskLaunchPolicy | None = None
    notification_policy: dict[str, bool] | None = None
    expected_revision: int | None = Field(default=None, ge=1)


class ContinueProductTaskRequest(GatewayRequest):
    client_request_id: str = Field(min_length=1, max_length=128)
    input: str = Field(default="", max_length=200_000)
    attachments: tuple[ArtifactInputRef, ...] = Field(default=(), max_length=8)

    def require_content(self) -> None:
        if not self.input.strip() and not self.attachments:
            raise ValueError("Task follow-up requires input or an attachment")


class CreateChatTurnRequest(GatewayRequest):
    client_request_id: str = Field(min_length=1, max_length=128)
    input: str = Field(default="", max_length=200_000)
    attachments: tuple[ArtifactInputRef, ...] = Field(default=(), max_length=8)
    tools_enabled: bool = True
    agent_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$")

    def require_content(self) -> None:
        if not self.input.strip() and not self.attachments:
            raise ValueError("ChatTurn request requires input or an attachment")


class UpdateConversationSessionRequest(GatewayRequest):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    state: Literal["active", "archived"] | None = None
    expected_revision: int | None = Field(default=None, ge=1)


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


class ResolveHumanInteractionRequest(GatewayRequest):
    value: Any


class GatewayQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskListQuery(GatewayQuery):
    session_handle: str = Field(default="", max_length=256)
    state: TaskState | None = None
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str = Field(default="", max_length=512)


class ProductTaskListQuery(GatewayQuery):
    state: TaskDefinitionState | None = None
    include_archived: bool = False
    limit: int = Field(default=100, ge=1, le=200)


class ConversationSessionListQuery(GatewayQuery):
    include_archived: bool = False
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str = Field(default="", max_length=512)


class TaskExecutionListQuery(GatewayQuery):
    limit: int = Field(default=100, ge=1, le=200)


class EventQuery(GatewayQuery):
    after_id: int = Field(default=0, ge=0, le=9_223_372_036_854_775_807)


class TaskEventQuery(GatewayQuery):
    after_seq: int = Field(default=0, ge=0)


class ChatTurnListQuery(GatewayQuery):
    limit: int = Field(default=100, ge=1, le=500)
    cursor: str = Field(default="", max_length=512)


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
    message: str = ""
    retryable: bool = False
    correlation_id: str = ""


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


class AgentSummary(BaseModel):
    agent_id: str
    display_name: str


class AgentListResponse(BaseModel):
    default_agent: str
    agents: tuple[AgentSummary, ...]


class ConversationSessionResponse(BaseModel):
    session: ConversationSessionSnapshot


class ConversationSessionListResponse(BaseModel):
    sessions: tuple[ConversationSessionSnapshot, ...]
    next_cursor: str = ""


class TaskAcceptedResponse(BaseModel):
    task_id: str
    state: TaskState


class ChatTurnResponse(BaseModel):
    turn: ChatTurnSnapshot


class ChatTurnListResponse(BaseModel):
    turns: tuple[ChatTurnSnapshot, ...]
    next_cursor: str = ""


class ChatApprovalResolvedResponse(BaseModel):
    approval: ChatApprovalSnapshot
    resolved: bool


class HumanInteractionResolvedResponse(BaseModel):
    interaction: HumanInteractionSnapshot
    resolved: bool


class TaskCommandResponse(BaseModel):
    accepted: bool
    state: TaskState


class TaskResponse(BaseModel):
    task: TaskSnapshot


class TaskListResponse(BaseModel):
    tasks: tuple[TaskSnapshot, ...]
    next_cursor: str = ""


class TaskEventListResponse(BaseModel):
    events: tuple[TaskEvent, ...]


class ProductTaskResponse(BaseModel):
    task: ProductTaskSnapshot
    execution: ProductTaskExecutionSnapshot | None = None


class ProductTaskListResponse(BaseModel):
    tasks: tuple[ProductTaskSnapshot, ...]


class ProductTaskExecutionResponse(BaseModel):
    execution: ProductTaskExecutionSnapshot


class ProductTaskExecutionListResponse(BaseModel):
    executions: tuple[ProductTaskExecutionSnapshot, ...]


class DeletedResponse(BaseModel):
    deleted: bool = True


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


class MCPResourceCatalogResponse(BaseModel):
    result: MCPResourceCatalogResult


class AuditEventResponse(BaseModel):
    event_id: int
    event_type: str
    occurred_at: float
    remote_address_hash: str
    detail_code: str


class AuditListResponse(BaseModel):
    events: tuple[AuditEventResponse, ...]


class DeviceRevokedResponse(BaseModel):
    revoked: bool


class AndroidReleaseResponse(BaseModel):
    platform: Literal["android"] = "android"
    channel: Literal["personal"] = "personal"
    version_name: str = Field(min_length=1, max_length=32)
    version_code: int = Field(ge=1, le=2_100_000_000)
    min_supported_version_code: int = Field(ge=1, le=2_100_000_000)
    size_bytes: int = Field(ge=1, le=1024 * 1024 * 1024)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    published_at: float = Field(gt=0)
    release_notes: str = Field(default="", max_length=20_000)
    download_path: str = Field(min_length=1, max_length=256)
