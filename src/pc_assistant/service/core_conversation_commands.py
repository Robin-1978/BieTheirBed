"""Conversation and interactive-session Core command handlers."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pc_assistant.agent_runtime.contracts import (
    ArtifactAttachment,
    ConfigSetRequest,
    ControlServicePort,
    RuntimeScope,
)
from pc_assistant.conversation import ConversationService
from pc_assistant.service.core_api import (
    CancelChatTurnRequest,
    ChatApprovalResolvedMessage,
    ChatApprovalSnapshot,
    ChatTurnAcceptedMessage,
    ChatTurnListMessage,
    ChatTurnSnapshot,
    ChatTurnSnapshotMessage,
    ClearMemoryRequest,
    ConfigSetMessage,
    ConversationSessionDeletedMessage,
    ConversationSessionListMessage,
    ConversationSessionMessage,
    ConversationSessionSnapshot,
    CreateChatTurnRequest,
    CreateSessionRequest,
    DeleteConversationSessionRequest,
    GetChatTurnRequest,
    GetConversationSessionRequest,
    GetHistoryRequest,
    GetStatusRequest,
    HistoryMessage,
    ListChatTurnsRequest,
    ListConversationSessionsRequest,
    ListMemoryRequest,
    ListToolsRequest,
    MemoryClearedMessage,
    MemoryListMessage,
    ResolveChatApprovalRequest,
    RetryChatTurnRequest,
    SessionCreatedMessage,
    SetConfigRequest,
    StatusMessage,
    ToolsMessage,
    UpdateConversationSessionRequest,
)

Send = Callable[[Any], Awaitable[None]]


class ConversationCommandHandler:
    def __init__(
        self,
        control: ControlServicePort,
        conversations: ConversationService | None,
    ) -> None:
        self._control = control
        self._conversations = conversations

    def _conversation_service(self) -> ConversationService:
        if self._conversations is None:
            raise RuntimeError("Conversation service is unavailable")
        return self._conversations

    async def dispatch(self, principal: str, request: Any, send: Send) -> bool:
        if isinstance(request, CreateSessionRequest):
            if request.activate:
                scope = await self._control.create_session(principal)
            else:
                scope = await self._control.create_session(
                    principal,
                    activate=False,
                )
            await send(SessionCreatedMessage(
                request_id=request.request_id,
                session_handle=scope.session_handle,
            ))
        elif isinstance(request, GetConversationSessionRequest):
            session = await self._conversation_service().get_session(
                principal,
                request.session_handle,
            )
            await send(ConversationSessionMessage(
                request_id=request.request_id,
                session=ConversationSessionSnapshot.from_record(session),
            ))
        elif isinstance(request, ListConversationSessionsRequest):
            sessions, next_cursor = await self._conversation_service().list_sessions(
                principal,
                include_archived=request.include_archived,
                limit=request.limit,
                cursor=request.cursor,
            )
            await send(ConversationSessionListMessage(
                request_id=request.request_id,
                sessions=tuple(
                    ConversationSessionSnapshot.from_record(item)
                    for item in sessions
                ),
                next_cursor=next_cursor,
            ))
        elif isinstance(request, UpdateConversationSessionRequest):
            session = await self._conversation_service().update_session(
                principal,
                request.session_handle,
                title=request.title,
                state=request.state,
                expected_revision=request.expected_revision,
            )
            await send(ConversationSessionMessage(
                request_id=request.request_id,
                session=ConversationSessionSnapshot.from_record(session),
            ))
        elif isinstance(request, DeleteConversationSessionRequest):
            await self._conversation_service().delete_session(
                principal,
                request.session_handle,
            )
            await send(ConversationSessionDeletedMessage(request_id=request.request_id))
        elif isinstance(request, CreateChatTurnRequest):
            turn = await self._conversation_service().create_turn(
                RuntimeScope(
                    principal_id=principal,
                    session_handle=request.session_handle,
                ),
                client_request_id=request.client_request_id,
                user_input=request.input,
                attachments=tuple(
                    ArtifactAttachment(
                        artifact_id=item.artifact_id,
                        caption=item.caption,
                    )
                    for item in request.attachments
                ),
                tools_enabled=request.tools_enabled,
            )
            await send(ChatTurnAcceptedMessage(
                request_id=request.request_id,
                turn=ChatTurnSnapshot.from_record(turn),
            ))
        elif isinstance(request, GetChatTurnRequest):
            turn = await self._conversation_service().get_turn(
                principal,
                request.turn_id,
            )
            await send(ChatTurnSnapshotMessage(
                request_id=request.request_id,
                turn=ChatTurnSnapshot.from_record(turn),
            ))
        elif isinstance(request, ListChatTurnsRequest):
            turns, next_cursor = await self._conversation_service().list_turns(
                principal,
                request.session_handle,
                limit=request.limit,
                cursor=request.cursor,
            )
            await send(ChatTurnListMessage(
                request_id=request.request_id,
                turns=tuple(ChatTurnSnapshot.from_record(turn) for turn in turns),
                next_cursor=next_cursor,
            ))
        elif isinstance(request, CancelChatTurnRequest):
            turn = await self._conversation_service().cancel(
                principal,
                request.turn_id,
            )
            await send(ChatTurnSnapshotMessage(
                request_id=request.request_id,
                turn=ChatTurnSnapshot.from_record(turn),
            ))
        elif isinstance(request, RetryChatTurnRequest):
            turn = await self._conversation_service().retry_turn(
                principal,
                request.turn_id,
                client_request_id=request.request_id,
            )
            await send(ChatTurnAcceptedMessage(
                request_id=request.request_id,
                turn=ChatTurnSnapshot.from_record(turn),
            ))
        elif isinstance(request, ResolveChatApprovalRequest):
            approval, changed = await self._conversation_service().resolve_approval(
                principal,
                request.approval_id,
                approved=request.approved,
                resolved_by="core_api",
            )
            await send(ChatApprovalResolvedMessage(
                request_id=request.request_id,
                approval=ChatApprovalSnapshot.model_validate(approval.model_dump()),
                resolved=changed,
            ))
        elif isinstance(request, GetStatusRequest):
            scope = RuntimeScope(principal_id=principal, session_handle=request.session_handle)
            await send(StatusMessage(
                request_id=request.request_id,
                result=await self._control.get_status(scope),
            ))
        elif isinstance(request, GetHistoryRequest):
            scope = RuntimeScope(principal_id=principal, session_handle=request.session_handle)
            await send(HistoryMessage(
                request_id=request.request_id,
                result=await self._control.get_history(scope),
            ))
        elif isinstance(request, ListMemoryRequest):
            scope = RuntimeScope(principal_id=principal, session_handle=request.session_handle)
            await send(MemoryListMessage(
                request_id=request.request_id,
                result=await self._control.list_memory(scope),
            ))
        elif isinstance(request, ClearMemoryRequest):
            scope = RuntimeScope(principal_id=principal, session_handle=request.session_handle)
            await send(MemoryClearedMessage(
                request_id=request.request_id,
                result=await self._control.clear_memory(scope),
            ))
        elif isinstance(request, ListToolsRequest):
            scope = RuntimeScope(principal_id=principal, session_handle=request.session_handle)
            await send(ToolsMessage(
                request_id=request.request_id,
                result=await self._control.list_tools(scope),
            ))
        elif isinstance(request, SetConfigRequest):
            scope = RuntimeScope(principal_id=principal, session_handle=request.session_handle)
            result = await self._control.set_config(
                scope,
                ConfigSetRequest(field_name=request.field_name, value=request.value),
            )
            await send(ConfigSetMessage(request_id=request.request_id, result=result))
        else:
            return False
        return True
