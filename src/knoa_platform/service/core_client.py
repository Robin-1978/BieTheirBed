"""Thin Core API v1 WebSocket client for durable Tasks."""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol

import websockets

from knoa_platform.agent_runtime.contracts import (
    ConfigSetResult,
    HealthStatus,
    HistoryResult,
    MemoryClearResult,
    MemoryDeleteResult,
    MemoryListResult,
    MCPResourceCatalogResult,
    RuntimeStatus,
    ToolListResult,
)
from knoa_platform.conversation import TERMINAL_CHAT_TURN_STATES
from knoa_platform.agents.definitions import ResolvedInvocationPolicy
from knoa_platform.configuration import (
    ConfigControlState,
    ConfigDraft,
    ConfigPublishResult,
    ConfigRevision,
    ConfigValidationResult,
    ManagedConfig,
)
from knoa_platform.service.core_api import (
    CORE_WS_MAX_SIZE,
    ApprovalResolvedMessage,
    ArtifactInputRef,
    AuthenticatedMessage,
    AuthenticateRequest,
    CancelChatTurnRequest,
    CancelTaskRequest,
    ChatApprovalResolvedMessage,
    ChatTurnAcceptedMessage,
    ChatTurnListMessage,
    ChatTurnSignalMessage,
    ChatTurnSnapshot,
    ChatTurnSnapshotMessage,
    ChatTurnSubscribedMessage,
    ClearMemoryRequest,
    ConfigSetMessage,
    ConfigCurrentMessage,
    ConfigDiffMessage,
    ConfigDraftMessage,
    ConfigHistoryMessage,
    ConfigPublishedMessage,
    ConfigRevisionMessage,
    ConfigValidationMessage,
    ContinueProductTaskRequest,
    ConversationSessionDeletedMessage,
    ConversationSessionListMessage,
    ConversationSessionMessage,
    ConversationSessionSnapshot,
    CoreError,
    CoreServerMessage,
    CreateChatTurnRequest,
    CreateConfigDraftRequest,
    CreateProductTaskRequest,
    CreateSessionRequest,
    CreateTaskRequest,
    DeleteConversationSessionRequest,
    DeleteMemoryRequest,
    DeleteProductTaskExecutionRequest,
    DeleteProductTaskRequest,
    DeployMCPPackageRequest,
    ExecuteProductTaskRequest,
    GetChatTurnRequest,
    GetConversationSessionRequest,
    GetConfigCurrentRequest,
    GetConfigDiffRequest,
    GetConfigDraftRequest,
    GetConfigHistoryRequest,
    GetConfigRevisionRequest,
    GetHistoryRequest,
    GetProductTaskExecutionRequest,
    GetProductTaskRequest,
    PreflightProductTaskRequest,
    GetStatusRequest,
    GetTaskRequest,
    HealthMessage,
    HealthRequest,
    HistoryMessage,
    HumanInteractionResolvedMessage,
    ListChatTurnsRequest,
    ListConversationSessionsRequest,
    ListMemoryRequest,
    ListMCPResourcesRequest,
    ListProductTaskExecutionsRequest,
    ListNotificationIntentsRequest,
    ListProductTasksRequest,
    ListTasksRequest,
    ListToolsRequest,
    MCPPackageDeployedMessage,
    MCPPackageDeploymentSnapshot,
    MCPResourcesMessage,
    MemoryClearedMessage,
    MemoryDeletedMessage,
    MemoryListMessage,
    PauseTaskRequest,
    PreflightConfigDraftRequest,
    PreviewInvocationPolicyRequest,
    PrincipalTaskEventMessage,
    PrincipalTaskEventsSubscribedMessage,
    ProductTaskDeletedMessage,
    ProductTaskExecutionListMessage,
    ProductTaskExecutionMessage,
    ProductTaskExecutionSnapshot,
    ProductTaskListMessage,
    ProductTaskMessage,
    ProductTaskPreflightMessage,
    NotificationIntentListMessage,
    NotificationIntentMessage,
    MarkNotificationIntentProjectedRequest,
    ProductTaskSnapshot,
    PublishConfigDraftRequest,
    RerunProductTaskExecutionRequest,
    ResolveApprovalRequest,
    ResolveChatApprovalRequest,
    ResolveHumanInteractionRequest,
    ResumeTaskRequest,
    ReplaceConfigDraftRequest,
    RollbackConfigRequest,
    RetryChatTurnRequest,
    SessionCreatedMessage,
    SetConfigRequest,
    SetProductTaskStateRequest,
    StatusMessage,
    SubscribeChatTurnRequest,
    SubscribePrincipalTaskEventsRequest,
    SubscribeTaskRequest,
    TaskAcceptedMessage,
    TaskCancelResultMessage,
    TaskEventMessage,
    TaskListMessage,
    TaskPauseResultMessage,
    TaskResumedMessage,
    TaskSnapshot,
    TaskSnapshotMessage,
    TaskSubscribedMessage,
    ToolsMessage,
    UnsubscribedMessage,
    UnsubscribeRequest,
    UpdateConversationSessionRequest,
    UpdateProductTaskRequest,
    ValidateConfigDraftRequest,
    InvocationPolicyPreviewMessage,
    parse_core_server_message_json,
)
from knoa_platform.service.core_client_artifacts import CoreArtifactClientMixin
from knoa_platform.service.core_client_automation import CoreAutomationClientMixin
from knoa_platform.tasks import (
    PrincipalTaskEvent,
    TaskDefinitionState,
    TaskEvent,
    TaskLaunchPolicy,
    TaskLaunchReason,
    TaskOrigin,
    TaskState,
)


