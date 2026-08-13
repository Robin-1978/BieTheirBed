"""Fail-closed HTTP/TLS surface for Secure Gateway mobile access."""
from __future__ import annotations

import logging

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from knoa_platform.conversation import ConversationSessionState
from knoa_platform.gateway.protocol import (
    ChatTurnListQuery,
    ConversationSessionListQuery,
    CreateChatTurnRequest,
    CreateSessionRequest,
    ResolveApprovalRequest,
    UpdateConversationSessionRequest,
)

logger = logging.getLogger(__name__)
_MAX_BODY_BYTES = 16 * 1024



class ConversationRoutes:

    async def _create_session(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            body = (
                CreateSessionRequest()
                if request.headers.get("content-length") in {None, "", "0"}
                else await self._parse_body(request, CreateSessionRequest)
            )
            if isinstance(body, JSONResponse):
                return body
            handle = await self._core.create_session(
                authenticated.device.principal_id,
                agent_id=body.agent_id,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"session_handle": handle}, status_code=201)

    async def _list_conversation_sessions(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=60)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            query = ConversationSessionListQuery.model_validate(dict(request.query_params))
            sessions, next_cursor = await self._core.list_conversation_sessions(
                authenticated.device.principal_id,
                include_archived=query.include_archived,
                limit=query.limit,
                cursor=query.cursor,
            )
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({
            "sessions": [item.model_dump(mode="json") for item in sessions],
            "next_cursor": next_cursor,
        })

    async def _conversation_session(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        session_handle = self._path_identifier(request, "session_handle")
        if session_handle is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            if request.method == "GET":
                session = await self._core.get_conversation_session(
                    authenticated.device.principal_id,
                    session_handle,
                )
            elif request.method == "PATCH":
                parsed = await self._parse_body(request, UpdateConversationSessionRequest)
                if isinstance(parsed, JSONResponse):
                    return parsed
                session = await self._core.update_conversation_session(
                    authenticated.device.principal_id,
                    session_handle,
                    title=parsed.title,
                    state=(None if parsed.state is None else ConversationSessionState(parsed.state)),
                    expected_revision=parsed.expected_revision,
                )
            else:
                await self._core.delete_conversation_session(
                    authenticated.device.principal_id,
                    session_handle,
                )
                return JSONResponse({"deleted": True})
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"session": session.model_dump(mode="json")})

    async def _create_chat_turn(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=60)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        session_handle = self._path_identifier(request, "session_handle")
        if session_handle is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        parsed = await self._parse_body(request, CreateChatTurnRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            parsed.require_content()
            turn = await self._core.create_chat_turn(
                authenticated.device.principal_id,
                session_handle,
                parsed.input,
                parsed.attachments,
                client_request_id=parsed.client_request_id,
                tools_enabled=parsed.tools_enabled,
                agent_id=parsed.agent_id,
            )
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse(
            {"turn": turn.model_dump(mode="json")},
            status_code=202,
        )

    async def _get_chat_turn(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=120)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        turn_id = self._path_identifier(request, "turn_id")
        if turn_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            turn = await self._core.get_chat_turn(
                authenticated.device.principal_id,
                turn_id,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"turn": turn.model_dump(mode="json")})

    async def _list_chat_turns(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=120)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        session_handle = self._path_identifier(request, "session_handle")
        if session_handle is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            query = ChatTurnListQuery.model_validate(dict(request.query_params))
            turns, next_cursor = await self._core.list_chat_turns(
                authenticated.device.principal_id,
                session_handle,
                limit=query.limit,
                cursor=query.cursor,
            )
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse(
            {
                "turns": [turn.model_dump(mode="json") for turn in turns],
                "next_cursor": next_cursor,
            }
        )

    async def _cancel_chat_turn(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        turn_id = self._path_identifier(request, "turn_id")
        if turn_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            turn = await self._core.cancel_chat_turn(
                authenticated.device.principal_id,
                turn_id,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"turn": turn.model_dump(mode="json")})

    async def _retry_chat_turn(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        turn_id = self._path_identifier(request, "turn_id")
        if turn_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            turn = await self._core.retry_chat_turn(
                authenticated.device.principal_id,
                turn_id,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"turn": turn.model_dump(mode="json")}, status_code=202)

    async def _resolve_chat_approval(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        approval_id = self._path_identifier(request, "approval_id")
        if approval_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        parsed = await self._parse_body(request, ResolveApprovalRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            result = await self._core.resolve_chat_approval(
                authenticated.device.principal_id,
                approval_id,
                approved=parsed.approved,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse(
            {
                "approval": result.approval.model_dump(mode="json"),
                "resolved": result.resolved,
            }
        )
