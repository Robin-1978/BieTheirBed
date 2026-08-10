from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from pc_assistant.artifacts import ArtifactRef

if TYPE_CHECKING:
    from pc_assistant.agent_runtime.tool_step import ConfirmationPort, ToolCommitPort


NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Identifier128 = Annotated[NonEmptyString, StringConstraints(max_length=128)]
PrincipalId = Annotated[NonEmptyString, StringConstraints(max_length=256)]
SessionHandle = Annotated[NonEmptyString, StringConstraints(max_length=256)]
BoundedInput = Annotated[str, StringConstraints(max_length=200_000)]
ArtifactDataUrl = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64 * 1024 * 1024),
]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RuntimeScope(ContractModel):
    principal_id: PrincipalId
    session_handle: SessionHandle


class ArtifactAttachment(ContractModel):
    artifact_id: Identifier128
    caption: Annotated[str, StringConstraints(max_length=1000)] = ""


class RunRequest(ContractModel):
    client_request_id: Identifier128
    input: BoundedInput = ""
    attachments: tuple[ArtifactAttachment, ...] = Field(default=(), max_length=8)
    tools_enabled: bool = True

    @model_validator(mode="after")
    def require_input_or_attachment(self) -> RunRequest:
        if not self.input.strip() and not self.attachments:
            raise ValueError("Run request requires input or an attachment")
        return self


class CancelRequest(ContractModel):
    run_id: Identifier128
    reason: Annotated[str, StringConstraints(max_length=1000)] = ""


class CancelResult(ContractModel):
    accepted: bool
    status: Literal["cancelling", "cancelled", "completed", "failed", "not_found"]


class ExtensionStatusRecord(ContractModel):
    extension_id: Identifier128
    kind: Literal["mcp", "skill"]
    state: Literal["configured", "running", "failed", "stopped"]
    tools: tuple[NonEmptyString, ...] = ()
    detail: Annotated[str, StringConstraints(max_length=1000)] = ""


class RuntimeStatus(ContractModel):
    status: NonEmptyString
    connected: bool
    details: dict[str, Any] = Field(default_factory=dict)
    extensions: tuple[ExtensionStatusRecord, ...] = ()


class HistoryResult(ContractModel):
    messages: tuple[dict[str, Any], ...] = ()


class MemoryRecord(ContractModel):
    key: NonEmptyString
    value: str
    category: NonEmptyString
    importance: Literal["core", "relevant"]
    confidence: float = Field(ge=0.0, le=1.0)
    source: str = ""


class MemoryListResult(ContractModel):
    memories: tuple[MemoryRecord, ...] = ()


class MemoryClearResult(ContractModel):
    cleared: bool


class ToolDescriptorRecord(ContractModel):
    name: NonEmptyString
    description: Annotated[str, StringConstraints(max_length=2000)] = ""
    origin_kind: Literal["builtin", "mcp"]
    extension_id: Identifier128
    effect: Literal[
        "read_only",
        "internal_write",
        "local_write",
        "external_side_effect",
        "desktop_control",
    ]
    risk: Literal["low", "medium", "high"]
    capabilities: tuple[
        Literal[
            "host_read",
            "host_write",
            "shell",
            "network",
            "desktop_observe",
            "desktop_control",
            "memory_read",
            "memory_write",
            "mcp",
            "task_management",
        ],
        ...,
    ] = ()
    requires_confirmation: bool


class ToolListResult(ContractModel):
    tools: tuple[NonEmptyString, ...] = ()
    descriptors: tuple[ToolDescriptorRecord, ...] = ()


class ConfigSetRequest(ContractModel):
    field_name: NonEmptyString
    value: bool | int | float | str


class ConfigSetResult(ContractModel):
    applied: bool
    restart_required: bool = False
    error: str = ""


class ArtifactUploadRequest(ContractModel):
    data_url: ArtifactDataUrl
    media_type: Annotated[NonEmptyString, StringConstraints(max_length=128)] = (
        "image/jpeg"
    )
    name: Annotated[str, StringConstraints(max_length=160)] = ""
    caption: Annotated[str, StringConstraints(max_length=1000)] = ""


