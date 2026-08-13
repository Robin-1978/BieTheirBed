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

from knoa_platform.agent_runtime.contracts import (
    ArtifactDownloadResult,
    ArtifactTranscriptionResult,
    ConfigSetResult,
    HealthStatus,
    HistoryResult,
    MemoryClearResult,
    MemoryListResult,
    RuntimeStatus,
    ToolListResult,
)
from knoa_platform.artifacts import ArtifactRef
from knoa_platform.interactions import HumanInteraction
from knoa_platform.automation import (
    ScheduleRecord,
    ScheduleSpec,
    ScheduleState,
    TriggerEventRecord,
    TriggerEventState,
    TriggerRecord,
    TriggerState,
)
from knoa_platform.conversation import (
    ChatTurn,
    ChatTurnState,
    ConversationSession,
    ConversationSessionState,
)
from knoa_platform.tasks import (
    ApprovalState,
    PrincipalTaskEvent,
    TaskCancelResult,
    TaskDefinitionRecord,
    TaskDefinitionState,
    TaskEvent,
    TaskExecutionRecord,
    TaskExecutionTrace,
    TaskLaunchPolicy,
    TaskLaunchReason,
    TaskOrigin,
    TaskPauseResult,
    TaskRecord,
    TaskState,
)

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
RequestId = Annotated[NonEmpty, StringConstraints(max_length=128)]
SessionHandle = Annotated[NonEmpty, StringConstraints(max_length=256)]
TaskId = Annotated[NonEmpty, StringConstraints(max_length=128)]
ChatTurnId = Annotated[NonEmpty, StringConstraints(max_length=128)]
CORE_WS_MAX_SIZE = 70 * 1024 * 1024


class CoreModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ArtifactInputRef(CoreModel):
    artifact_id: Annotated[NonEmpty, StringConstraints(max_length=128)]
    caption: Annotated[str, StringConstraints(max_length=1000)] = ""


class TaskSnapshot(CoreModel):
    task_id: TaskId
    session_handle: SessionHandle
    agent_id: TaskId
    client_request_id: RequestId
    origin: TaskOrigin = TaskOrigin.USER
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
    trace: TaskExecutionTrace | None = None

    @classmethod
    def from_record(
        cls,
        task: TaskRecord,
        *,
        trace: TaskExecutionTrace | None = None,
    ) -> TaskSnapshot:
        return cls(
            task_id=task.task_id,
            session_handle=task.session_handle,
            agent_id=task.agent_id,
            client_request_id=task.client_request_id,
            origin=getattr(task, "origin", TaskOrigin.USER),
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
            trace=trace,
        )


class ProductTaskSnapshot(CoreModel):
    task_id: TaskId
    session_handle: SessionHandle
    agent_id: TaskId
    title: Annotated[NonEmpty, StringConstraints(max_length=200)]
    goal: Annotated[str, StringConstraints(min_length=1, max_length=200_000)]
    attachments: tuple[ArtifactInputRef, ...] = Field(default=(), max_length=8)
    tools_enabled: bool
    priority: int = Field(ge=0, le=9)
    launch_policy: TaskLaunchPolicy
    notification_policy: dict[str, bool] = Field(default_factory=dict)
    state: TaskDefinitionState
    revision: int = Field(ge=1)
    latest_execution_id: str = ""
    execution_count: int = Field(ge=0)
    created_at: float = Field(ge=0.0)
    updated_at: float = Field(ge=0.0)

    @classmethod
    def from_record(cls, task: TaskDefinitionRecord) -> ProductTaskSnapshot:
        return cls(
            task_id=task.task_id,
            session_handle=task.session_handle,
            agent_id=task.agent_id,
            title=task.title,
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
            launch_policy=task.launch_policy,
            notification_policy=task.notification_policy,
            state=task.state,
            revision=task.revision,
            latest_execution_id=task.latest_execution_id,
            execution_count=task.execution_count,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )


