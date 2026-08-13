"""Wire-safe Agent Runtime SPI with no dependency on Knoa Platform internals."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol, TypeAlias, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Identifier = Annotated[NonEmpty, StringConstraints(max_length=256)]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

AgentCapability: TypeAlias = Literal[
    "turn.steer",
    "interaction.approval",
    "interaction.user_input",
    "mcp.client",
    "input.image",
    "input.file",
    "input.audio",
    "event.reasoning_summary",
    "event.plan",
    "event.tool_lifecycle",
    "event.file_change",
    "event.usage",
    "event.context_compaction",
]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RuntimeLimits(ContractModel):
    max_input_bytes: int = Field(default=16 * 1024 * 1024, gt=0)
    max_inline_artifact_bytes: int = Field(default=1024 * 1024, gt=0)
    max_concurrent_turns: int = Field(default=1, gt=0)


class AgentDescriptor(ContractModel):
    agent_id: Identifier
    display_name: NonEmpty
    implementation_version: NonEmpty
    protocol_name: NonEmpty = "knoa-agent-runtime"
    protocol_version: NonEmpty = "1.0"
    capabilities: frozenset[AgentCapability] = frozenset()
    limits: RuntimeLimits = Field(default_factory=RuntimeLimits)


class RuntimeSession(ContractModel):
    agent_id: Identifier
    runtime_session_ref: Identifier
    runtime_protocol_version: NonEmpty
    binding_epoch: int = Field(ge=1)


class CreateRuntimeSession(ContractModel):
    operation_id: Identifier
    binding_epoch: int = Field(ge=1)


class ResumeRuntimeSession(ContractModel):
    operation_id: Identifier
    session: RuntimeSession


class ArtifactReference(ContractModel):
    artifact_id: Identifier
    name: Annotated[str, StringConstraints(max_length=512)] = ""
    media_type: Annotated[NonEmpty, StringConstraints(max_length=256)]
    size_bytes: int = Field(ge=0)
    sha256: Digest


class TextPart(ContractModel):
    type: Literal["text"] = "text"
    text: Annotated[str, StringConstraints(max_length=200_000)]


class ArtifactPart(ContractModel):
    type: Literal["artifact"] = "artifact"
    artifact: ArtifactReference
    resource_uri: NonEmpty
    presentation: Literal["image", "file", "audio"]
    caption: Annotated[str, StringConstraints(max_length=1000)] = ""


class ResourceLinkPart(ContractModel):
    type: Literal["resource_link"] = "resource_link"
    uri: NonEmpty
    name: Annotated[str, StringConstraints(max_length=512)] = ""
    media_type: Annotated[str, StringConstraints(max_length=256)] = ""


TurnInputPart: TypeAlias = Annotated[
    TextPart | ArtifactPart | ResourceLinkPart,
    Field(discriminator="type"),
]


class McpEndpointGrant(ContractModel):
    server_id: Identifier
    transport: Literal["streamable_http", "in_memory"]
    endpoint: NonEmpty
    authorization: NonEmpty
    expires_at: float = Field(gt=0.0)
    scope_digest: Digest
    binding_epoch: int = Field(ge=1)

    def __repr__(self) -> str:
        return (
            "McpEndpointGrant("
            f"server_id={self.server_id!r}, transport={self.transport!r}, "
            f"endpoint={self.endpoint!r}, authorization='<redacted>', "
            f"expires_at={self.expires_at!r}, scope_digest={self.scope_digest!r}, "
            f"binding_epoch={self.binding_epoch!r})"
        )


class RuntimeTurnRequest(ContractModel):
    session: RuntimeSession
    operation_id: Identifier
    input: tuple[TurnInputPart, ...] = Field(min_length=1, max_length=16)
    mcp: McpEndpointGrant
    deadline: float | None = Field(default=None, gt=0.0)
    options: dict[str, bool | int | float | str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_binding(self) -> RuntimeTurnRequest:
        if self.session.binding_epoch != self.mcp.binding_epoch:
            raise ValueError("Runtime Session and MCP grant binding epochs differ")
        return self


class _RuntimeEvent(ContractModel):
    source_event_id: str | None = None
    runtime_session_ref: Identifier
    runtime_turn_ref: Identifier
    occurred_at: float = Field(ge=0.0)


class AssistantDelta(_RuntimeEvent):
    event_type: Literal["assistant_delta"] = "assistant_delta"
    content: str


class ReasoningSummaryDelta(_RuntimeEvent):
    event_type: Literal["reasoning_summary_delta"] = "reasoning_summary_delta"
    content: str


class PlanChanged(_RuntimeEvent):
    event_type: Literal["plan_changed"] = "plan_changed"
    content: str


class ToolCallStarted(_RuntimeEvent):
    event_type: Literal["tool_call_started"] = "tool_call_started"
    tool_call_id: Identifier
    tool_name: Identifier
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolCallFinished(_RuntimeEvent):
    event_type: Literal["tool_call_finished"] = "tool_call_finished"
    tool_call_id: Identifier
    tool_name: Identifier
    status: Literal["completed", "rejected", "failed", "not_executed"]
    code: str = ""
    output: Any = None


class ExistingResourceArtifact(ContractModel):
    type: Literal["existing_resource"] = "existing_resource"
    resource_uri: NonEmpty
    media_type: NonEmpty
    sha256: Digest


class InlineArtifact(ContractModel):
    type: Literal["inline"] = "inline"
    name: NonEmpty
    media_type: NonEmpty
    data_base64: NonEmpty
    sha256: Digest


RuntimeArtifact: TypeAlias = Annotated[
    ExistingResourceArtifact | InlineArtifact,
    Field(discriminator="type"),
]


class ArtifactProduced(_RuntimeEvent):
    event_type: Literal["artifact_produced"] = "artifact_produced"
    artifact: RuntimeArtifact


class InteractionRequested(_RuntimeEvent):
    event_type: Literal["interaction_requested"] = "interaction_requested"
    interaction_id: Identifier
    interaction_epoch: int = Field(ge=1)
    kind: Literal[
        "tool_approval",
        "permission_approval",
        "user_input",
        "mcp_elicitation",
    ]
    display: dict[str, Any] = Field(default_factory=dict)
    resolution_schema: dict[str, Any] = Field(default_factory=dict)
    expires_at: float | None = Field(default=None, gt=0.0)


class ContextCompacted(_RuntimeEvent):
    event_type: Literal["context_compacted"] = "context_compacted"
    source_cursor: int = Field(ge=0)
    state_version: NonEmpty
    tokens_before: int = Field(ge=0)
    tokens_after: int = Field(ge=0)


class UsageReported(_RuntimeEvent):
    event_type: Literal["usage_reported"] = "usage_reported"
    usage: dict[str, int | float | str] = Field(default_factory=dict)


class RuntimeWarning(_RuntimeEvent):
    event_type: Literal["runtime_warning"] = "runtime_warning"
    code: NonEmpty
    message: str = ""


class TurnFinished(_RuntimeEvent):
    event_type: Literal["turn_finished"] = "turn_finished"
    status: Literal[
        "completed",
        "interrupted",
        "failed",
        "refused",
        "outcome_unknown",
    ]
    final_output: str = ""
    error_code: str = ""


RuntimeTurnEvent: TypeAlias = Annotated[
    AssistantDelta
    | ReasoningSummaryDelta
    | PlanChanged
    | ToolCallStarted
    | ToolCallFinished
    | ArtifactProduced
    | InteractionRequested
    | ContextCompacted
    | UsageReported
    | RuntimeWarning
    | TurnFinished,
    Field(discriminator="event_type"),
]


@dataclass(frozen=True)
class RuntimeTurn:
    runtime_turn_ref: str
    events: AsyncIterator[RuntimeTurnEvent]

    def __post_init__(self) -> None:
        if not self.runtime_turn_ref.strip():
            raise ValueError("runtime_turn_ref must not be empty")


class RuntimeInterruptCommand(ContractModel):
    session: RuntimeSession
    runtime_turn_ref: Identifier
    command_id: Identifier
    reason: Annotated[str, StringConstraints(max_length=1000)] = ""


class RuntimeSteerCommand(ContractModel):
    session: RuntimeSession
    runtime_turn_ref: Identifier
    command_id: Identifier
    input: tuple[TurnInputPart, ...] = Field(min_length=1, max_length=16)


class RuntimeInteractionResolution(ContractModel):
    session: RuntimeSession
    runtime_turn_ref: Identifier
    interaction_id: Identifier
    interaction_epoch: int = Field(ge=1)
    command_id: Identifier
    value: Any


ResolveRuntimeInteraction = RuntimeInteractionResolution


class RuntimeCommandResult(ContractModel):
    status: Literal["accepted", "rejected", "unknown", "not_found"]
    code: str = ""


class ReconcileRuntime(ContractModel):
    session: RuntimeSession
    runtime_turn_ref: str = ""
    operation_id: Identifier


class RuntimeObservedState(ContractModel):
    session_state: Literal["ready", "not_found", "not_resumable"]
    turn_state: Literal[
        "none",
        "running",
        "completed",
        "interrupted",
        "failed",
        "unknown",
    ] = "none"
    runtime_turn_ref: str = ""


class RuntimeHealth(ContractModel):
    healthy: bool
    state: Literal["ready", "degraded", "failed", "draining"]
    detail: str = ""


@runtime_checkable
class AgentRuntime(Protocol):
    @property
    def descriptor(self) -> AgentDescriptor: ...

    async def create_session(self, request: CreateRuntimeSession) -> RuntimeSession: ...

    async def resume_session(self, request: ResumeRuntimeSession) -> RuntimeSession: ...

    async def start_turn(self, request: RuntimeTurnRequest) -> RuntimeTurn: ...

    async def steer_turn(self, command: RuntimeSteerCommand) -> RuntimeCommandResult: ...

    async def interrupt_turn(
        self, command: RuntimeInterruptCommand
    ) -> RuntimeCommandResult: ...

    async def resolve_interaction(
        self, command: RuntimeInteractionResolution
    ) -> RuntimeCommandResult: ...

    async def reconcile(self, request: ReconcileRuntime) -> RuntimeObservedState: ...

    async def release_session(self, session: RuntimeSession) -> None: ...

    async def delete_session(self, session: RuntimeSession) -> None: ...

    async def health_check(self) -> RuntimeHealth: ...

    async def drain(self, deadline: float) -> None: ...
