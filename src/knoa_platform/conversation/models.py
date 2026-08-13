"""Conversation domain models; ordinary chat is never a Task."""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from knoa_platform.agent_runtime.contracts import ArtifactAttachment
from knoa_platform.artifacts import ArtifactRef


NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ChatTurnState(str, Enum):
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ConversationSessionState(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


TERMINAL_CHAT_TURN_STATES = frozenset(
    {ChatTurnState.COMPLETED, ChatTurnState.FAILED, ChatTurnState.CANCELLED}
)


class ConversationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ConversationSession(ConversationModel):
    session_handle: NonEmpty
    principal_id: NonEmpty
    agent_id: NonEmpty = "knoa"
    title: NonEmpty
    state: ConversationSessionState
    turn_count: int = Field(ge=0)
    last_turn_at: float | None = Field(default=None, ge=0.0)
    created_at: float = Field(ge=0.0)
    updated_at: float = Field(ge=0.0)
    revision: int = Field(ge=1)


class ChatToolStep(ConversationModel):
    step_id: NonEmpty
    tool_call_id: NonEmpty
    tool_name: NonEmpty
    arguments: dict[str, Any] = Field(default_factory=dict)
    state: str
    result: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(ge=0.0)
    updated_at: float = Field(ge=0.0)


class ChatApproval(ConversationModel):
    approval_id: NonEmpty
    step_id: NonEmpty
    tool_call_id: NonEmpty
    tool_name: NonEmpty
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    state: str
    created_at: float = Field(ge=0.0)
    resolved_at: float | None = Field(default=None, ge=0.0)
    resolved_by: str = ""


class ChatTimelineEntry(ConversationModel):
    kind: str
    content: str = ""
    tool_call_id: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = Field(default_factory=dict)
    tool_result: Any = None
    blocked: bool = False
    iteration: int = Field(default=0, ge=0)


class ChatTurn(ConversationModel):
    turn_id: NonEmpty
    principal_id: NonEmpty
    session_handle: NonEmpty
    client_request_id: NonEmpty
    user_input: str = ""
    attachments: tuple[ArtifactAttachment, ...] = Field(default=(), max_length=8)
    tools_enabled: bool = True
    state: ChatTurnState
    reasoning: str = ""
    content: str = ""
    final_output: str = ""
    artifacts: tuple[ArtifactRef, ...] = ()
    failure_code: str = ""
    cancel_requested: bool = False
    tool_steps: tuple[ChatToolStep, ...] = ()
    approvals: tuple[ChatApproval, ...] = ()
    timeline: tuple[ChatTimelineEntry, ...] = ()
    created_at: float = Field(ge=0.0)
    updated_at: float = Field(ge=0.0)
    finished_at: float | None = Field(default=None, ge=0.0)
    revision: int = Field(ge=1)


class ChatTurnSignal(ConversationModel):
    turn: ChatTurn
    kind: str = "updated"