class HumanInteractionSnapshot(CoreModel):
    interaction_id: NonEmpty
    owner_kind: Literal["conversation_turn", "task_execution"]
    owner_id: NonEmpty
    kind: Literal["user_input", "mcp_elicitation"] = "user_input"
    state: Literal["pending", "resolved", "cancelled", "expired", "runtime_lost"]
    display: dict[str, Any] = Field(default_factory=dict)
    resolution_schema: dict[str, Any] = Field(default_factory=dict)
    resolution: Any = None
    created_at: float = Field(ge=0.0)
    resolved_at: float | None = Field(default=None, ge=0.0)
    expires_at: float | None = Field(default=None, gt=0.0)

    @classmethod
    def from_record(cls, interaction: HumanInteraction) -> HumanInteractionSnapshot:
        return cls.model_validate(
            interaction.model_dump(
                exclude={
                    "principal_id",
                    "runtime_session_ref",
                    "runtime_turn_ref",
                    "runtime_interaction_id",
                    "interaction_epoch",
                    "resolved_by",
                }
            )
        )


class ProductTaskExecutionSnapshot(CoreModel):
    execution_id: TaskId
    task_id: TaskId
    agent_id_snapshot: TaskId
    task_revision: int = Field(ge=1)
    launch_reason: TaskLaunchReason
    goal_snapshot: Annotated[str, StringConstraints(min_length=1, max_length=200_000)]
    attachment_snapshots: tuple[ArtifactInputRef, ...] = Field(default=(), max_length=8)
    policy_snapshot: TaskLaunchPolicy
    state: TaskState
    phase: str = ""
    cancel_requested: bool = False
    final_result: str = ""
    failure_code: str = ""
    created_at: float = Field(ge=0.0)
    updated_at: float = Field(ge=0.0)
    started_at: float | None = Field(default=None, ge=0.0)
    finished_at: float | None = Field(default=None, ge=0.0)
    trace: TaskExecutionTrace | None = None
    approvals: tuple[TaskApprovalSnapshot, ...] = ()
    interactions: tuple[HumanInteractionSnapshot, ...] = ()

    @classmethod
    def from_record(
        cls,
        execution: TaskExecutionRecord,
    ) -> ProductTaskExecutionSnapshot:
        return cls(
            execution_id=execution.execution_id,
            task_id=execution.task_id,
            agent_id_snapshot=execution.agent_id_snapshot,
            task_revision=execution.task_revision,
            launch_reason=execution.launch_reason,
            goal_snapshot=execution.goal_snapshot,
            attachment_snapshots=tuple(
                ArtifactInputRef(
                    artifact_id=attachment.artifact_id,
                    caption=attachment.caption,
                )
                for attachment in execution.attachment_snapshots
            ),
            policy_snapshot=execution.policy_snapshot,
            state=execution.state,
            phase=execution.phase,
            cancel_requested=execution.cancel_requested,
            final_result=execution.final_result,
            failure_code=execution.failure_code,
            created_at=execution.created_at,
            updated_at=execution.updated_at,
            started_at=execution.started_at,
            finished_at=execution.finished_at,
            trace=execution.trace,
            approvals=tuple(
                TaskApprovalSnapshot(
                    approval_id=approval.approval_id,
                    tool_name=approval.tool_name,
                    arguments=approval.arguments,
                    reason=approval.reason,
                    state=approval.state,
                    created_at=approval.created_at,
                    resolved_at=approval.resolved_at,
                )
                for approval in execution.approvals
            ),
            interactions=tuple(
                HumanInteractionSnapshot.from_record(interaction)
                for interaction in execution.interactions
            ),
        )


class TaskApprovalSnapshot(CoreModel):
    approval_id: NonEmpty
    tool_name: NonEmpty
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    state: ApprovalState
    created_at: float = Field(ge=0.0)
    resolved_at: float | None = Field(default=None, ge=0.0)


