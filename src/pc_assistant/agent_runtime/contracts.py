from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from pc_assistant.artifacts import ArtifactRef
from pc_assistant.model_adapter.types import ImageAttachment


NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RuntimeScope(ContractModel):
    principal_id: NonEmptyString
    session_handle: NonEmptyString


class RunRequest(ContractModel):
    input: str = ""
    attachments: tuple[ImageAttachment, ...] = ()


class CancelRequest(ContractModel):
    reason: str = ""


class CancelResult(ContractModel):
    accepted: bool
    status: str = "cancelled"


class StatusRequest(ContractModel):
    include_sessions: bool = False


class RuntimeStatus(ContractModel):
    status: NonEmptyString
    connected: bool
    details: dict[str, Any] = Field(default_factory=dict)


class CommandRequest(ContractModel):
    command: NonEmptyString


class CommandResult(ContractModel):
    applied: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class HealthStatus(ContractModel):
    healthy: bool
    detail: str = ""


class RuntimeEventPayload(ContractModel):
    content: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = Field(default_factory=dict)
    tool_result: Any = None
    artifact: ArtifactRef | None = None
    blocked: bool = False
    iteration: int = Field(default=0, ge=0)


class RuntimeEvent(ContractModel):
    event_type: NonEmptyString
    payload: RuntimeEventPayload = Field(default_factory=RuntimeEventPayload)


class RunEvent(ContractModel):
    api_version: Literal["v1"] = "v1"
    run_id: NonEmptyString
    event_seq: int = Field(gt=0)
    event_type: NonEmptyString
    payload: RuntimeEventPayload


@runtime_checkable
class AgentRuntimePort(Protocol):
    def run(
        self,
        scope: RuntimeScope,
        request: RunRequest,
    ) -> AsyncIterator[RuntimeEvent]: ...

    async def cancel(
        self,
        scope: RuntimeScope,
        request: CancelRequest,
    ) -> CancelResult: ...

    async def health_check(self) -> HealthStatus: ...

    async def get_status(
        self,
        scope: RuntimeScope,
        request: StatusRequest,
    ) -> RuntimeStatus: ...

    async def command(
        self,
        scope: RuntimeScope,
        request: CommandRequest,
    ) -> CommandResult: ...


@runtime_checkable
class TurnInvoker(Protocol):
    def __call__(
        self,
        scope: RuntimeScope,
        request: RunRequest,
    ) -> AsyncIterator[RuntimeEvent]: ...