class ClientWebSocket(Protocol):
    async def send(self, message: str) -> None: ...
    async def close(self) -> None: ...
    def __aiter__(self): ...


class CoreConnectionLostError(ConnectionError):
    pass


class CoreTaskBufferOverflowError(CoreConnectionLostError):
    pass


class CoreRequestTimeoutError(TimeoutError):
    pass


class CoreRequestError(RuntimeError):
    def __init__(self, error: CoreError) -> None:
        self.code = error.code
        self.correlation_id = error.correlation_id
        super().__init__(error.message)


ApprovalHandler = Callable[[TaskEvent], Awaitable[bool]]


class CoreClient(CoreArtifactClientMixin, CoreAutomationClientMixin):
    def __init__(
        self,
        websocket: ClientWebSocket,
        *,
        approval_handler: ApprovalHandler | None = None,
        request_timeout_seconds: float = 60.0,
        max_buffered_task_events: int = 256,
    ) -> None:
        if max_buffered_task_events < 1:
            raise ValueError("Task event buffer limit must be at least one")
        self._websocket = websocket
        self._approval_handler = approval_handler
        self._request_timeout = max(0.01, request_timeout_seconds)
        self._max_buffered_task_events = max_buffered_task_events
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[CoreServerMessage]] = {}
        self._subscription_queues: dict[
            str,
            asyncio.Queue[TaskEvent | PrincipalTaskEvent | ChatTurnSnapshot | Exception],
        ] = {}
        self._active_tasks: list[str] = []
        self._send_lock = asyncio.Lock()
        self._connected = False

    @classmethod
    async def connect(
        cls,
        uri: str,
        credential: str,
        *,
        approval_handler: ApprovalHandler | None = None,
        request_timeout_seconds: float = 60.0,
        max_buffered_task_events: int = 256,
    ) -> CoreClient:
        websocket = await websockets.connect(uri, max_size=CORE_WS_MAX_SIZE)
        client = cls(
            websocket,
            approval_handler=approval_handler,
            request_timeout_seconds=request_timeout_seconds,
            max_buffered_task_events=max_buffered_task_events,
        )
        await client.start(credential)
        return client

    @staticmethod
    def _request_id() -> str:
        return uuid.uuid4().hex

    async def start(self, credential: str) -> None:
        if self._reader_task is not None:
            raise RuntimeError("CoreClient is already started")
        self._connected = True
        self._reader_task = asyncio.create_task(self._reader_loop())
        try:
            response = await self._request(
                AuthenticateRequest(
                    request_id=self._request_id(),
                    credential=credential,
                )
            )
            if not isinstance(response, AuthenticatedMessage):
                raise RuntimeError(
                    "CoreServer returned an invalid authentication response"
                )
        except BaseException:
            await self.disconnect()
            raise

    async def disconnect(self) -> None:
        self._connected = False
        try:
            await self._websocket.close()
        finally:
            if self._reader_task is not None:
                self._reader_task.cancel()
                try:
                    await self._reader_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._reader_task = None
            self._fail_all(CoreConnectionLostError("Core connection closed"))

    async def _send(self, request: Any) -> None:
        if not self._connected:
            raise CoreConnectionLostError("Core client is not connected")
        async with self._send_lock:
            await self._websocket.send(request.model_dump_json())

    async def _request(self, request: Any) -> CoreServerMessage:
        future: asyncio.Future[CoreServerMessage] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[request.request_id] = future
        try:
            try:
                await self._send(request)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.disconnect()
                raise CoreConnectionLostError(
                    f"Core request send failed: {request.method}"
                ) from exc
            try:
                response = await asyncio.wait_for(
                    future,
                    timeout=self._request_timeout,
                )
            except asyncio.TimeoutError as exc:
                await self.disconnect()
                raise CoreRequestTimeoutError(
                    f"Core request timed out: {request.method}"
                ) from exc
        finally:
            self._pending.pop(request.request_id, None)
        if isinstance(response, CoreError):
            raise CoreRequestError(response)
        return response

    async def _reader_loop(self) -> None:
        failure: Exception = CoreConnectionLostError("Core connection lost")
        try:
            async for raw in self._websocket:
                message = parse_core_server_message_json(raw)
                if isinstance(message, TaskEventMessage):
                    queue = self._subscription_queues.get(message.request_id)
                    if queue is not None:
                        try:
                            queue.put_nowait(message.event)
                        except asyncio.QueueFull:
                            failure = CoreTaskBufferOverflowError(
                                "Core Task event buffer overflow"
                            )
                            await self._websocket.close()
                            break
                    continue
                if isinstance(message, PrincipalTaskEventMessage):
                    queue = self._subscription_queues.get(message.request_id)
                    if queue is not None:
                        try:
                            queue.put_nowait(message.feed_event)
                        except asyncio.QueueFull:
                            failure = CoreTaskBufferOverflowError(
                                "Core principal Task event buffer overflow"
                            )
                            await self._websocket.close()
                            break
                    continue
                if isinstance(message, ChatTurnSignalMessage):
                    queue = self._subscription_queues.get(message.request_id)
                    if queue is not None:
                        if queue.full():
                            try:
                                queue.get_nowait()
                            except asyncio.QueueEmpty:
                                pass
                        queue.put_nowait(message.turn)
                    continue
                if isinstance(
                    message,
                    (
                        TaskSubscribedMessage,
                        PrincipalTaskEventsSubscribedMessage,
                        ChatTurnSubscribedMessage,
                    ),
                ):
                    future = self._pending.get(message.request_id)
                    if future is None or future.done():
                        failure = CoreConnectionLostError(
                            "Core protocol violation: unsolicited Task subscription"
                        )
                        await self._websocket.close()
                        break
                    self._subscription_queues.setdefault(
                        message.request_id,
                        asyncio.Queue(
                            maxsize=(
                                1
                                if isinstance(message, ChatTurnSubscribedMessage)
                                else self._max_buffered_task_events
                            )
                        ),
                    )
                if isinstance(message, CoreError):
                    queue = self._subscription_queues.get(message.request_id)
                    if queue is not None:
                        queue.put_nowait(CoreRequestError(message))
                        continue
                future = self._pending.get(message.request_id)
                if future is not None and not future.done():
                    future.set_result(message)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            failure = CoreConnectionLostError(
                f"Core connection lost: {type(exc).__name__}"
            )
        finally:
            self._connected = False
            self._fail_all(failure)

    def _fail_all(self, error: Exception) -> None:
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        for queue in tuple(self._subscription_queues.values()):
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(error)

    async def _unsubscribe(self, subscription_request_id: str) -> None:
        if not self._connected:
            return
        try:
            response = await self._request(
                UnsubscribeRequest(
                    request_id=self._request_id(),
                    subscription_request_id=subscription_request_id,
                )
            )
        except (CoreConnectionLostError, CoreRequestTimeoutError):
            return
        if not isinstance(response, UnsubscribedMessage):
            raise RuntimeError("CoreServer returned an invalid unsubscribe response")

    async def create_session(
        self,
        *,
        activate: bool = True,
        agent_id: str | None = None,
    ) -> str:
        response = await self._request(
            CreateSessionRequest(
                request_id=self._request_id(),
                activate=activate,
                agent_id=agent_id,
            )
        )
        if not isinstance(response, SessionCreatedMessage):
            raise RuntimeError("CoreServer returned an invalid session response")
        return response.session_handle

    async def get_conversation_session(self, session_handle: str) -> ConversationSessionSnapshot:
        response = await self._request(GetConversationSessionRequest(
            request_id=self._request_id(),
            session_handle=session_handle,
        ))
        if not isinstance(response, ConversationSessionMessage):
            raise RuntimeError("CoreServer returned an invalid ConversationSession response")
        return response.session

    async def list_conversation_sessions(
        self,
        *,
        include_archived: bool = False,
        limit: int = 100,
        cursor: str = "",
    ) -> tuple[tuple[ConversationSessionSnapshot, ...], str]:
        response = await self._request(ListConversationSessionsRequest(
            request_id=self._request_id(),
            include_archived=include_archived,
            limit=limit,
            cursor=cursor,
        ))
        if not isinstance(response, ConversationSessionListMessage):
            raise RuntimeError("CoreServer returned an invalid ConversationSession list")
        return response.sessions, response.next_cursor

    async def update_conversation_session(
        self,
        session_handle: str,
        *,
        title: str | None = None,
        state=None,
        expected_revision: int | None = None,
    ) -> ConversationSessionSnapshot:
        response = await self._request(UpdateConversationSessionRequest(
            request_id=self._request_id(),
            session_handle=session_handle,
            title=title,
            state=state,
            expected_revision=expected_revision,
        ))
        if not isinstance(response, ConversationSessionMessage):
            raise RuntimeError("CoreServer returned an invalid ConversationSession update")
        return response.session

    async def delete_conversation_session(self, session_handle: str) -> None:
        response = await self._request(DeleteConversationSessionRequest(
            request_id=self._request_id(),
            session_handle=session_handle,
        ))
        if not isinstance(response, ConversationSessionDeletedMessage):
            raise RuntimeError("CoreServer returned an invalid ConversationSession deletion")

    async def health(self) -> HealthStatus:
        response = await self._request(HealthRequest(request_id=self._request_id()))
        if not isinstance(response, HealthMessage):
            raise RuntimeError("CoreServer returned an invalid health response")
        return response.result

    async def status(self, session_handle: str) -> RuntimeStatus:
        response = await self._request(
            GetStatusRequest(
                request_id=self._request_id(),
                session_handle=session_handle,
            )
        )
        if not isinstance(response, StatusMessage):
            raise RuntimeError("CoreServer returned an invalid status response")
        return response.result

    async def history(self, session_handle: str) -> HistoryResult:
        response = await self._request(
            GetHistoryRequest(
                request_id=self._request_id(),
                session_handle=session_handle,
            )
        )
        if not isinstance(response, HistoryMessage):
            raise RuntimeError("CoreServer returned an invalid history response")
        return response.result

    async def list_memory(self, session_handle: str) -> MemoryListResult:
        response = await self._request(
            ListMemoryRequest(
                request_id=self._request_id(),
                session_handle=session_handle,
            )
        )
        if not isinstance(response, MemoryListMessage):
            raise RuntimeError("CoreServer returned an invalid memory response")
        return response.result

    async def clear_memory(self, session_handle: str) -> MemoryClearResult:
        response = await self._request(
            ClearMemoryRequest(
                request_id=self._request_id(),
                session_handle=session_handle,
            )
        )
        if not isinstance(response, MemoryClearedMessage):
            raise RuntimeError("CoreServer returned an invalid memory clear response")
        return response.result

    async def delete_memory(self, session_handle: str, key: str) -> MemoryDeleteResult:
        response = await self._request(
            DeleteMemoryRequest(
                request_id=self._request_id(),
                session_handle=session_handle,
                key=key,
            )
        )
        if not isinstance(response, MemoryDeletedMessage):
            raise RuntimeError("CoreServer returned an invalid memory delete response")
        return response.result

    async def list_tools(self, session_handle: str) -> ToolListResult:
        response = await self._request(
            ListToolsRequest(
                request_id=self._request_id(),
                session_handle=session_handle,
            )
        )
        if not isinstance(response, ToolsMessage):
            raise RuntimeError("CoreServer returned an invalid tools response")
        return response.result

    async def list_mcp_resources(self) -> MCPResourceCatalogResult:
        response = await self._request(
            ListMCPResourcesRequest(
                request_id=self._request_id(),
            )
        )
        if not isinstance(response, MCPResourcesMessage):
            raise RuntimeError("CoreServer returned an invalid MCP Resource response")
        return response.result

    async def set_config(
        self,
        session_handle: str,
        field_name: str,
        value: bool | float | str,
    ) -> ConfigSetResult:
        response = await self._request(
            SetConfigRequest(
                request_id=self._request_id(),
                session_handle=session_handle,
                field_name=field_name,
                value=value,
            )
        )
        if not isinstance(response, ConfigSetMessage):
            raise RuntimeError("CoreServer returned an invalid config response")
        return response.result

    async def get_config_current(
        self,
    ) -> tuple[ConfigRevision, ConfigControlState, tuple[dict[str, Any], ...]]:
        response = await self._request(
            GetConfigCurrentRequest(request_id=self._request_id())
        )
        if not isinstance(response, ConfigCurrentMessage):
            raise RuntimeError("CoreServer returned an invalid config current response")
        return response.revision, response.state, response.generations

    async def get_config_history(self, *, limit: int = 50) -> tuple[ConfigRevision, ...]:
        response = await self._request(
            GetConfigHistoryRequest(request_id=self._request_id(), limit=limit)
        )
        if not isinstance(response, ConfigHistoryMessage):
            raise RuntimeError("CoreServer returned an invalid config history response")
        return response.revisions

    async def get_config_revision(self, revision_id: str) -> ConfigRevision:
        response = await self._request(
            GetConfigRevisionRequest(
                request_id=self._request_id(),
                revision_id=revision_id,
            )
        )
        if not isinstance(response, ConfigRevisionMessage):
            raise RuntimeError("CoreServer returned an invalid config revision response")
        return response.revision

    async def create_config_draft(self) -> ConfigDraft:
        response = await self._request(
            CreateConfigDraftRequest(request_id=self._request_id())
        )
        if not isinstance(response, ConfigDraftMessage):
            raise RuntimeError("CoreServer returned an invalid config draft response")
        return response.draft

    async def get_config_draft(self, draft_id: str) -> ConfigDraft:
        response = await self._request(
            GetConfigDraftRequest(
                request_id=self._request_id(),
                draft_id=draft_id,
            )
        )
        if not isinstance(response, ConfigDraftMessage):
            raise RuntimeError("CoreServer returned an invalid config draft response")
        return response.draft

    async def replace_config_draft(
        self,
        draft_id: str,
        document: ManagedConfig,
        *,
        expected_version: int,
    ) -> ConfigDraft:
        response = await self._request(
            ReplaceConfigDraftRequest(
                request_id=self._request_id(),
                draft_id=draft_id,
                document=document,
                expected_version=expected_version,
            )
        )
        if not isinstance(response, ConfigDraftMessage):
            raise RuntimeError("CoreServer returned an invalid config draft response")
        return response.draft

    async def validate_config_draft(
        self,
        draft_id: str,
        *,
        preflight: bool = False,
    ) -> ConfigValidationResult:
        request_type = (
            PreflightConfigDraftRequest if preflight else ValidateConfigDraftRequest
        )
        response = await self._request(
            request_type(request_id=self._request_id(), draft_id=draft_id)
        )
        if not isinstance(response, ConfigValidationMessage):
            raise RuntimeError("CoreServer returned an invalid config validation response")
        return response.result

    async def publish_config_draft(
        self,
        draft_id: str,
        *,
        expected_version: int,
        summary: str = "",
    ) -> ConfigPublishResult:
        response = await self._request(
            PublishConfigDraftRequest(
                request_id=self._request_id(),
                draft_id=draft_id,
                expected_version=expected_version,
                summary=summary,
            )
        )
        if not isinstance(response, ConfigPublishedMessage):
            raise RuntimeError("CoreServer returned an invalid config publish response")
        return response.result

    async def rollback_config(
        self,
        revision_id: str,
        *,
        summary: str = "",
    ) -> ConfigPublishResult:
        response = await self._request(
            RollbackConfigRequest(
                request_id=self._request_id(),
                revision_id=revision_id,
                summary=summary,
            )
        )
        if not isinstance(response, ConfigPublishedMessage):
            raise RuntimeError("CoreServer returned an invalid config rollback response")
        return response.result

    async def get_config_diff(
        self,
        from_revision_id: str,
        to_revision_id: str,
    ) -> tuple[dict[str, Any], ...]:
        response = await self._request(
            GetConfigDiffRequest(
                request_id=self._request_id(),
                from_revision_id=from_revision_id,
                to_revision_id=to_revision_id,
            )
        )
        if not isinstance(response, ConfigDiffMessage):
            raise RuntimeError("CoreServer returned an invalid config diff response")
        return response.changes

    async def preview_invocation_policy(
        self,
        agent_id: str,
        *,
        invocation_kind: str = "user",
        caller_id: str = "",
        requested_tools: frozenset[str] | None = None,
        requested_skills: frozenset[str] | None = None,
    ) -> ResolvedInvocationPolicy:
        response = await self._request(
            PreviewInvocationPolicyRequest(
                request_id=self._request_id(),
                agent_id=agent_id,
                invocation_kind=invocation_kind,
                caller_id=caller_id,
                requested_tools=requested_tools,
                requested_skills=requested_skills,
            )
        )
        if not isinstance(response, InvocationPolicyPreviewMessage):
            raise RuntimeError("CoreServer returned an invalid policy preview response")
        return response.policy

    async def deploy_mcp_package(
        self,
        path: str,
        server_id: str,
        *,
        resource_uri: str = "",
        route_id: str = "events",
        session_handle: str = "",
        include_root: bool = False,
        tools_enabled: bool = True,
        priority: int = 0,
    ) -> MCPPackageDeploymentSnapshot:
        response = await self._request(
            DeployMCPPackageRequest(
                request_id=self._request_id(),
                path=path,
                server_id=server_id,
                resource_uri=resource_uri,
                route_id=route_id,
                session_handle=session_handle,
                include_root=include_root,
                tools_enabled=tools_enabled,
                priority=priority,
            )
        )
        if not isinstance(response, MCPPackageDeployedMessage):
            raise RuntimeError("CoreServer returned an invalid MCP deployment response")
        return response.deployment

    async def create_task(
        self,
        session_handle: str,
        user_input: str = "",
        attachments: tuple[ArtifactInputRef, ...] = (),
        *,
        tools_enabled: bool = True,
        priority: int = 0,
        parent_task_id: str = "",
        origin: TaskOrigin = TaskOrigin.USER,
        agent_id: str | None = None,
    ) -> TaskAcceptedMessage:
        response = await self._request(
            CreateTaskRequest(
                request_id=self._request_id(),
                session_handle=session_handle,
                input=user_input,
                attachments=attachments,
                tools_enabled=tools_enabled,
                priority=priority,
                parent_task_id=parent_task_id,
                origin=origin,
                agent_id=agent_id,
            )
        )
        if not isinstance(response, TaskAcceptedMessage):
            raise RuntimeError("CoreServer returned an invalid Task response")
        return response

    async def create_chat_turn(
        self,
        session_handle: str,
        user_input: str = "",
        attachments: tuple[ArtifactInputRef, ...] = (),
        *,
        client_request_id: str,
        tools_enabled: bool = True,
        agent_id: str | None = None,
    ) -> ChatTurnSnapshot:
        response = await self._request(
            CreateChatTurnRequest(
                request_id=self._request_id(),
                client_request_id=client_request_id,
                session_handle=session_handle,
                input=user_input,
                attachments=attachments,
                tools_enabled=tools_enabled,
                agent_id=agent_id,
            )
        )
        if not isinstance(response, ChatTurnAcceptedMessage):
            raise RuntimeError("CoreServer returned an invalid ChatTurn response")
        return response.turn

    async def get_chat_turn(self, turn_id: str) -> ChatTurnSnapshot:
        response = await self._request(
            GetChatTurnRequest(
                request_id=self._request_id(),
                turn_id=turn_id,
            )
        )
        if not isinstance(response, ChatTurnSnapshotMessage):
            raise RuntimeError("CoreServer returned an invalid ChatTurn snapshot")
        return response.turn

    async def list_chat_turns(
        self,
        session_handle: str,
        *,
        limit: int = 100,
        cursor: str = "",
    ) -> tuple[tuple[ChatTurnSnapshot, ...], str]:
        response = await self._request(
            ListChatTurnsRequest(
                request_id=self._request_id(),
                session_handle=session_handle,
                limit=limit,
                cursor=cursor,
            )
        )
        if not isinstance(response, ChatTurnListMessage):
            raise RuntimeError("CoreServer returned an invalid ChatTurn list")
        return response.turns, response.next_cursor

    async def chat_turn_updates(
        self,
        turn_id: str,
    ) -> AsyncIterator[ChatTurnSnapshot]:
        request_id = self._request_id()
        response = await self._request(
            SubscribeChatTurnRequest(
                request_id=request_id,
                turn_id=turn_id,
            )
        )
        if not isinstance(response, ChatTurnSubscribedMessage):
            raise RuntimeError("CoreServer returned an invalid ChatTurn subscription")
        queue = self._subscription_queues[request_id]
        try:
            while True:
                item = await queue.get()
                if isinstance(item, Exception):
                    raise item
                if not isinstance(item, ChatTurnSnapshot):
                    raise CoreConnectionLostError(
                        "Core protocol mixed ChatTurn subscription event types"
                    )
                yield item
                if item.state in TERMINAL_CHAT_TURN_STATES:
                    return
        finally:
            await self._unsubscribe(request_id)
            self._subscription_queues.pop(request_id, None)

    async def cancel_chat_turn(self, turn_id: str) -> ChatTurnSnapshot:
        response = await self._request(
            CancelChatTurnRequest(
                request_id=self._request_id(),
                turn_id=turn_id,
            )
        )
        if not isinstance(response, ChatTurnSnapshotMessage):
            raise RuntimeError("CoreServer returned an invalid ChatTurn cancel response")
        return response.turn

    async def retry_chat_turn(self, turn_id: str) -> ChatTurnSnapshot:
        response = await self._request(RetryChatTurnRequest(
            request_id=self._request_id(),
            turn_id=turn_id,
        ))
        if not isinstance(response, ChatTurnAcceptedMessage):
            raise RuntimeError("CoreServer returned an invalid ChatTurn retry response")
        return response.turn

    async def resolve_chat_approval(
        self,
        approval_id: str,
        *,
        approved: bool,
    ) -> ChatApprovalResolvedMessage:
        response = await self._request(
            ResolveChatApprovalRequest(
                request_id=self._request_id(),
                approval_id=approval_id,
                approved=approved,
            )
        )
        if not isinstance(response, ChatApprovalResolvedMessage):
            raise RuntimeError("CoreServer returned an invalid Chat approval response")
        return response

    async def resolve_interaction(
        self,
        interaction_id: str,
        value: Any,
    ) -> HumanInteractionResolvedMessage:
        response = await self._request(
            ResolveHumanInteractionRequest(
                request_id=self._request_id(),
                interaction_id=interaction_id,
                value=value,
            )
        )
        if not isinstance(response, HumanInteractionResolvedMessage):
            raise RuntimeError("CoreServer returned an invalid interaction response")
        return response

    async def task_events(
        self,
        task_id: str,
        *,
        after_seq: int = 0,
        handle_approvals: bool = True,
    ) -> AsyncIterator[TaskEvent]:
        request_id = self._request_id()
        response = await self._request(
            SubscribeTaskRequest(
                request_id=request_id,
                task_id=task_id,
                after_seq=after_seq,
            )
        )
        if not isinstance(response, TaskSubscribedMessage):
            raise RuntimeError("CoreServer returned an invalid Task subscription")
        queue = self._subscription_queues[request_id]
        self._active_tasks.append(task_id)
        try:
            while True:
                item = await queue.get()
                if isinstance(item, Exception):
                    raise item
                if not isinstance(item, TaskEvent):
                    raise CoreConnectionLostError(
                        "Core protocol mixed Task subscription event types"
                    )
                if (
                    item.event_type == "approval_requested"
                    and handle_approvals
                    and self._approval_handler is not None
                ):
                    approved = False
                    try:
                        approved = bool(await self._approval_handler(item))
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        approved = False
                    await self.resolve_approval(
                        item.payload.approval_id,
                        approved=approved,
                    )
                yield item
                if item.event_type in {"completed", "failed", "cancelled"}:
                    return
        finally:
            await self._unsubscribe(request_id)
            self._subscription_queues.pop(request_id, None)
            if task_id in self._active_tasks:
                self._active_tasks.remove(task_id)

    async def principal_task_events(
        self,
        *,
        after_id: int = 0,
    ) -> AsyncIterator[PrincipalTaskEvent]:
        request_id = self._request_id()
        response = await self._request(
            SubscribePrincipalTaskEventsRequest(
                request_id=request_id,
                after_id=after_id,
            )
        )
        if not isinstance(response, PrincipalTaskEventsSubscribedMessage):
            raise RuntimeError(
                "CoreServer returned an invalid principal Task event subscription"
            )
        queue = self._subscription_queues[request_id]
        try:
            while True:
                item = await queue.get()
                if isinstance(item, Exception):
                    raise item
                if not isinstance(item, PrincipalTaskEvent):
                    raise CoreConnectionLostError(
                        "Core protocol mixed Task subscription event types"
                    )
                yield item
        finally:
            await self._unsubscribe(request_id)
            self._subscription_queues.pop(request_id, None)

    async def get_task(self, task_id: str) -> TaskSnapshot:
        response = await self._request(
            GetTaskRequest(
                request_id=self._request_id(),
                task_id=task_id,
            )
        )
        if not isinstance(response, TaskSnapshotMessage):
            raise RuntimeError("CoreServer returned an invalid Task snapshot")
        return response.task

    async def list_tasks(
        self,
        *,
        session_handle: str = "",
        state: TaskState | None = None,
        origins: tuple[TaskOrigin, ...] = (),
        limit: int = 50,
        cursor: str = "",
    ) -> TaskListMessage:
        response = await self._request(
            ListTasksRequest(
                request_id=self._request_id(),
                session_handle=session_handle,
                state=state,
                origins=origins,
                limit=limit,
                cursor=cursor,
            )
        )
        if not isinstance(response, TaskListMessage):
            raise RuntimeError("CoreServer returned an invalid Task list")
        return response

    async def create_product_task(
        self,
        session_handle: str,
        goal: str,
        *,
        client_request_id: str,
        title: str = "",
        attachments: tuple[ArtifactInputRef, ...] = (),
        tools_enabled: bool = True,
        priority: int = 0,
        launch_policy: TaskLaunchPolicy | None = None,
        notification_policy: dict[str, bool] | None = None,
        agent_id: str | None = None,
        auto_launch: bool = True,
    ) -> ProductTaskMessage:
        response = await self._request(
            CreateProductTaskRequest(
                request_id=self._request_id(),
                client_request_id=client_request_id,
                session_handle=session_handle,
                title=title,
                goal=goal,
                attachments=attachments,
                tools_enabled=tools_enabled,
                priority=priority,
                launch_policy=launch_policy or TaskLaunchPolicy(),
                auto_launch=auto_launch,
                notification_policy=notification_policy or {},
                agent_id=agent_id,
            )
        )
        if not isinstance(response, ProductTaskMessage):
            raise RuntimeError("CoreServer returned an invalid product Task response")
        return response

    async def get_product_task(self, task_id: str) -> ProductTaskSnapshot:
        response = await self._request(
            GetProductTaskRequest(request_id=self._request_id(), task_id=task_id)
        )
        if not isinstance(response, ProductTaskMessage):
            raise RuntimeError("CoreServer returned an invalid product Task snapshot")
        return response.task

    async def preflight_product_task(self, task_id: str):
        response = await self._request(
            PreflightProductTaskRequest(
                request_id=self._request_id(),
                task_id=task_id,
            )
        )
        if not isinstance(response, ProductTaskPreflightMessage):
            raise RuntimeError("CoreServer returned an invalid Task preflight")
        return response.result

    async def list_notification_intents(
        self,
        *,
        after_sequence: int = 0,
        limit: int = 100,
        pending_only: bool = False,
    ):
        response = await self._request(ListNotificationIntentsRequest(
            request_id=self._request_id(),
            after_sequence=after_sequence,
            limit=limit,
            pending_only=pending_only,
        ))
        if not isinstance(response, NotificationIntentListMessage):
            raise RuntimeError("CoreServer returned invalid NotificationIntents")
        return response.intents

    async def mark_notification_intent_projected(self, intent_id: str):
        response = await self._request(MarkNotificationIntentProjectedRequest(
            request_id=self._request_id(),
            intent_id=intent_id,
        ))
        if not isinstance(response, NotificationIntentMessage):
            raise RuntimeError("CoreServer returned invalid NotificationIntent")
        return response.intent

    async def list_product_tasks(
        self,
        *,
        state: TaskDefinitionState | None = None,
        include_archived: bool = False,
        limit: int = 100,
    ) -> tuple[ProductTaskSnapshot, ...]:
        response = await self._request(
            ListProductTasksRequest(
                request_id=self._request_id(),
                state=state,
                include_archived=include_archived,
                limit=limit,
            )
        )
        if not isinstance(response, ProductTaskListMessage):
            raise RuntimeError("CoreServer returned an invalid product Task list")
        return response.tasks

    async def update_product_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        goal: str | None = None,
        attachments: tuple[ArtifactInputRef, ...] | None = None,
        tools_enabled: bool | None = None,
        priority: int | None = None,
        launch_policy: TaskLaunchPolicy | None = None,
        notification_policy: dict[str, bool] | None = None,
        expected_revision: int | None = None,
    ) -> ProductTaskSnapshot:
        response = await self._request(
            UpdateProductTaskRequest(
                request_id=self._request_id(),
                task_id=task_id,
                title=title,
                goal=goal,
                attachments=attachments,
                tools_enabled=tools_enabled,
                priority=priority,
                launch_policy=launch_policy,
                notification_policy=notification_policy,
                expected_revision=expected_revision,
            )
        )
        if not isinstance(response, ProductTaskMessage):
            raise RuntimeError("CoreServer returned an invalid updated product Task")
        return response.task

    async def set_product_task_state(
        self,
        task_id: str,
        state: TaskDefinitionState,
    ) -> ProductTaskSnapshot:
        response = await self._request(
            SetProductTaskStateRequest(
                request_id=self._request_id(),
                task_id=task_id,
                state=state,
            )
        )
        if not isinstance(response, ProductTaskMessage):
            raise RuntimeError("CoreServer returned an invalid product Task state")
        return response.task

    async def delete_product_task(self, task_id: str) -> None:
        response = await self._request(
            DeleteProductTaskRequest(
                request_id=self._request_id(),
                task_id=task_id,
            )
        )
        if not isinstance(response, ProductTaskDeletedMessage) or not response.deleted:
            raise RuntimeError("CoreServer returned an invalid product Task deletion")

    async def execute_product_task(
        self,
        task_id: str,
        *,
        launch_reason: TaskLaunchReason = TaskLaunchReason.MANUAL,
    ) -> ProductTaskExecutionSnapshot:
        response = await self._request(
            ExecuteProductTaskRequest(
                request_id=self._request_id(),
                task_id=task_id,
                launch_reason=launch_reason,
            )
        )
        if not isinstance(response, ProductTaskExecutionMessage):
            raise RuntimeError("CoreServer returned an invalid TaskExecution")
        return response.execution

    async def get_product_task_execution(
        self,
        execution_id: str,
    ) -> ProductTaskExecutionSnapshot:
        response = await self._request(
            GetProductTaskExecutionRequest(
                request_id=self._request_id(),
                execution_id=execution_id,
            )
        )
        if not isinstance(response, ProductTaskExecutionMessage):
            raise RuntimeError("CoreServer returned an invalid TaskExecution snapshot")
        return response.execution

    async def list_product_task_executions(
        self,
        task_id: str,
        *,
        limit: int = 100,
    ) -> tuple[ProductTaskExecutionSnapshot, ...]:
        response = await self._request(
            ListProductTaskExecutionsRequest(
                request_id=self._request_id(),
                task_id=task_id,
                limit=limit,
            )
        )
        if not isinstance(response, ProductTaskExecutionListMessage):
            raise RuntimeError("CoreServer returned an invalid TaskExecution list")
        return response.executions

    async def delete_product_task_execution(self, execution_id: str) -> None:
        response = await self._request(
            DeleteProductTaskExecutionRequest(
                request_id=self._request_id(),
                execution_id=execution_id,
            )
        )
        if not isinstance(response, ProductTaskDeletedMessage) or not response.deleted:
            raise RuntimeError("CoreServer returned an invalid TaskExecution deletion")

    async def rerun_product_task_execution(
        self,
        execution_id: str,
    ) -> ProductTaskExecutionSnapshot:
        response = await self._request(
            RerunProductTaskExecutionRequest(
                request_id=self._request_id(),
                execution_id=execution_id,
            )
        )
        if not isinstance(response, ProductTaskExecutionMessage):
            raise RuntimeError("CoreServer returned an invalid rerun TaskExecution")
        return response.execution

    async def continue_product_task(
        self,
        task_id: str,
        input: str = "",
        attachments: tuple[ArtifactInputRef, ...] = (),
        *,
        client_request_id: str,
    ) -> ProductTaskExecutionSnapshot:
        response = await self._request(
            ContinueProductTaskRequest(
                request_id=self._request_id(),
                client_request_id=client_request_id,
                task_id=task_id,
                input=input,
                attachments=attachments,
            )
        )
        if not isinstance(response, ProductTaskExecutionMessage):
            raise RuntimeError("CoreServer returned an invalid follow-up TaskExecution")
        return response.execution

    async def execute_task(
        self,
        session_handle: str,
        user_input: str = "",
        attachments: tuple[ArtifactInputRef, ...] = (),
        *,
        tools_enabled: bool = True,
        priority: int = 0,
    ) -> AsyncIterator[TaskEvent]:
        accepted = await self.create_task(
            session_handle,
            user_input,
            attachments,
            tools_enabled=tools_enabled,
            priority=priority,
        )
        async for event in self.task_events(accepted.task_id):
            yield event

    async def cancel_task(
        self,
        task_id: str,
        *,
        reason: str = "",
    ) -> TaskCancelResultMessage:
        response = await self._request(
            CancelTaskRequest(
                request_id=self._request_id(),
                task_id=task_id,
                reason=reason,
            )
        )
        if not isinstance(response, TaskCancelResultMessage):
            raise RuntimeError("CoreServer returned an invalid Task cancel response")
        return response

    async def cancel_active_task(self) -> TaskCancelResultMessage | None:
        if not self._active_tasks:
            return None
        return await self.cancel_task(self._active_tasks[-1])

    async def pause_task(self, task_id: str, *, reason: str = "") -> TaskPauseResultMessage:
        response = await self._request(
            PauseTaskRequest(
                request_id=self._request_id(),
                task_id=task_id,
                reason=reason,
            )
        )
        if not isinstance(response, TaskPauseResultMessage):
            raise RuntimeError("CoreServer returned an invalid Task pause response")
        return response

    async def resume_task(
        self,
        task_id: str,
        *,
        reason: str = "",
        acknowledge_outcome_unknown: bool = False,
    ) -> TaskResumedMessage:
        response = await self._request(
            ResumeTaskRequest(
                request_id=self._request_id(),
                task_id=task_id,
                reason=reason,
                acknowledge_outcome_unknown=acknowledge_outcome_unknown,
            )
        )
        if not isinstance(response, TaskResumedMessage):
            raise RuntimeError("CoreServer returned an invalid Task resume response")
        return response

    async def resolve_approval(
        self,
        approval_id: str,
        *,
        approved: bool,
    ) -> ApprovalResolvedMessage:
        response = await self._request(
            ResolveApprovalRequest(
                request_id=self._request_id(),
                approval_id=approval_id,
                approved=approved,
            )
        )
        if not isinstance(response, ApprovalResolvedMessage):
            raise RuntimeError("CoreServer returned an invalid approval response")
        return response

    @property
    def is_connected(self) -> bool:
        return self._connected

    def set_approval_handler(self, handler: ApprovalHandler | None) -> None:
        self._approval_handler = handler
