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
    CancelResult,
    ConfigSetResult,
    HealthStatus,
    HistoryResult,
    MemoryClearResult,
    MemoryListResult,
    RunEvent,
    RuntimeStatus,
    ToolListResult,
)
from pc_assistant.artifacts import ArtifactRef


NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
RequestId = Annotated[NonEmpty, StringConstraints(max_length=128)]
SessionHandle = Annotated[NonEmpty, StringConstraints(max_length=256)]
RunId = Annotated[NonEmpty, StringConstraints(max_length=128)]
CORE_WS_MAX_SIZE = 70 * 1024 * 1024


class CoreModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ArtifactInputRef(CoreModel):
    artifact_id: Annotated[NonEmpty, StringConstraints(max_length=128)]
    caption: Annotated[str, StringConstraints(max_length=1000)] = ""


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


class StartRunRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["run"] = "run"
    session_handle: SessionHandle
    input: Annotated[str, StringConstraints(max_length=200_000)] = ""
    attachments: tuple[ArtifactInputRef, ...] = Field(default=(), max_length=8)
    tools_enabled: bool = True

    @model_validator(mode="after")
    def require_input_or_attachment(self) -> StartRunRequest:
        if not self.input.strip() and not self.attachments:
            raise ValueError("Run request requires input or an attachment")
        return self


class CancelRunRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["cancel_run"] = "cancel_run"
    run_id: RunId
    reason: Annotated[str, StringConstraints(max_length=1000)] = ""


class ResolveConfirmationRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["confirmation_resolve"] = "confirmation_resolve"
    confirmation_id: Annotated[NonEmpty, StringConstraints(max_length=128)]
    approved: bool


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
    | StartRunRequest
    | CancelRunRequest
    | ResolveConfirmationRequest
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
    "run_not_found",
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


class RunAcceptedMessage(CoreModel):
    message_type: Literal["run_accepted"] = "run_accepted"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    run_id: RunId


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


class CancelResultMessage(CoreModel):
    message_type: Literal["cancel_result"] = "cancel_result"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    result: CancelResult


class ConfirmationRequestedMessage(CoreModel):
    message_type: Literal["confirmation_requested"] = "confirmation_requested"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    confirmation_id: Annotated[NonEmpty, StringConstraints(max_length=128)]
    session_handle: SessionHandle
    tool_name: Annotated[NonEmpty, StringConstraints(max_length=256)]
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: Annotated[str, StringConstraints(max_length=1000)] = ""


class ConfirmationResolvedMessage(CoreModel):
    message_type: Literal["confirmation_resolved"] = "confirmation_resolved"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    resolved: bool


CoreServerMessage: TypeAlias = Annotated[
    AuthenticatedMessage
    | SessionCreatedMessage
    | RunAcceptedMessage
    | HealthMessage
    | StatusMessage
    | HistoryMessage
    | MemoryListMessage
    | MemoryClearedMessage
    | ToolsMessage
    | ConfigSetMessage
    | ArtifactUploadedMessage
    | ArtifactDownloadedMessage
    | CancelResultMessage
    | ConfirmationRequestedMessage
    | ConfirmationResolvedMessage
    | CoreError
    | RunEvent,
    Field(discriminator="message_type"),
]
_CORE_SERVER_MESSAGE_ADAPTER = TypeAdapter(CoreServerMessage)


def parse_core_server_message_json(raw: str | bytes) -> CoreServerMessage:
    return _CORE_SERVER_MESSAGE_ADAPTER.validate_json(raw)


def core_request_schema() -> dict[str, Any]:
    """Expose the canonical JSON Schema for protocol/tooling generation."""
    return _CORE_REQUEST_ADAPTER.json_schema()