class ArtifactDownloadRequest(ContractModel):
    artifact_id: Identifier128


class ArtifactDownloadResult(ContractModel):
    artifact: ArtifactRef
    data_url: ArtifactDataUrl


class ArtifactTranscriptionRequest(ContractModel):
    artifact_id: Identifier128


class ArtifactTranscriptionResult(ContractModel):
    artifact_id: Identifier128
    transcript: BoundedInput
    tool_name: Annotated[NonEmptyString, StringConstraints(max_length=256)]


class HealthStatus(ContractModel):
    healthy: bool
    detail: str = ""


class RuntimeEventPayload(ContractModel):
    content: str = ""
    tool_call_id: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = Field(default_factory=dict)
    tool_result: Any = None
    artifact: ArtifactRef | None = None
    blocked: bool = False
    iteration: int = Field(default=0, ge=0)


RuntimeEventType = Literal[
    "content_delta",
    "final_output",
    "reasoning_delta",
    "plan",
    "tool_call",
    "tool_result",
    "artifact",
    "context_compacted",
    "warning",
]


class RuntimeEvent(ContractModel):
    event_type: RuntimeEventType
    payload: RuntimeEventPayload = Field(default_factory=RuntimeEventPayload)


@dataclass(frozen=True)
class RuntimeRunContext:
    scope: RuntimeScope
    run_id: str
    cancellation: asyncio.Event
    messages: tuple[dict[str, Any], ...] = ()
    commit_messages: Callable[[tuple[dict[str, Any], ...]], Awaitable[None]] | None = None
    confirmation: ConfirmationPort | None = None
    tool_commit: ToolCommitPort | None = None

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id must not be empty")


@runtime_checkable
class AgentRuntimePort(Protocol):
    def run(
        self,
        context: RuntimeRunContext,
        request: RunRequest,
    ) -> AsyncIterator[RuntimeEvent]: ...

    async def cancel(
        self,
        scope: RuntimeScope,
        request: CancelRequest,
    ) -> CancelResult: ...

    async def health_check(self) -> HealthStatus: ...


@runtime_checkable
class ControlServicePort(Protocol):
    async def create_session(self, principal_id: NonEmptyString) -> RuntimeScope: ...

    async def get_status(
        self,
        scope: RuntimeScope,
    ) -> RuntimeStatus: ...

    async def get_history(
        self,
        scope: RuntimeScope,
    ) -> HistoryResult: ...

    async def list_memory(
        self,
        scope: RuntimeScope,
    ) -> MemoryListResult: ...

    async def clear_memory(
        self,
        scope: RuntimeScope,
    ) -> MemoryClearResult: ...

    async def list_tools(
        self,
        scope: RuntimeScope,
    ) -> ToolListResult: ...

    async def set_config(
        self,
        scope: RuntimeScope,
        request: ConfigSetRequest,
    ) -> ConfigSetResult: ...


@runtime_checkable
class ArtifactServicePort(Protocol):
    async def upload(
        self,
        scope: RuntimeScope,
        request: ArtifactUploadRequest,
    ) -> ArtifactRef: ...

    async def download(
        self,
        scope: RuntimeScope,
        request: ArtifactDownloadRequest,
    ) -> ArtifactDownloadResult: ...

    async def acknowledge_delivery(
        self,
        scope: RuntimeScope,
        artifact_id: NonEmptyString,
    ) -> None: ...


@runtime_checkable
class ArtifactTranscriptionServicePort(Protocol):
    async def transcribe(
        self,
        scope: RuntimeScope,
        request: ArtifactTranscriptionRequest,
    ) -> ArtifactTranscriptionResult: ...


@runtime_checkable
class TurnInvoker(Protocol):
    def __call__(
        self,
        scope: RuntimeScope,
        request: RunRequest,
    ) -> AsyncIterator[RuntimeEvent]: ...