class ChatToolStepSnapshot(CoreModel):
    step_id: NonEmpty
    tool_call_id: NonEmpty
    tool_name: NonEmpty
    arguments: dict[str, Any] = Field(default_factory=dict)
    state: str
    result: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(ge=0.0)
    updated_at: float = Field(ge=0.0)


class ChatApprovalSnapshot(CoreModel):
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


class ChatTimelineEntrySnapshot(CoreModel):
    kind: str
    content: str = ""
    tool_call_id: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = Field(default_factory=dict)
    tool_result: Any = None
    blocked: bool = False
    iteration: int = Field(default=0, ge=0)


class ChatTurnSnapshot(CoreModel):
    turn_id: ChatTurnId
    session_handle: SessionHandle
    client_request_id: RequestId
    user_input: Annotated[str, StringConstraints(max_length=200_000)] = ""
    attachments: tuple[ArtifactInputRef, ...] = Field(default=(), max_length=8)
    tools_enabled: bool
    state: ChatTurnState
    reasoning: str = ""
    content: str = ""
    final_output: str = ""
    artifacts: tuple[ArtifactRef, ...] = ()
    failure_code: str = ""
    cancel_requested: bool
    tool_steps: tuple[ChatToolStepSnapshot, ...] = ()
    approvals: tuple[ChatApprovalSnapshot, ...] = ()
    interactions: tuple[HumanInteractionSnapshot, ...] = ()
    timeline: tuple[ChatTimelineEntrySnapshot, ...] = ()
    created_at: float = Field(ge=0.0)
    updated_at: float = Field(ge=0.0)
    finished_at: float | None = Field(default=None, ge=0.0)
    revision: int = Field(ge=1)

    @classmethod
    def from_record(cls, turn: ChatTurn) -> ChatTurnSnapshot:
        return cls(
            turn_id=turn.turn_id,
            session_handle=turn.session_handle,
            client_request_id=turn.client_request_id,
            user_input=turn.user_input,
            attachments=tuple(
                ArtifactInputRef(
                    artifact_id=attachment.artifact_id,
                    caption=attachment.caption,
                )
                for attachment in turn.attachments
            ),
            tools_enabled=turn.tools_enabled,
            state=turn.state,
            reasoning=turn.reasoning,
            content=turn.content,
            final_output=turn.final_output,
            artifacts=turn.artifacts,
            failure_code=turn.failure_code,
            cancel_requested=turn.cancel_requested,
            tool_steps=tuple(
                ChatToolStepSnapshot.model_validate(step.model_dump())
                for step in turn.tool_steps
            ),
            approvals=tuple(
                ChatApprovalSnapshot.model_validate(approval.model_dump())
                for approval in turn.approvals
            ),
            interactions=tuple(
                HumanInteractionSnapshot.from_record(interaction)
                for interaction in turn.interactions
            ),
            timeline=tuple(
                ChatTimelineEntrySnapshot.model_validate(entry.model_dump())
                for entry in turn.timeline
            ),
            created_at=turn.created_at,
            updated_at=turn.updated_at,
            finished_at=turn.finished_at,
            revision=turn.revision,
        )


