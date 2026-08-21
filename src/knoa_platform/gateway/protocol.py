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
from knoa_platform.agent_runtime.model_step import ProviderCallRequest, ProviderChunk
from knoa_platform.agents.definitions import ResolvedInvocationPolicy
from knoa_platform.artifacts import ArtifactRef
from knoa_platform.configuration import (
    ConfigControlState,
    ConfigDraft,
    ConfigPublishResult,
    ConfigRevision,
    ConfigValidationResult,
    ManagedConfig,
)
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
    PrincipalTaskEvent,
    TaskDefinitionState,
    TaskEvent,
    TaskLaunchPolicy,
    TaskState,
)


class GatewayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CreateSessionRequest(GatewayRequest):
    agent_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$")


class P2POfferRequest(GatewayRequest):
    type: Literal["offer"]
    sdp: str = Field(min_length=1, max_length=2_000_000)


class ResourceP2POfferRequest(P2POfferRequest):
    invocation_id: str = Field(min_length=1, max_length=128)
    ticket: str = Field(min_length=64, max_length=16_384)


class P2PAnswer(BaseModel):
    type: Literal["answer"]
    sdp: str = Field(min_length=1, max_length=2_000_000)


class P2PAnswerResponse(BaseModel):
    answer: P2PAnswer


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


class NodeHubEnrollmentRequest(GatewayRequest):
    hub_url: str = Field(min_length=8, max_length=2048)
    hub_id: str = Field(min_length=1, max_length=128)
    hub_signing_public_key: str = Field(min_length=40, max_length=64)
    grant_id: str = Field(min_length=1, max_length=128)
    grant_secret: str = Field(min_length=32, max_length=256)
    challenge: str = Field(min_length=16, max_length=256)
    display_name: str = Field(default="Knoa Node", min_length=1, max_length=80)


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


class ReplaceConfigDraftRequest(GatewayRequest):
    document: ManagedConfig
    expected_version: int = Field(ge=1)


class PublishConfigDraftRequest(GatewayRequest):
    expected_version: int = Field(ge=1)
    summary: str = Field(default="", max_length=2000)


class RollbackConfigRequest(GatewayRequest):
    revision_id: str = Field(min_length=1, max_length=128)
    summary: str = Field(default="", max_length=2000)


class PreviewInvocationPolicyRequest(GatewayRequest):
    agent_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    invocation_kind: Literal["user", "delegate", "system"] = "user"
    caller_id: str = Field(default="", max_length=256)
    requested_tools: frozenset[str] | None = None
    requested_skills: frozenset[str] | None = None


class ImportSkillRequest(GatewayRequest):
    source_path: str = Field(min_length=1, max_length=4096)


class ImportLocalMCPRequest(ImportSkillRequest):
    server_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,23}$")


class ImportRemoteMCPRequest(GatewayRequest):
    server_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,23}$")
    url: str = Field(min_length=1, max_length=4096)
    allow_private_network: bool = False


class ApplyFleetCandidateRequest(GatewayRequest):
    rollout_id: str = Field(min_length=1, max_length=128)
    envelope: dict[str, Any]


class WriteSecretRequest(GatewayRequest):
    value: str = Field(min_length=1, max_length=65_536)


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


class EventPollQuery(EventQuery):
    limit: int = Field(default=100, ge=1, le=200)


class TaskEventQuery(GatewayQuery):
    after_seq: int = Field(default=0, ge=0)


class ChatTurnListQuery(GatewayQuery):
    limit: int = Field(default=100, ge=1, le=500)
    cursor: str = Field(default="", max_length=512)


class ArtifactUploadQuery(GatewayQuery):
    session_handle: str = Field(min_length=1, max_length=256)
    name: str = Field(default="", max_length=160)
    caption: str = Field(default="", max_length=1000)


class ArtifactSearchQuery(GatewayQuery):
    session_handle: str = Field(min_length=1, max_length=256)
    q: str = Field(default="", max_length=160)
    kind: Literal["", "image", "file"] = ""
    limit: int = Field(default=50, ge=1, le=200)


class ArtifactDownloadQuery(GatewayQuery):
    session_handle: str = Field(min_length=1, max_length=256)


