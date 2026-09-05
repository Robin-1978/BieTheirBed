from __future__ import annotations

from typing import Annotated, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from knoa_platform.artifacts import ArtifactRef

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


class MemoryDeleteResult(ContractModel):
    deleted: bool


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


class MCPResourceCatalogRecord(ContractModel):
    """A discovered, read-only MCP Resource exposed to client configuration UIs."""

    server_id: Identifier128
    uri: Annotated[str, StringConstraints(max_length=4096)]
    name: Annotated[str, StringConstraints(max_length=256)] = ""
    description: Annotated[str, StringConstraints(max_length=2000)] = ""
    mime_type: Annotated[str, StringConstraints(max_length=256)] = ""
    subscribable: bool = False


class MCPResourceCatalogResult(ContractModel):
    resources: tuple[MCPResourceCatalogRecord, ...] = ()


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

    async def delete_memory(
        self,
        scope: RuntimeScope,
        key: str,
    ) -> MemoryDeleteResult: ...

    async def list_tools(
        self,
        scope: RuntimeScope,
    ) -> ToolListResult: ...

    async def list_mcp_resources(
        self,
        principal_id: NonEmptyString,
    ) -> MCPResourceCatalogResult: ...

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