class ConversationSessionSnapshot(CoreModel):
    session_handle: SessionHandle
    agent_id: TaskId
    title: Annotated[NonEmpty, StringConstraints(max_length=120)]
    state: ConversationSessionState
    turn_count: int = Field(ge=0)
    last_turn_at: float | None = Field(default=None, ge=0.0)
    created_at: float = Field(ge=0.0)
    updated_at: float = Field(ge=0.0)
    revision: int = Field(ge=1)

    @classmethod
    def from_record(cls, session: ConversationSession) -> ConversationSessionSnapshot:
        return cls(
            session_handle=session.session_handle,
            agent_id=session.agent_id,
            title=session.title,
            state=session.state,
            turn_count=session.turn_count,
            last_turn_at=session.last_turn_at,
            created_at=session.created_at,
            updated_at=session.updated_at,
            revision=session.revision,
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


class TriggerSnapshot(CoreModel):
    trigger_id: Annotated[NonEmpty, StringConstraints(max_length=128)]
    session_handle: SessionHandle
    name: Annotated[NonEmpty, StringConstraints(max_length=128)]
    goal: Annotated[str, StringConstraints(min_length=1, max_length=64_000)]
    tools_enabled: bool
    priority: int = Field(ge=0, le=9)
    state: TriggerState
    event_count: int = Field(ge=0)
    last_event_at: float | None = Field(default=None, ge=0.0)
    created_at: float = Field(ge=0.0)
    updated_at: float = Field(ge=0.0)

    @classmethod
    def from_record(cls, trigger: TriggerRecord) -> TriggerSnapshot:
        return cls(
            trigger_id=trigger.trigger_id,
            session_handle=trigger.session_handle,
            name=trigger.name,
            goal=trigger.goal,
            tools_enabled=trigger.tools_enabled,
            priority=trigger.priority,
            state=trigger.state,
            event_count=trigger.event_count,
            last_event_at=trigger.last_event_at,
            created_at=trigger.created_at,
            updated_at=trigger.updated_at,
        )


class TriggerEventSnapshot(CoreModel):
    trigger_event_id: Annotated[NonEmpty, StringConstraints(max_length=128)]
    trigger_id: Annotated[NonEmpty, StringConstraints(max_length=128)]
    external_event_id: Annotated[NonEmpty, StringConstraints(max_length=256)]
    state: TriggerEventState
    task_id: Annotated[str, StringConstraints(max_length=128)] = ""
    received_at: float = Field(ge=0.0)

    @classmethod
    def from_record(cls, event: TriggerEventRecord) -> TriggerEventSnapshot:
        return cls(
            trigger_event_id=event.trigger_event_id,
            trigger_id=event.trigger_id,
            external_event_id=event.external_event_id,
            state=event.state,
            task_id=event.task_id,
            received_at=event.received_at,
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
    activate: bool = True
    agent_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")] | None = None


class GetConversationSessionRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["get_conversation_session"] = "get_conversation_session"
    session_handle: SessionHandle


class ListConversationSessionsRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["list_conversation_sessions"] = "list_conversation_sessions"
    include_archived: bool = False
    limit: int = Field(default=100, ge=1, le=200)
    cursor: Annotated[str, StringConstraints(max_length=512)] = ""


class UpdateConversationSessionRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["update_conversation_session"] = "update_conversation_session"
    session_handle: SessionHandle
    title: Annotated[str, StringConstraints(min_length=1, max_length=120)] | None = None
    state: ConversationSessionState | None = None
    expected_revision: int | None = Field(default=None, ge=1)


class DeleteConversationSessionRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["delete_conversation_session"] = "delete_conversation_session"
    session_handle: SessionHandle


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
    origin: TaskOrigin = TaskOrigin.USER
    agent_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")] | None = None

    @model_validator(mode="after")
    def require_input_or_attachment(self) -> CreateTaskRequest:
        if not self.input.strip() and not self.attachments:
            raise ValueError("Task request requires input or an attachment")
        return self


class CreateChatTurnRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["create_chat_turn"] = "create_chat_turn"
    client_request_id: RequestId
    session_handle: SessionHandle
    input: Annotated[str, StringConstraints(max_length=200_000)] = ""
    attachments: tuple[ArtifactInputRef, ...] = Field(default=(), max_length=8)
    tools_enabled: bool = True
    agent_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")] | None = None

    @model_validator(mode="after")
    def require_input_or_attachment(self) -> CreateChatTurnRequest:
        if not self.input.strip() and not self.attachments:
            raise ValueError("ChatTurn request requires input or an attachment")
        return self


class GetChatTurnRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["get_chat_turn"] = "get_chat_turn"
    turn_id: ChatTurnId


class ListChatTurnsRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["list_chat_turns"] = "list_chat_turns"
    session_handle: SessionHandle
    limit: int = Field(default=100, ge=1, le=500)
    cursor: Annotated[str, StringConstraints(max_length=512)] = ""


class SubscribeChatTurnRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["subscribe_chat_turn"] = "subscribe_chat_turn"
    turn_id: ChatTurnId


class CancelChatTurnRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["cancel_chat_turn"] = "cancel_chat_turn"
    turn_id: ChatTurnId


class RetryChatTurnRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["retry_chat_turn"] = "retry_chat_turn"
    turn_id: ChatTurnId


class ResolveChatApprovalRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["resolve_chat_approval"] = "resolve_chat_approval"
    approval_id: Annotated[NonEmpty, StringConstraints(max_length=128)]
    approved: bool


class ResolveHumanInteractionRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["interaction_resolve"] = "interaction_resolve"
    interaction_id: Annotated[NonEmpty, StringConstraints(max_length=128)]
    value: Any


class SubscribeTaskRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["subscribe_task"] = "subscribe_task"
    task_id: TaskId
    after_seq: int = Field(default=0, ge=0)


class SubscribePrincipalTaskEventsRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["subscribe_principal_task_events"] = (
        "subscribe_principal_task_events"
    )
    after_id: int = Field(default=0, ge=0)


class UnsubscribeRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["unsubscribe"] = "unsubscribe"
    subscription_request_id: RequestId


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
    origins: tuple[TaskOrigin, ...] = Field(default=(), max_length=5)
    limit: int = Field(default=50, ge=1, le=100)
    cursor: Annotated[str, StringConstraints(max_length=512)] = ""


class CreateProductTaskRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["product_task_create"] = "product_task_create"
    client_request_id: RequestId
    session_handle: SessionHandle
    title: Annotated[str, StringConstraints(max_length=200)] = ""
    goal: Annotated[str, StringConstraints(min_length=1, max_length=200_000)]
    attachments: tuple[ArtifactInputRef, ...] = Field(default=(), max_length=8)
    tools_enabled: bool = True
    priority: int = Field(default=0, ge=0, le=9)
    launch_policy: TaskLaunchPolicy = Field(default_factory=TaskLaunchPolicy)
    notification_policy: dict[str, bool] = Field(default_factory=dict)
    agent_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")] | None = None


class GetProductTaskRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["product_task_get"] = "product_task_get"
    task_id: TaskId


class ListProductTasksRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["product_task_list"] = "product_task_list"
    state: TaskDefinitionState | None = None
    include_archived: bool = False
    limit: int = Field(default=100, ge=1, le=200)


class UpdateProductTaskRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["product_task_update"] = "product_task_update"
    task_id: TaskId
    title: Annotated[str, StringConstraints(max_length=200)] | None = None
    goal: Annotated[str, StringConstraints(min_length=1, max_length=200_000)] | None = None
    attachments: tuple[ArtifactInputRef, ...] | None = Field(default=None, max_length=8)
    tools_enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=9)
    launch_policy: TaskLaunchPolicy | None = None
    notification_policy: dict[str, bool] | None = None
    expected_revision: int | None = Field(default=None, ge=1)


class SetProductTaskStateRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["product_task_set_state"] = "product_task_set_state"
    task_id: TaskId
    state: TaskDefinitionState


class DeleteProductTaskRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["product_task_delete"] = "product_task_delete"
    task_id: TaskId


class ExecuteProductTaskRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["product_task_execute"] = "product_task_execute"
    task_id: TaskId


class GetProductTaskExecutionRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["product_task_execution_get"] = "product_task_execution_get"
    execution_id: TaskId


class ListProductTaskExecutionsRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["product_task_execution_list"] = "product_task_execution_list"
    task_id: TaskId
    limit: int = Field(default=100, ge=1, le=200)


class DeleteProductTaskExecutionRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["product_task_execution_delete"] = "product_task_execution_delete"
    execution_id: TaskId


class RerunProductTaskExecutionRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["product_task_execution_rerun"] = "product_task_execution_rerun"
    execution_id: TaskId


class ContinueProductTaskRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["product_task_continue"] = "product_task_continue"
    client_request_id: RequestId
    task_id: TaskId
    input: Annotated[str, StringConstraints(max_length=200_000)] = ""
    attachments: tuple[ArtifactInputRef, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def require_input_or_attachment(self) -> ContinueProductTaskRequest:
        if not self.input.strip() and not self.attachments:
            raise ValueError("Task follow-up requires input or an attachment")
        return self


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


class PauseScheduleRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["pause_schedule"] = "pause_schedule"
    schedule_id: Annotated[NonEmpty, StringConstraints(max_length=128)]


class ResumeScheduleRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["resume_schedule"] = "resume_schedule"
    schedule_id: Annotated[NonEmpty, StringConstraints(max_length=128)]


class CreateTriggerRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["create_trigger"] = "create_trigger"
    session_handle: SessionHandle
    name: Annotated[NonEmpty, StringConstraints(max_length=128)]
    goal: Annotated[str, StringConstraints(min_length=1, max_length=64_000)]
    tools_enabled: bool = True
    priority: int = Field(default=0, ge=0, le=9)


class GetTriggerRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["get_trigger"] = "get_trigger"
    trigger_id: Annotated[NonEmpty, StringConstraints(max_length=128)]


class ListTriggersRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["list_triggers"] = "list_triggers"
    state: TriggerState | None = None
    limit: int = Field(default=50, ge=1, le=100)


class PauseTriggerRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["pause_trigger"] = "pause_trigger"
    trigger_id: Annotated[NonEmpty, StringConstraints(max_length=128)]


class ResumeTriggerRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["resume_trigger"] = "resume_trigger"
    trigger_id: Annotated[NonEmpty, StringConstraints(max_length=128)]


class FireTriggerRequest(CoreModel):
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    method: Literal["fire_trigger"] = "fire_trigger"
    trigger_id: Annotated[NonEmpty, StringConstraints(max_length=128)]
    external_event_id: Annotated[NonEmpty, StringConstraints(max_length=256)]
    payload: dict[str, Any] = Field(default_factory=dict)


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
    name: Annotated[str, StringConstraints(max_length=160)] = ""
    caption: Annotated[str, StringConstraints(max_length=1000)] = ""


class DownloadArtifactRequest(SessionRequest):
    method: Literal["artifact_download"] = "artifact_download"
    artifact_id: Annotated[NonEmpty, StringConstraints(max_length=128)]


class TranscribeArtifactRequest(SessionRequest):
    method: Literal["artifact_transcribe"] = "artifact_transcribe"
    artifact_id: Annotated[NonEmpty, StringConstraints(max_length=128)]


CoreRequest: TypeAlias = Annotated[
    AuthenticateRequest
    | HealthRequest
    | CreateSessionRequest
    | GetConversationSessionRequest
    | ListConversationSessionsRequest
    | UpdateConversationSessionRequest
    | DeleteConversationSessionRequest
    | CreateTaskRequest
    | CreateChatTurnRequest
    | GetChatTurnRequest
    | ListChatTurnsRequest
    | SubscribeChatTurnRequest
    | CancelChatTurnRequest
    | RetryChatTurnRequest
    | ResolveChatApprovalRequest
    | ResolveHumanInteractionRequest
    | SubscribeTaskRequest
    | SubscribePrincipalTaskEventsRequest
    | UnsubscribeRequest
    | GetTaskRequest
    | ListTasksRequest
    | CreateProductTaskRequest
    | GetProductTaskRequest
    | ListProductTasksRequest
    | UpdateProductTaskRequest
    | SetProductTaskStateRequest
    | DeleteProductTaskRequest
    | ExecuteProductTaskRequest
    | GetProductTaskExecutionRequest
    | ListProductTaskExecutionsRequest
    | DeleteProductTaskExecutionRequest
    | RerunProductTaskExecutionRequest
    | ContinueProductTaskRequest
    | CancelTaskRequest
    | PauseTaskRequest
    | ResumeTaskRequest
    | ResolveApprovalRequest
    | CreateScheduleRequest
    | GetScheduleRequest
    | ListSchedulesRequest
    | PauseScheduleRequest
    | ResumeScheduleRequest
    | CreateTriggerRequest
    | GetTriggerRequest
    | ListTriggersRequest
    | PauseTriggerRequest
    | ResumeTriggerRequest
    | FireTriggerRequest
    | GetStatusRequest
    | GetHistoryRequest
    | ListMemoryRequest
    | ClearMemoryRequest
    | ListToolsRequest
    | SetConfigRequest
    | UploadArtifactRequest
    | DownloadArtifactRequest
    | TranscribeArtifactRequest,
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
    "chat_turn_not_found",
    "schedule_not_found",
    "trigger_not_found",
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


class ConversationSessionMessage(CoreModel):
    message_type: Literal["conversation_session"] = "conversation_session"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    session: ConversationSessionSnapshot


class ConversationSessionListMessage(CoreModel):
    message_type: Literal["conversation_session_list"] = "conversation_session_list"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    sessions: tuple[ConversationSessionSnapshot, ...]
    next_cursor: Annotated[str, StringConstraints(max_length=512)] = ""


class ConversationSessionDeletedMessage(CoreModel):
    message_type: Literal["conversation_session_deleted"] = "conversation_session_deleted"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    deleted: bool = True


class TaskAcceptedMessage(CoreModel):
    message_type: Literal["task_accepted"] = "task_accepted"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    task_id: TaskId
    state: TaskState


class ChatTurnAcceptedMessage(CoreModel):
    message_type: Literal["chat_turn_accepted"] = "chat_turn_accepted"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    turn: ChatTurnSnapshot


class ChatTurnSubscribedMessage(CoreModel):
    message_type: Literal["chat_turn_subscribed"] = "chat_turn_subscribed"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    turn_id: ChatTurnId


class ChatTurnSnapshotMessage(CoreModel):
    message_type: Literal["chat_turn_snapshot"] = "chat_turn_snapshot"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    turn: ChatTurnSnapshot


class ChatTurnListMessage(CoreModel):
    message_type: Literal["chat_turn_list"] = "chat_turn_list"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    turns: tuple[ChatTurnSnapshot, ...]
    next_cursor: Annotated[str, StringConstraints(max_length=512)] = ""


class ChatTurnSignalMessage(CoreModel):
    message_type: Literal["chat_turn_signal"] = "chat_turn_signal"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    turn: ChatTurnSnapshot


class ChatApprovalResolvedMessage(CoreModel):
    message_type: Literal["chat_approval_resolved"] = "chat_approval_resolved"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    approval: ChatApprovalSnapshot
    resolved: bool


class HumanInteractionResolvedMessage(CoreModel):
    message_type: Literal["interaction_resolved"] = "interaction_resolved"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    interaction: HumanInteractionSnapshot
    resolved: bool


class TaskSubscribedMessage(CoreModel):
    message_type: Literal["task_subscribed"] = "task_subscribed"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    task_id: TaskId
    after_seq: int = Field(ge=0)


class PrincipalTaskEventsSubscribedMessage(CoreModel):
    message_type: Literal["principal_task_events_subscribed"] = (
        "principal_task_events_subscribed"
    )
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    after_id: int = Field(ge=0)


class UnsubscribedMessage(CoreModel):
    message_type: Literal["unsubscribed"] = "unsubscribed"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    subscription_request_id: RequestId
    released: bool


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


class ProductTaskMessage(CoreModel):
    message_type: Literal["product_task"] = "product_task"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    task: ProductTaskSnapshot
    execution: ProductTaskExecutionSnapshot | None = None


class ProductTaskListMessage(CoreModel):
    message_type: Literal["product_task_list"] = "product_task_list"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    tasks: tuple[ProductTaskSnapshot, ...]


class ProductTaskExecutionMessage(CoreModel):
    message_type: Literal["product_task_execution"] = "product_task_execution"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    execution: ProductTaskExecutionSnapshot


class ProductTaskExecutionListMessage(CoreModel):
    message_type: Literal["product_task_execution_list"] = "product_task_execution_list"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    executions: tuple[ProductTaskExecutionSnapshot, ...]


class ProductTaskDeletedMessage(CoreModel):
    message_type: Literal["product_task_deleted"] = "product_task_deleted"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    deleted: bool = True
    task_id: str = ""
    execution_id: str = ""


class TaskEventMessage(CoreModel):
    message_type: Literal["task_event"] = "task_event"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    event: TaskEvent


class PrincipalTaskEventMessage(CoreModel):
    message_type: Literal["principal_task_event"] = "principal_task_event"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    feed_event: PrincipalTaskEvent


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


class ArtifactTranscribedMessage(CoreModel):
    message_type: Literal["artifact_transcribed"] = "artifact_transcribed"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    result: ArtifactTranscriptionResult


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


class TriggerAcceptedMessage(CoreModel):
    message_type: Literal["trigger_accepted"] = "trigger_accepted"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    trigger: TriggerSnapshot


class TriggerSnapshotMessage(CoreModel):
    message_type: Literal["trigger_snapshot"] = "trigger_snapshot"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    trigger: TriggerSnapshot


class TriggerListMessage(CoreModel):
    message_type: Literal["trigger_list"] = "trigger_list"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    triggers: tuple[TriggerSnapshot, ...]


class TriggerEventAcceptedMessage(CoreModel):
    message_type: Literal["trigger_event_accepted"] = "trigger_event_accepted"
    api_version: Literal["v1"] = "v1"
    request_id: RequestId
    event: TriggerEventSnapshot


CoreServerMessage: TypeAlias = Annotated[
    AuthenticatedMessage
    | SessionCreatedMessage
    | ConversationSessionMessage
    | ConversationSessionListMessage
    | ConversationSessionDeletedMessage
    | TaskAcceptedMessage
    | ChatTurnAcceptedMessage
    | ChatTurnSubscribedMessage
    | ChatTurnSnapshotMessage
    | ChatTurnListMessage
    | ChatTurnSignalMessage
    | ChatApprovalResolvedMessage
    | HumanInteractionResolvedMessage
    | TaskSubscribedMessage
    | PrincipalTaskEventsSubscribedMessage
    | UnsubscribedMessage
    | TaskSnapshotMessage
    | TaskListMessage
    | ProductTaskMessage
    | ProductTaskListMessage
    | ProductTaskExecutionMessage
    | ProductTaskExecutionListMessage
    | ProductTaskDeletedMessage
    | TaskEventMessage
    | PrincipalTaskEventMessage
    | HealthMessage
    | StatusMessage
    | HistoryMessage
    | MemoryListMessage
    | MemoryClearedMessage
    | ToolsMessage
    | ConfigSetMessage
    | ArtifactUploadedMessage
    | ArtifactDownloadedMessage
    | ArtifactTranscribedMessage
    | TaskCancelResultMessage
    | TaskPauseResultMessage
    | TaskResumedMessage
    | ApprovalResolvedMessage
    | ScheduleAcceptedMessage
    | ScheduleSnapshotMessage
    | ScheduleListMessage
    | TriggerAcceptedMessage
    | TriggerSnapshotMessage
    | TriggerListMessage
    | TriggerEventAcceptedMessage
    | CoreError,
    Field(discriminator="message_type"),
]
_CORE_SERVER_MESSAGE_ADAPTER = TypeAdapter(CoreServerMessage)


def parse_core_server_message_json(raw: str | bytes) -> CoreServerMessage:
    return _CORE_SERVER_MESSAGE_ADAPTER.validate_json(raw)


def core_request_schema() -> dict[str, Any]:
    """Expose the canonical JSON Schema for protocol/tooling generation."""
    return _CORE_REQUEST_ADAPTER.json_schema()