class RuntimeQuery(GatewayQuery):
    session_handle: str = Field(min_length=1, max_length=256)


class AuditQuery(GatewayQuery):
    after_id: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=200)


class ConfigHistoryQuery(GatewayQuery):
    limit: int = Field(default=50, ge=1, le=200)


class ConfigDiffQuery(GatewayQuery):
    from_revision_id: str = Field(min_length=1, max_length=128)
    to_revision_id: str = Field(min_length=1, max_length=128)


class ErrorResponse(BaseModel):
    error: str
    message: str = ""
    retryable: bool = False
    correlation_id: str = ""


class HealthResponse(BaseModel):
    status: str
    scope: str
    node_id: str


class NodeDescriptorResponse(BaseModel):
    node_id: str
    signing_public_key: str
    signing_key_version: int
    configuration_public_key: str
    configuration_key_version: int
    created_at: float


class NodeHubDescriptorResponse(BaseModel):
    hub_url: str
    hub_id: str
    hub_signing_public_key: str
    enrolled_at: float


class NodeHubStatusResponse(BaseModel):
    enrolled: bool
    hub: NodeHubDescriptorResponse | None = None
    relay_connected: bool
    last_error: str = ""


class ResourceInvocationRequest(GatewayRequest):
    ticket: str = Field(min_length=100, max_length=8192)
    request: ProviderCallRequest


class ResourceInvocationCancelRequest(GatewayRequest):
    ticket: str = Field(min_length=100, max_length=8192)


class ResourceInvocationResponse(BaseModel):
    chunks: tuple[ProviderChunk, ...]


class ResourceInvocationCancelResponse(BaseModel):
    cancel_requested: bool


class NodeHubEnrollmentResponse(BaseModel):
    enrollment: NodeHubDescriptorResponse
    relay_connected: bool


class NodeHubRemovedResponse(BaseModel):
    removed: bool


class ChallengeResponse(BaseModel):
    challenge_id: str
    nonce: str
    expires_at: float


class PairCompleteResponse(BaseModel):
    device_id: str
    principal_id: str
    node: NodeDescriptorResponse


class ExtensionPackageListResponse(BaseModel):
    packages: tuple[dict[str, Any], ...]


class ExtensionImportResponse(BaseModel):
    result: dict[str, Any]


class SecretStatusResponse(BaseModel):
    reference: str
    configured: bool
    rotated_at: float
    fingerprint: str = ""


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


class AgentAvailability(BaseModel):
    agent_id: str
    display_name: str
    reason: str


class AgentAvailabilityResponse(BaseModel):
    unavailable: tuple[AgentAvailability, ...]


class ConfigCurrentResponse(BaseModel):
    revision: ConfigRevision
    state: ConfigControlState
    generations: tuple[dict[str, Any], ...] = ()


class ConfigHistoryResponse(BaseModel):
    revisions: tuple[ConfigRevision, ...]


class ConfigRevisionResponse(BaseModel):
    revision: ConfigRevision


class ConfigDraftResponse(BaseModel):
    draft: ConfigDraft


class ConfigValidationResponse(BaseModel):
    result: ConfigValidationResult


class ConfigPublishResponse(BaseModel):
    result: ConfigPublishResult


class ConfigDiffResponse(BaseModel):
    changes: tuple[dict[str, Any], ...]


class InvocationPolicyPreviewResponse(BaseModel):
    policy: ResolvedInvocationPolicy


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


class PrincipalTaskEventListResponse(BaseModel):
    events: tuple[PrincipalTaskEvent, ...]


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


class ArtifactSearchResponse(BaseModel):
    artifacts: tuple[ArtifactRef, ...]
    next_cursor: str = ""


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
    channel: Literal["personal", "hosted"] = "personal"
    version_name: str = Field(min_length=1, max_length=32)
    version_code: int = Field(ge=1, le=2_100_000_000)
    min_supported_version_code: int = Field(ge=1, le=2_100_000_000)
    size_bytes: int = Field(ge=1, le=1024 * 1024 * 1024)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    published_at: float = Field(gt=0)
    release_notes: str = Field(default="", max_length=20_000)
    download_path: str = Field(min_length=1, max_length=256)
